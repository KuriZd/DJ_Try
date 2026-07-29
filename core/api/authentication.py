from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from core.models import EstadoUsuario, Usuario


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

        return usuario
