import uuid

from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response

from core.models import Comentario, EstadoReporte, EstadoUsuario, Publicacion, Reaccion, Reporte
from core.services import moderacion
from .curso_views import APIPrivada
from .publicacion_serializers import (
    ComentarioSerializer, CrearReporteSerializer, EstadoReaccionSerializer,
    PublicacionSerializer, ReporteSerializer, ResolverReporteSerializer,
)
from .serializers import permisos_de
from .throttles import (
    ComentarRateThrottle, PublicarRateThrottle, ReaccionarRateThrottle, ReportarRateThrottle,
)

# Lo que cabe en una consulta de "mis reacciones": un par de paginas del muro.
MAXIMO_IDS_REACCIONES = 50


def modera_publicaciones(user):
    return 'publicaciones:administrar' in permisos_de(user)


def cuenta_visible(campo):
    """Filtro de "la cuenta sigue activa". Lo de una cuenta bloqueada o dada de
    baja desaparece del muro -- publicaciones, comentarios y reacciones -- sin
    tener que borrarlo uno por uno, y vuelve si la cuenta se reactiva."""
    return {f'{campo}__estado': EstadoUsuario.ACTIVO, f'{campo}__eliminado_en__isnull': True}


def total_visible(modelo, campo_cuenta):
    """Cuantas filas de `modelo` cuelgan de la publicacion, en una subconsulta.

    Dos `Count` sobre la misma consulta unirian reacciones con comentarios y
    multiplicarian las filas (1,000 x 500 = 500,000 para contar 1,500); cada
    subconsulta cuenta lo suyo con el indice de su llave foranea.
    """
    conteo = (
        modelo.objects.filter(publicacion=OuterRef('pk'), **cuenta_visible(campo_cuenta))
        .order_by().values('publicacion').annotate(n=Count('pk')).values('n')[:1]
    )
    return Coalesce(Subquery(conteo, output_field=IntegerField()), 0)


class ConCorreoVerificado(BasePermission):
    """Escribir en el muro pide el correo verificado: es publico y lleva el
    nombre de quien escribe. Borrar lo propio no, ni quitar una reaccion: nadie
    debe quedarse sin poder retirar lo que dijo."""

    message = 'Verifica tu correo para participar en Actualiza.'
    code = 'correo_no_verificado'

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS or request.method == 'DELETE':
            return True
        return getattr(request.user, 'email_verificado_en', None) is not None


class PaginacionMuro(CursorPagination):
    """Cursor y no numero de pagina: el muro crece por arriba mientras se lee,
    y con offsets la siguiente pagina repetiria las que se acaban de recorrer."""

    page_size = 10
    ordering = ('-fecha_publicacion', '-id')


class EsModerador(BasePermission):
    message = 'Solo el equipo de moderacion puede hacer esto.'

    def has_permission(self, request, view):
        return modera_publicaciones(request.user)


def reportar_contenido(request, contenido):
    """Reportar es de cualquier cuenta con sesion, verificada o no: avisar de
    un abuso no debe pedir mas requisitos que el abuso mismo."""
    if contenido.autor_id == request.user.pk:
        raise ValidationError({'detail': 'No puedes reportar lo que escribiste.'})
    datos = CrearReporteSerializer(data=request.data)
    datos.is_valid(raise_exception=True)
    _, creado = moderacion.reportar(
        request.user, contenido, datos.validated_data['motivo'], datos.validated_data['detalle'],
    )
    # La respuesta no dice cuantos reportes hay: quien reporta no lo necesita,
    # y quien es reportado no debe poder averiguarlo asi.
    return Response({'detail': 'Gracias. Lo revisara el equipo de moderacion.'},
                    status=201 if creado else 200)


class PaginacionComentarios(CursorPagination):
    """Del mas antiguo al mas reciente: se leen como una conversacion."""

    page_size = 20
    ordering = ('fecha_publicacion', 'id')


class PuedeModificarPublicacion(BasePermission):
    """Editar es solo del autor; eliminar, del autor o de quien modera."""

    message = 'No puedes modificar esta publicacion.'

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if obj.autor_id == request.user.pk:
            return True
        return request.method == 'DELETE' and modera_publicaciones(request.user)


class PuedeModificarComentario(BasePermission):
    """Editar es solo de quien comento. Eliminar, ademas, del autor de la
    publicacion -- es su conversacion -- y de quien modera el muro."""

    message = 'No puedes modificar este comentario.'

    def has_object_permission(self, request, view, obj):
        if obj.autor_id == request.user.pk:
            return True
        if request.method != 'DELETE':
            return False
        return obj.publicacion.autor_id == request.user.pk or modera_publicaciones(request.user)


def ids_de_consulta(valor):
    """`?ids=a,b,c` como UUIDs validos, o 400."""
    partes = [parte for parte in (valor or '').split(',') if parte.strip()]
    if len(partes) > MAXIMO_IDS_REACCIONES:
        raise ValidationError({'ids': f'Como maximo {MAXIMO_IDS_REACCIONES} publicaciones por consulta.'})
    try:
        return [uuid.UUID(parte.strip()) for parte in partes]
    except ValueError:
        raise ValidationError({'ids': 'Debe ser una lista de identificadores separados por coma.'})


class PublicacionViewSet(APIPrivada, viewsets.ModelViewSet):
    """Muro de `/actualiza`.

    Leer es publico, tambien los comentarios. Publicar, comentar y reaccionar
    piden sesion, sin permiso adicional: cualquier cuenta activa participa,
    con un limite de altas por hora.
    """

    serializer_class = PublicacionSerializer
    pagination_class = PaginacionMuro
    permission_classes = [IsAuthenticated, ConCorreoVerificado, PuedeModificarPublicacion]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.action in ('list', 'retrieve') or (
            self.action == 'comentarios' and self.request.method == 'GET'
        ):
            return [AllowAny()]
        # Reaccionar y comentar no modifican la publicacion: no pasan por
        # PuedeModificarPublicacion, que los reservaria al autor.
        if self.action in ('reaccion', 'comentarios', 'mis_reacciones'):
            return [IsAuthenticated(), ConCorreoVerificado()]
        if self.action == 'reportar':
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == 'create':
            return [PublicarRateThrottle()]
        if self.action == 'reportar':
            return [ReportarRateThrottle()]
        if self.action == 'comentarios' and self.request.method == 'POST':
            return [ComentarRateThrottle()]
        if self.action == 'reaccion':
            return [ReaccionarRateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == 'comentarios':
            return ComentarioSerializer
        if self.action == 'reaccion':
            return EstadoReaccionSerializer
        return PublicacionSerializer

    def get_queryset(self):
        return (
            Publicacion.objects.select_related('autor')
            .filter(**cuenta_visible('autor'))
            .annotate(
                total_reacciones=total_visible(Reaccion, 'usuario'),
                total_comentarios=total_visible(Comentario, 'autor'),
            )
        )

    def perform_create(self, serializer):
        serializer.save(autor=self.request.user)

    @transaction.atomic
    def perform_destroy(self, instance):
        moderacion.eliminar(self.request, instance, 'moderacion')

    def perform_update(self, serializer):
        serializer.save(fecha_edicion=timezone.now())
        # Se relee anotada para que la respuesta no reporte los totales en cero.
        serializer.instance = self.get_queryset().get(pk=serializer.instance.pk)

    @action(detail=True, methods=['post'])
    def reportar(self, request, pk=None):
        return reportar_contenido(request, self.get_object())

    @action(detail=True, methods=['post', 'delete'])
    def reaccion(self, request, pk=None):
        """POST la pone y DELETE la quita; las dos son idempotentes, asi que un
        doble clic no deshace lo que se acaba de hacer."""
        publicacion = self.get_object()
        if request.method == 'POST':
            Reaccion.objects.get_or_create(publicacion=publicacion, usuario=request.user)
        else:
            Reaccion.objects.filter(publicacion=publicacion, usuario=request.user).delete()
        estado = {
            'reaccionaste': request.method == 'POST',
            'total_reacciones': publicacion.reacciones.filter(**cuenta_visible('usuario')).count(),
        }
        return Response(EstadoReaccionSerializer(estado).data)

    @action(detail=False, methods=['get'], url_path='mis-reacciones')
    def mis_reacciones(self, request):
        """De las publicaciones de `?ids=`, en cuales reacciono la cuenta.

        Va aparte del muro porque el muro se lee sin token: este es el unico
        dato de la pagina que depende de quien mira.
        """
        ids = ids_de_consulta(request.query_params.get('ids'))
        reaccionadas = Reaccion.objects.filter(
            usuario=request.user, publicacion_id__in=ids,
        ).values_list('publicacion_id', flat=True)
        return Response({'reaccionadas': [str(id_) for id_ in reaccionadas]})

    @action(detail=True, methods=['get', 'post'])
    def comentarios(self, request, pk=None):
        publicacion = self.get_object()
        if request.method == 'POST':
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(publicacion=publicacion, autor=request.user)
            return Response(serializer.data, status=201)

        paginador = PaginacionComentarios()
        pagina = paginador.paginate_queryset(
            publicacion.comentarios.select_related('autor').filter(**cuenta_visible('autor')),
            request, view=self,
        )
        return paginador.get_paginated_response(self.get_serializer(pagina, many=True).data)


class ComentarioViewSet(APIPrivada, mixins.UpdateModelMixin, mixins.DestroyModelMixin,
                        viewsets.GenericViewSet):
    """Editar y eliminar un comentario. Listarlos y crearlos cuelga de su
    publicacion: `publicaciones/{id}/comentarios/`."""

    serializer_class = ComentarioSerializer
    permission_classes = [IsAuthenticated, ConCorreoVerificado, PuedeModificarComentario]
    http_method_names = ['post', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.action == 'reportar':
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == 'reportar':
            return [ReportarRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        return Comentario.objects.select_related('autor', 'publicacion')

    def perform_update(self, serializer):
        serializer.save(fecha_edicion=timezone.now())

    @transaction.atomic
    def perform_destroy(self, instance):
        motivo = (
            'autor_publicacion' if instance.publicacion.autor_id == self.request.user.pk
            else 'moderacion'
        )
        moderacion.eliminar(self.request, instance, motivo)

    @action(detail=True, methods=['post'])
    def reportar(self, request, pk=None):
        return reportar_contenido(request, self.get_object())


class PaginacionReportes(CursorPagination):
    page_size = 20
    ordering = ('creado_en', 'id')


class ReporteViewSet(APIPrivada, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Cola de moderacion: `?estado=` (pendiente por omision), del mas antiguo
    al mas reciente, para atender primero lo que lleva mas tiempo esperando."""

    serializer_class = ReporteSerializer
    pagination_class = PaginacionReportes
    permission_classes = [IsAuthenticated, EsModerador]

    def get_queryset(self):
        estado = self.request.query_params.get('estado', EstadoReporte.PENDIENTE)
        if estado not in EstadoReporte.values:
            raise ValidationError({'estado': 'Estado de reporte desconocido.'})
        return Reporte.objects.filter(estado=estado).select_related(
            'autor_reportado', 'reportado_por', 'comentario',
        )

    @action(detail=True, methods=['post'])
    def resolver(self, request, pk=None):
        """`eliminar` borra lo reportado; `descartar` lo deja. Las dos cierran
        todos los reportes pendientes de ese contenido y quedan auditadas."""
        datos = ResolverReporteSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        with transaction.atomic():
            reporte = Reporte.objects.select_for_update().filter(pk=pk).first()
            if reporte is None:
                raise NotFound()
            if reporte.estado != EstadoReporte.PENDIENTE:
                return Response({'detail': 'Este reporte ya se resolvio.'}, status=409)

            contenido = moderacion.contenido_de(reporte)
            if datos.validated_data['accion'] == 'eliminar' and contenido is not None:
                moderacion.eliminar(request, contenido, 'reporte')
            else:
                moderacion.descartar(request, reporte)
        reporte.refresh_from_db()
        return Response(ReporteSerializer(reporte).data)
