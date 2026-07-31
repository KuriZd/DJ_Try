import hashlib
import uuid
from datetime import datetime, timezone as datetime_timezone

from django.db import connection
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Aspirante, Certificado, Convocatoria, Sesion, Vacante

from .serializers import (
    AspiranteSerializer,
    CambioPasswordSerializer,
    CertificadoSerializer,
    ConvocatoriaSerializer,
    LoginSerializer,
    PerfilUpdateSerializer,
    UsuarioSerializer,
    VacanteSerializer,
)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_root(request):
    return Response(
        {
            "name": "DJ Try API",
            "health": reverse("api:health", request=request),
            "usuario_actual": reverse("api:usuario-actual", request=request),
            "aspirantes": reverse("api:aspirante-list", request=request),
            "convocatorias": reverse("api:convocatoria-list", request=request),
            "vacantes": reverse("api:vacante-list", request=request),
            "certificados": reverse("api:certificado-list", request=request),
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return Response(
            {"status": "error", "database": "unavailable"},
            status=HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(
        {"status": "ok", "database": "available"},
        status=HTTP_200_OK,
    )


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_response(usuario, refresh):
    access = refresh.access_token
    access["email"] = usuario.email
    access["nombre_completo"] = usuario.nombre_completo

    # Se reutiliza UsuarioSerializer para que el login entregue exactamente la
    # misma forma que GET /auth/me/ (fechas, roles y aspirante incluidos).
    return {
        "access": str(access),
        "refresh": str(refresh),
        "usuario": UsuarioSerializer(usuario).data,
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    usuario = serializer.validated_data["usuario"]

    refresh = RefreshToken.for_user(usuario)
    refresh["email"] = usuario.email

    Sesion.objects.create(
        id=uuid.uuid4(),
        usuario=usuario,
        refresh_token_hash=token_hash(str(refresh)),
        ip=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT"),
        expira_en=datetime.fromtimestamp(
            refresh["exp"], tz=datetime_timezone.utc
        ),
        creado_en=timezone.now(),
    )
    usuario.ultimo_acceso_en = timezone.now()
    usuario.save(update_fields=["ultimo_acceso_en"])

    return Response(token_response(usuario, refresh), status=HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def refresh_token(request):
    raw_refresh = request.data.get("refresh")
    if not raw_refresh:
        return Response(
            {"detail": "El campo refresh es obligatorio."},
            status=HTTP_400_BAD_REQUEST,
        )

    try:
        refresh = RefreshToken(raw_refresh)
        session = Sesion.objects.select_related("usuario").get(
            refresh_token_hash=token_hash(raw_refresh),
            revocada_en__isnull=True,
            expira_en__gt=timezone.now(),
        )
    except (TokenError, Sesion.DoesNotExist):
        return Response(
            {"detail": "El refresh token no es válido o fue revocado."},
            status=HTTP_401_UNAUTHORIZED,
        )

    if not session.usuario.is_active:
        return Response(
            {"detail": "El usuario no está activo."},
            status=HTTP_401_UNAUTHORIZED,
        )

    return Response(
        {"access": str(refresh.access_token)},
        status=HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request):
    raw_refresh = request.data.get("refresh")
    if not raw_refresh:
        return Response(
            {"detail": "El campo refresh es obligatorio."},
            status=HTTP_400_BAD_REQUEST,
        )

    updated = Sesion.objects.filter(
        usuario=request.user,
        refresh_token_hash=token_hash(raw_refresh),
        revocada_en__isnull=True,
    ).update(revocada_en=timezone.now())

    if not updated:
        return Response(
            {"detail": "La sesión no existe o ya fue revocada."},
            status=HTTP_400_BAD_REQUEST,
        )

    return Response({"detail": "Sesión cerrada."}, status=HTTP_200_OK)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def usuario_actual(request):
    """Consulta (GET) o edita (PATCH) el perfil del usuario autenticado."""
    if request.method == "PATCH":
        serializer = PerfilUpdateSerializer(
            request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

    return Response(UsuarioSerializer(request.user).data, status=HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cambiar_password(request):
    """
    Cambia la contraseña del usuario autenticado.

    Por seguridad se revocan las sesiones abiertas. Si el cliente envía su
    `refresh`, esa sesión se conserva para no expulsarlo del navegador actual;
    si no lo envía, se revocan todas.
    """
    serializer = CambioPasswordSerializer(
        data=request.data, context={"usuario": request.user}
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    sesiones = Sesion.objects.filter(
        usuario=request.user, revocada_en__isnull=True
    )

    raw_refresh = request.data.get("refresh")
    if raw_refresh:
        sesiones = sesiones.exclude(refresh_token_hash=token_hash(raw_refresh))

    sesiones.update(revocada_en=timezone.now())

    return Response(status=HTTP_204_NO_CONTENT)


class AspiranteViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AspiranteSerializer
    queryset = (
        Aspirante.objects.filter(eliminado_en__isnull=True)
        .select_related("convocatoria", "perfil_profesional")
        .order_by("id")
    )


class ConvocatoriaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConvocatoriaSerializer
    queryset = Convocatoria.objects.all().order_by("-creado_en", "id")


class VacanteViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VacanteSerializer
    queryset = Vacante.objects.select_related("convocatoria").order_by(
        "-creado_en", "id"
    )


class CertificadoViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CertificadoSerializer
    queryset = Certificado.objects.select_related(
        "aspirante", "tipo", "proceso"
    ).order_by("-creado_en", "id")
