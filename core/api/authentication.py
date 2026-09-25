import uuid

from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from core.models import EstadoUsuario, Sesion, Usuario


class UsuarioJWTAuthentication(JWTAuthentication):
    """Autenticación JWT contra la tabla personalizada `usuarios`."""

    def get_user(self, validated_token):
        user_id = validated_token.get("user_id")
        if not user_id:
            raise AuthenticationFailed("El token no contiene un usuario.")

        try:
            usuario = Usuario.objects.get(id=user_id, eliminado_en__isnull=True)
        except (Usuario.DoesNotExist, ValueError, TypeError):
            raise AuthenticationFailed("Usuario no encontrado.")

        if usuario.estado != EstadoUsuario.ACTIVO:
            raise AuthenticationFailed("El usuario no está activo.")

        # Los access tokens heredan el sid del refresh. No se admiten tokens
        # anteriores sin sesion: no seria posible revocarlos al recuperar acceso.
        try:
            session_id = uuid.UUID(str(validated_token.get("sid", "")))
        except (ValueError, TypeError, AttributeError):
            raise AuthenticationFailed("La sesión no es válida. Inicia sesión de nuevo.")
        if not Sesion.objects.filter(
            id=session_id, usuario=usuario, revocada_en__isnull=True,
            expira_en__gt=timezone.now(),
        ).exists():
            raise AuthenticationFailed("La sesión expiró o fue revocada.")

        return usuario
