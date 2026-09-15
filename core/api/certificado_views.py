from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Case, Q, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response

from core.models import Certificado, EstadoCertificado, PlantillaCertificado, TipoCertificado
from core.services import certificados
from .certificado_serializers import (
    ActivarPlantillaSerializer, CertificadoDetalleSerializer, CertificadoGestionSerializer,
    CertificadoSerializer, EmitirCertificadoSerializer, EnvioCertificadoSerializer,
    HistorialCertificadoSerializer, MotivoCertificadoSerializer,
    PlantillaCertificadoSerializer, TipoCertificadoSerializer,
    VerificacionCertificadoSerializer,
)
from .serializers import permisos_de
from .throttles import VerificarCertificadoRateThrottle


def permisos(request):
    if not hasattr(request, '_permisos_certificados'):
        request._permisos_certificados = permisos_de(request.user)
    return request._permisos_certificados


def es_gestor(request):
    return 'aspirantes:consultar' in permisos(request)


class PermisoCertificado(BasePermission):
    def has_permission(self, request, view):
        requeridos = {
            'create': {'certificados:generar', 'certificados:generar-manual'},
            'descargar': {'certificados:descargar'},
            'cancelar': {'certificados:cancelar'}, 'revocar': {'certificados:revocar'},
            'enviar': {'certificados:enviar'},
            'historial': {'certificados:historial'}, 'envios': {'certificados:historial'},
        }.get(view.action, {'certificados:consultar'})
        return requeridos <= permisos(request) and (
            view.action not in ('create', 'cancelar', 'revocar', 'enviar', 'historial', 'envios')
            or es_gestor(request)
        )


class PaginaCertificados(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class RespuestaPrivada:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class CertificadoViewSet(RespuestaPrivada, mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PermisoCertificado]
    pagination_class = PaginaCertificados
    serializer_class = CertificadoSerializer

    @swagger_auto_schema(manual_parameters=[
        openapi.Parameter(nombre, openapi.IN_QUERY, type=openapi.TYPE_STRING, description=descripcion)
        for nombre, descripcion in (
            ('estado', 'en_proceso, emitido, enviado, reenviado, cancelado o revocado'),
            ('tipo', 'Clave del tipo de certificado'), ('aspirante', 'ID del aspirante'),
            ('proceso', 'ID de convocatoria'), ('postulacion', 'ID numérico de postulación'),
            ('search', 'Busca por folio, nombre o folio de postulación'),
        )
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.action == 'create':
            return EmitirCertificadoSerializer
        if self.action == 'retrieve':
            return CertificadoGestionSerializer if es_gestor(self.request) else CertificadoDetalleSerializer
        return CertificadoSerializer

    def get_queryset(self):
        qs = Certificado.objects.defer('archivo_pdf').annotate(
            pdf_disponible=Case(When(archivo_pdf__isnull=False, then=Value(True)),
                                default=Value(False), output_field=BooleanField()),
        ).order_by('-creado_en', '-id')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        if not es_gestor(self.request):
            qs = qs.filter(aspirante__usuario=self.request.user)
        # Los filtros sólo afectan a la lista, nunca a las acciones de un ID.
        if self.action != 'list':
            return qs
        params = self.request.query_params
        estado = params.get('estado')
        if estado:
            if estado not in EstadoCertificado.values:
                raise ValidationError({'estado': 'Estado de certificado inválido.'})
            qs = qs.filter(estado=estado)
        for campo in ('tipo', 'aspirante', 'proceso'):
            if params.get(campo):
                qs = qs.filter(**{campo + '_id': params[campo]})
        if params.get('postulacion'):
            try:
                valor = int(params['postulacion'])
                if not 0 < valor <= 9223372036854775807:
                    raise ValueError
            except ValueError:
                raise ValidationError({'postulacion': 'Debe ser un entero positivo válido.'})
            qs = qs.filter(postulacion_id=valor)
        if params.get('search'):
            texto = params['search'][:200]
            qs = qs.filter(Q(folio__icontains=texto) | Q(aspirante_snapshot__generales__nombre_completo__icontains=texto)
                           | Q(aspirante_snapshot__postulacion__folio__icontains=texto))
        return qs

    def respuesta_certificado(self, certificado, status=200):
        certificado = self.get_queryset().get(pk=certificado.pk)
        return Response(CertificadoGestionSerializer(certificado, context={'request': self.request}).data, status=status)

    @swagger_auto_schema(request_body=EmitirCertificadoSerializer, responses={201: CertificadoGestionSerializer})
    def create(self, request, *args, **kwargs):
        entrada = EmitirCertificadoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        try:
            certificado = certificados.emitir(entrada.validated_data, request.user)
        except IntegrityError:
            raise certificados.ConflictoCertificado('Existe un conflicto con otro certificado. Actualiza la lista.')
        return self.respuesta_certificado(certificado, 201)

    @swagger_auto_schema(responses={200: openapi.Response('PDF privado', schema=openapi.Schema(type=openapi.TYPE_FILE))})
    @action(detail=True, methods=['get'])
    def descargar(self, request, pk=None):
        certificado = self.get_object()
        if certificado.estado not in ('emitido', 'enviado', 'reenviado') or not certificado.pdf_disponible:
            raise certificados.ConflictoCertificado('El certificado no tiene un PDF vigente disponible.')
        respuesta = HttpResponse(bytes(certificado.archivo_pdf), content_type='application/pdf')
        respuesta['Content-Disposition'] = f'attachment; filename="{certificado.folio}.pdf"'
        respuesta['X-Content-Type-Options'] = 'nosniff'
        return respuesta

    @transaction.atomic
    def transicion(self, request, accion):
        entrada = MotivoCertificadoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        certificado = get_object_or_404(self.get_queryset().select_for_update(), pk=self.kwargs['pk'])
        certificados.cambiar_estado(certificado, request.user, accion, entrada.validated_data['motivo'])
        return self.respuesta_certificado(certificado)

    @swagger_auto_schema(request_body=MotivoCertificadoSerializer, responses={200: CertificadoGestionSerializer})
    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        return self.transicion(request, 'cancelar')

    @swagger_auto_schema(request_body=MotivoCertificadoSerializer, responses={200: CertificadoGestionSerializer})
    @action(detail=True, methods=['post'])
    def revocar(self, request, pk=None):
        return self.transicion(request, 'revocar')

    @swagger_auto_schema(request_body=no_body, responses={200: EnvioCertificadoSerializer, 502: EnvioCertificadoSerializer})
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def enviar(self, request, pk=None):
        certificado = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        envio = certificados.enviar(certificado, request.user)
        return Response(EnvioCertificadoSerializer(envio).data, status=502 if envio.estado == 'fallido' else 200)

    @swagger_auto_schema(responses={200: HistorialCertificadoSerializer(many=True)})
    @action(detail=True, methods=['get'])
    def historial(self, request, pk=None):
        return Response(HistorialCertificadoSerializer(self.get_object().historial.order_by('creado_en', 'id'), many=True).data)

    @swagger_auto_schema(responses={200: EnvioCertificadoSerializer(many=True)})
    @action(detail=True, methods=['get'])
    def envios(self, request, pk=None):
        return Response(EnvioCertificadoSerializer(self.get_object().envios.order_by('-creado_en', 'id'), many=True).data)


@swagger_auto_schema(
    method='get',
    responses={
        200: VerificacionCertificadoSerializer,
        404: 'No existe un certificado con ese folio o codigo.',
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([VerificarCertificadoRateThrottle])
def verificar_certificado(request, codigo):
    """Verificacion publica. Es a donde apunta el QR impreso en el documento.

    El unico endpoint del modulo sin sesion, y tiene que serlo: quien recibe
    un certificado —otra institucion, una empresa— no tiene cuenta aqui y aun
    asi necesita comprobar que el documento es autentico.

    Un certificado cancelado o revocado se responde igual, con su estado, en
    vez de darlo por inexistente: callarlo dejaria pasar por bueno un
    documento retirado, que es justo lo que esta pagina debe impedir.

    Se acepta el folio ademas del codigo porque es lo que se lee a simple
    vista en el papel cuando el QR no se puede escanear.
    """
    clave = (codigo or '').strip()[:64]
    certificado = Certificado.objects.defer('archivo_pdf').select_related('tipo').filter(
        Q(codigo_verificacion__iexact=clave) | Q(folio__iexact=clave),
    ).first()
    if not certificado:
        raise NotFound('No existe un certificado con ese folio o codigo.')
    respuesta = Response(VerificacionCertificadoSerializer(certificado).data)
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta


class PermisoCatalogoCertificado(BasePermission):
    def has_permission(self, request, view):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return 'certificados:consultar' in permisos(request)
        return 'certificados:plantillas' in permisos(request)


class TipoCertificadoViewSet(RespuestaPrivada, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PermisoCatalogoCertificado]
    serializer_class = TipoCertificadoSerializer
    queryset = TipoCertificado.objects.order_by('clave')


class PlantillaCertificadoViewSet(RespuestaPrivada, mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, PermisoCatalogoCertificado]
    serializer_class = PlantillaCertificadoSerializer
    queryset = PlantillaCertificado.objects.select_related('tipo').order_by('id', 'version')

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('tipo'):
            qs = qs.filter(tipo_id=self.request.query_params['tipo'])
        return qs

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                serializer.save(creado_por=self.request.user, creado_en=timezone.now(), configuracion={})
        except IntegrityError:
            raise certificados.ConflictoCertificado('Ya existe esta versión de plantilla.')

    @swagger_auto_schema(method='get', responses={200: PlantillaCertificadoSerializer})
    @swagger_auto_schema(method='patch', request_body=ActivarPlantillaSerializer, responses={200: PlantillaCertificadoSerializer})
    @action(detail=False, methods=['get', 'patch'], url_path=r'(?P<plantilla_id>[^/.]+)/versiones/(?P<version>[^/]+)')
    def version(self, request, plantilla_id=None, version=None):
        plantilla = get_object_or_404(self.get_queryset(), id=plantilla_id, version=version)
        if request.method == 'PATCH':
            entrada = ActivarPlantillaSerializer(data=request.data)
            entrada.is_valid(raise_exception=True)
            plantilla.activa = entrada.validated_data['activa']
            plantilla.save(update_fields=['activa'])
        return Response(self.get_serializer(plantilla).data)
