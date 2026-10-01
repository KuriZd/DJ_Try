from django.utils import timezone
from rest_framework import viewsets
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission, IsAuthenticated

from core.models import Publicacion
from .curso_views import APIPrivada
from .publicacion_serializers import PublicacionSerializer
from .serializers import permisos_de
from .throttles import PublicarRateThrottle


class PaginacionMuro(CursorPagination):
    """Cursor y no numero de pagina: el muro crece por arriba mientras se lee,
    y con offsets la siguiente pagina repetiria las que se acaban de recorrer."""

    page_size = 10
    ordering = ('-fecha_publicacion', '-id')


class PuedeModificarPublicacion(BasePermission):
    """Editar es solo del autor; eliminar, del autor o de quien modera."""

    message = 'No puedes modificar esta publicacion.'

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if obj.autor_id == request.user.pk:
            return True
        return request.method == 'DELETE' and 'publicaciones:administrar' in permisos_de(request.user)


class PublicacionViewSet(APIPrivada, viewsets.ModelViewSet):
    """Muro de `/actualiza`.

    Leer es publico. Publicar pide sesion, sin permiso adicional: cualquier
    cuenta activa escribe en el muro, con un limite de altas por hora.
    """

    serializer_class = PublicacionSerializer
    pagination_class = PaginacionMuro
    permission_classes = [IsAuthenticated, PuedeModificarPublicacion]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [AllowAny()]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == 'create':
            return [PublicarRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        return Publicacion.objects.select_related('autor')

    def perform_create(self, serializer):
        serializer.save(autor=self.request.user)

    def perform_update(self, serializer):
        serializer.save(fecha_edicion=timezone.now())
