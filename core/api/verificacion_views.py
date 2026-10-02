"""Verificacion del correo de una cuenta.

Hasta ahora el registro dejaba la cuenta activa sin comprobar el correo.
Actualiza es la primera seccion que lo exige: es publica y lleva el nombre de
quien escribe, asi que una cuenta con correo desechable no debe poder hablar
ahi en nombre de nadie.

    POST /api/auth/verificacion/            con sesion — manda el enlace
    POST /api/auth/verificacion/confirmar/  publico — { token } lo canjea

Confirmar es publico porque el enlace se abre donde llegue el correo, que
puede ser otro dispositivo sin sesion. Lo que autoriza es el token, igual que
al restablecer la contrasena.
"""

from django.db import transaction
from django.utils import timezone
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_204_NO_CONTENT, HTTP_400_BAD_REQUEST

from core.models import PropositoToken
from core.services import avisos
from core.services import tokens as tokens_service
from .throttles import ConfirmarVerificacionRateThrottle, EnviarVerificacionRateThrottle


class ConfirmarVerificacionSerializer(serializers.Serializer):
    token = serializers.CharField(write_only=True, trim_whitespace=False)


@swagger_auto_schema(
    method="post",
    request_body=no_body,
    responses={204: "Enlace enviado, o el correo ya estaba verificado."},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([EnviarVerificacionRateThrottle])
def solicitar_verificacion(request):
    """Manda el enlace al correo de la cuenta. Si ya esta verificado, no hace
    nada: responder igual evita que un doble clic acabe en un error."""
    usuario = request.user
    if usuario.email_verificado_en is None:
        token = tokens_service.emitir(usuario, PropositoToken.VERIFICACION)
        avisos.enviar_verificacion(usuario, token)
    return Response(status=HTTP_204_NO_CONTENT)


@swagger_auto_schema(
    method="post",
    request_body=ConfirmarVerificacionSerializer,
    responses={
        204: "Correo verificado.",
        400: "El enlace no es valido, ya se uso o caduco.",
    },
)
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ConfirmarVerificacionRateThrottle])
@transaction.atomic
def confirmar_verificacion(request):
    serializer = ConfirmarVerificacionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        usuario = tokens_service.canjear(
            serializer.validated_data["token"], PropositoToken.VERIFICACION,
        )
    except tokens_service.TokenInvalido as error:
        return Response({"token": [str(error)]}, status=HTTP_400_BAD_REQUEST)

    if usuario.email_verificado_en is None:
        usuario.email_verificado_en = timezone.now()
        usuario.save(update_fields=["email_verificado_en"])

    return Response(status=HTTP_204_NO_CONTENT)
