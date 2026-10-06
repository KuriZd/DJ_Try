from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import Curso, Modulo, Leccion, Inscripcion, ProgresoLeccion, CertificadoCurso, Video
from core.services import portadas_curso
from core.services.cursos import recalcular_curso, recalcular_inscripcion
from .curso_permissions import PuedeGestionarCursos, administra_cursos, gestiona_curso, permisos_cursos
from .curso_serializers import (
    CursoSerializer, CursoDetalleSerializer, ModuloSerializer, LeccionSerializer,
    InscripcionSerializer, InscribirSerializer, ProgresoSerializer,
    RegistrarProgresoSerializer, CertificadoCursoSerializer, ResultadoProgresoSerializer,
    PedirPortadaSerializer, ConfirmarPortadaSerializer,
)


class PortadaNoDisponible(APIException):
    status_code = 503
    default_detail = 'El almacenamiento de imagenes no esta disponible. Intenta de nuevo.'


UUID_REGEX = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
SLUG_REGEX = '[-a-zA-Z0-9_]+'


class APIPrivada:
    permission_classes = [IsAuthenticated]
    lookup_value_regex = UUID_REGEX

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


def filtro_gestion(request, prefijo=''):
    if administra_cursos(request):
        return Q(pk__isnull=False)
    if 'cursos:crear' in permisos_cursos(request):
        return Q(**{prefijo + 'instructor': request.user})
    return Q(pk__in=[])


def exigir_inscripcion(request, curso):
    if not request.user.is_authenticated or not curso.activo or not Inscripcion.objects.filter(
        curso=curso, usuario=request.user,
    ).exists():
        raise PermissionDenied('Necesitas estar inscrito en un curso activo para acceder al contenido.')


def comprobar_orden(modelo, serializer, **ambito):
    """El orden es unico dentro de su ambito: el modulo en el curso, la leccion
    en el modulo. Se comprueba aqui y no con un validador del serializer porque
    tiene que correr bajo el bloqueo del curso, tambien en PATCH."""
    orden = serializer.validated_data.get('orden', serializer.instance.orden if serializer.instance else None)
    qs = modelo.objects.filter(orden=orden, **ambito)
    if serializer.instance:
        qs = qs.exclude(pk=serializer.instance.pk)
    if qs.exists():
        raise ValidationError({'orden': 'Ya existe un registro con ese orden.'})


@transaction.atomic
def inscribir_usuario(request, **filtros):
    curso = get_object_or_404(Curso.objects.select_for_update(), activo=True, **filtros)
    inscripcion, creada = Inscripcion.objects.get_or_create(curso=curso, usuario=request.user)
    recalcular_inscripcion(inscripcion)
    return Response(InscripcionSerializer(inscripcion).data, status=201 if creada else 200)


class CursoViewSet(APIPrivada, viewsets.ModelViewSet):
    """Catalogo y ficha del curso.

    Listar y consultar son publicos: el catalogo cerrado no invita a
    registrarse, y la ficha no entrega nada que valga por si solo -- el
    temario dice que hay dentro, no lo entrega. El archivo de video sigue
    detras de `videos/{id}/playback/`, que comprueba la inscripcion.
    """

    serializer_class = CursoSerializer
    permission_classes = [IsAuthenticated, PuedeGestionarCursos]
    lookup_field = 'slug'
    lookup_value_regex = SLUG_REGEX

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [AllowAny()]
        return super().get_permissions()

    def get_serializer_class(self):
        return CursoDetalleSerializer if self.action == 'retrieve' else CursoSerializer

    def get_queryset(self):
        qs = Curso.objects.select_related('instructor').prefetch_related('modulos__lecciones')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        if self.action in ('update', 'partial_update', 'destroy', 'portada', 'confirmar_portada'):
            return qs.filter(filtro_gestion(self.request)).distinct()
        return qs.filter(Q(activo=True) | filtro_gestion(self.request)).distinct()

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        get_object_or_404(Curso.objects.select_for_update(), slug=kwargs['slug'])
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        curso = get_object_or_404(Curso.objects.select_for_update(), slug=kwargs['slug'])
        clave = curso.imagen_clave
        respuesta = super().destroy(request, *args, **kwargs)
        # La caratula no se queda huerfana en S3. Despues del commit: si el
        # borrado se revierte, el archivo sigue haciendo falta.
        transaction.on_commit(lambda: portadas_curso.borrar([clave]))
        return respuesta

    @swagger_auto_schema(method='post', request_body=PedirPortadaSerializer)
    @swagger_auto_schema(method='delete', responses={200: CursoSerializer})
    @action(detail=True, methods=['post', 'delete'], url_path='portada')
    def portada(self, request, slug=None):
        """Caratula del curso.

            POST   cursos/{slug}/portada/            { content_type, tamano } -> URL de subida
            PUT    {upload_url}                      el navegador, directo a S3
            POST   cursos/{slug}/portada/confirmar/  { clave } comprueba el archivo y la fija
            DELETE cursos/{slug}/portada/            la quita

        Pedir la subida no cambia el curso: una carga que nunca se confirma
        no le deja una caratula rota a nadie.
        """
        if request.method == 'DELETE':
            return self._quitar_portada(request)

        curso = self.get_object()
        datos = PedirPortadaSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        content_type = datos.validated_data['content_type']
        clave = portadas_curso.clave_nueva(curso, content_type)
        try:
            url, headers = portadas_curso.url_de_subida(clave, content_type)
        except portadas_curso.ERRORES_S3 as error:
            raise PortadaNoDisponible() from error
        return Response({
            'clave': clave, 'upload_url': url, 'method': 'PUT', 'headers': headers,
            'expires_in': settings.VIDEO_UPLOAD_URL_TTL,
        }, status=201)

    @swagger_auto_schema(request_body=ConfirmarPortadaSerializer, responses={200: CursoSerializer})
    @action(detail=True, methods=['post'], url_path='portada/confirmar')
    def confirmar_portada(self, request, slug=None):
        datos = ConfirmarPortadaSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        clave = datos.validated_data['clave']

        with transaction.atomic():
            curso = get_object_or_404(Curso.objects.select_for_update(), pk=self.get_object().pk)
            content_type = portadas_curso.tipo_de_clave(curso, clave)
            if content_type is None:
                raise ValidationError({'clave': 'No es una caratula de este curso.'})
            try:
                tamano, guardado, inicio = portadas_curso.inspeccionar(clave)
            except ClientError as error:
                if error.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                    return Response({'detail': 'La imagen todavia no termina de subirse.'}, status=409)
                raise PortadaNoDisponible() from error
            except portadas_curso.ERRORES_S3 as error:
                raise PortadaNoDisponible() from error

            if not portadas_curso.es_valida(content_type, tamano, guardado, inicio):
                # Lo rechazado no se queda en S3.
                transaction.on_commit(lambda: portadas_curso.borrar_si_retirada(curso.pk, clave))
                limite = settings.CURSO_PORTADA_MAX_BYTES // (1024 * 1024)
                return Response({
                    'detail': f'El archivo no es una imagen JPG, PNG o WebP valida, o pasa de {limite} MB.',
                }, status=400)

            anterior = curso.imagen_clave
            curso.imagen_clave, curso.imagen_tipo, curso.imagen = clave, content_type, ''
            curso.save(update_fields=['imagen_clave', 'imagen_tipo', 'imagen'])
            if anterior and anterior != clave:
                transaction.on_commit(lambda: portadas_curso.borrar_si_retirada(curso.pk, anterior))

        return Response(CursoSerializer(curso, context=self.get_serializer_context()).data)

    def _quitar_portada(self, request):
        with transaction.atomic():
            curso = get_object_or_404(Curso.objects.select_for_update(), pk=self.get_object().pk)
            anterior = curso.imagen_clave
            curso.imagen_clave, curso.imagen_tipo, curso.imagen = '', '', ''
            curso.save(update_fields=['imagen_clave', 'imagen_tipo', 'imagen'])
            transaction.on_commit(lambda: portadas_curso.borrar_si_retirada(curso.pk, anterior))
        return Response(CursoSerializer(curso, context=self.get_serializer_context()).data)

    @swagger_auto_schema(request_body=no_body, responses={200: InscripcionSerializer, 201: InscripcionSerializer})
    @action(detail=True, methods=['post'])
    def inscribir(self, request, slug=None):
        return inscribir_usuario(request, slug=slug)

    @swagger_auto_schema(responses={200: ModuloSerializer(many=True)})
    @action(detail=True, methods=['get'])
    def lecciones(self, request, slug=None):
        """Temario con el detalle que solo ve quien entro al curso.

        La ficha publica ya trae los modulos; esto entrega ademas las
        lecciones desactivadas a quien administra el curso, y por eso exige
        inscripcion o permiso de gestion.
        """
        curso = self.get_object()
        completo = gestiona_curso(request, curso)
        if not completo:
            exigir_inscripcion(request, curso)
        contexto = {**self.get_serializer_context(), 'temario_completo': completo}
        return Response(ModuloSerializer(curso.modulos.all(), many=True, context=contexto).data)


class ModuloViewSet(APIPrivada, viewsets.ModelViewSet):
    serializer_class = ModuloSerializer
    permission_classes = [IsAuthenticated, PuedeGestionarCursos]

    def get_queryset(self):
        qs = Modulo.objects.select_related('curso').prefetch_related('lecciones')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        qs = qs.filter(Q(curso__activo=True) | filtro_gestion(self.request, 'curso__')).distinct()
        if curso_id := self.request.query_params.get('curso'):
            from rest_framework.fields import UUIDField
            qs = qs.filter(curso_id=UUIDField().run_validation(curso_id))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=serializer.validated_data['curso'].pk)
        if not gestiona_curso(self.request, curso):
            raise PermissionDenied('No puedes administrar este curso.')
        comprobar_orden(Modulo, serializer, curso=curso)
        serializer.save(curso=curso)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        modulo = self.get_object()
        get_object_or_404(Curso.objects.select_for_update(), pk=modulo.curso_id)
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        comprobar_orden(Modulo, serializer, curso=serializer.instance.curso)
        serializer.save()

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        modulo = self.get_object()
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=modulo.curso_id)
        response = super().destroy(request, *args, **kwargs)
        # Se lleva sus lecciones por cascada: el avance de quien ya las vio
        # deja de contar y el porcentaje tiene que reflejarlo.
        recalcular_curso(curso)
        return response


class LeccionViewSet(APIPrivada, viewsets.ModelViewSet):
    serializer_class = LeccionSerializer
    permission_classes = [IsAuthenticated, PuedeGestionarCursos]

    def get_queryset(self):
        qs = Leccion.objects.select_related('modulo__curso', 'video')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        acceso = Q(activo=True, modulo__curso__activo=True,
                   modulo__curso__inscripciones__usuario=self.request.user)
        qs = qs.filter(acceso | filtro_gestion(self.request, 'modulo__curso__')).distinct()
        from rest_framework.fields import UUIDField
        if curso_id := self.request.query_params.get('curso'):
            qs = qs.filter(modulo__curso_id=UUIDField().run_validation(curso_id))
        if modulo_id := self.request.query_params.get('modulo'):
            qs = qs.filter(modulo_id=UUIDField().run_validation(modulo_id))
        return qs

    def bloquear_video(self, serializer):
        # Comparte bloqueo con DELETE /videos/: no se puede vincular un video
        # que haya sido eliminado entre la validacion y la escritura.
        video = serializer.validated_data.get('video', serializer.instance.video if serializer.instance else None)
        video = Video.objects.select_for_update().get(pk=video.pk)
        if video.eliminado_en is not None:
            raise ValidationError({'video': 'El video fue eliminado.'})
        serializer.validated_data['video'] = video

    @transaction.atomic
    def perform_create(self, serializer):
        modulo = serializer.validated_data['modulo']
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=modulo.curso_id)
        if not gestiona_curso(self.request, curso):
            raise PermissionDenied('No puedes administrar este curso.')
        comprobar_orden(Leccion, serializer, modulo=modulo)
        self.bloquear_video(serializer)
        serializer.save(modulo=modulo)
        recalcular_curso(curso)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        leccion = self.get_object()
        get_object_or_404(Curso.objects.select_for_update(), pk=leccion.modulo.curso_id)
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        comprobar_orden(Leccion, serializer,
                        modulo=serializer.validated_data.get('modulo', serializer.instance.modulo))
        self.bloquear_video(serializer)
        cambio_video = ('video' in serializer.validated_data
                        and serializer.validated_data['video'].pk != serializer.instance.video_id)
        leccion = serializer.save()
        if cambio_video:
            leccion.progresos.update(visto=False, fecha_completado=None)
        recalcular_curso(leccion.modulo.curso)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        leccion = self.get_object()
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=leccion.modulo.curso_id)
        response = super().destroy(request, *args, **kwargs)
        recalcular_curso(curso)
        return response

    @swagger_auto_schema(request_body=RegistrarProgresoSerializer, responses={200: ResultadoProgresoSerializer})
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    @transaction.atomic
    def progreso(self, request, pk=None):
        serializer = RegistrarProgresoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        leccion = self.get_object()
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=leccion.modulo.curso_id)
        leccion = get_object_or_404(Leccion, pk=pk, activo=True)
        exigir_inscripcion(request, curso)
        progreso, _ = ProgresoLeccion.objects.get_or_create(usuario=request.user, leccion=leccion)
        visto = serializer.validated_data['visto']
        progreso.fecha_completado = (progreso.fecha_completado or timezone.now()) if visto else None
        progreso.visto = visto
        progreso.save(update_fields=['visto', 'fecha_completado'])
        inscripcion = Inscripcion.objects.select_related('usuario', 'curso__instructor').get(
            usuario=request.user, curso=curso,
        )
        recalcular_inscripcion(inscripcion)
        return Response({'progreso': ProgresoSerializer(progreso).data,
                         'inscripcion': InscripcionSerializer(inscripcion).data})


class InscripcionViewSet(APIPrivada, mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = InscripcionSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Inscripcion.objects.none()
        return Inscripcion.objects.select_related('curso').filter(usuario=self.request.user)

    def get_serializer_class(self):
        return InscribirSerializer if self.action == 'create' else InscripcionSerializer

    @swagger_auto_schema(responses={200: InscripcionSerializer, 201: InscripcionSerializer})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return inscribir_usuario(request, pk=serializer.validated_data['curso'].pk)


class ProgresoLeccionViewSet(APIPrivada, viewsets.ReadOnlyModelViewSet):
    serializer_class = ProgresoSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return ProgresoLeccion.objects.none()
        return ProgresoLeccion.objects.filter(usuario=self.request.user)


class CertificadoCursoViewSet(APIPrivada, viewsets.ReadOnlyModelViewSet):
    serializer_class = CertificadoCursoSerializer

    def get_queryset(self):
        qs = CertificadoCurso.objects.select_related('curso').defer('archivo_pdf')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        return qs.filter(usuario=self.request.user)

    @swagger_auto_schema(responses={200: openapi.Response('Certificado PDF privado', schema=openapi.Schema(type=openapi.TYPE_FILE))})
    @action(detail=True, methods=['get'])
    def descargar(self, request, pk=None):
        certificado = self.get_object()
        response = HttpResponse(bytes(certificado.archivo_pdf), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="certificado-{certificado.codigo_certificado}.pdf"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
