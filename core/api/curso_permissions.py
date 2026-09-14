from rest_framework.permissions import BasePermission, SAFE_METHODS

from .serializers import permisos_de


def permisos_cursos(request):
    if not hasattr(request, '_permisos_cursos'):
        request._permisos_cursos = permisos_de(request.user)
    return request._permisos_cursos


def administra_cursos(request):
    return 'cursos:administrar' in permisos_cursos(request)


def gestiona_curso(request, curso):
    return administra_cursos(request) or (
        'cursos:crear' in permisos_cursos(request) and curso.instructor_id == request.user.pk
    )


class PuedeGestionarCursos(BasePermission):
    message = 'No tienes permiso para administrar este curso.'

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS or view.action == 'inscribir':
            return True
        return bool({'cursos:crear', 'cursos:administrar'} & permisos_cursos(request))

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return gestiona_curso(request, getattr(obj, 'curso', obj))
