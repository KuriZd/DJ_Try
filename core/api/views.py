import hashlib
import uuid
from datetime import datetime, timezone as datetime_timezone

from django.db import connection, transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Aspirante, Postulacion, Sesion

from .serializers import (
    AspiranteSerializer,
    CambioPasswordSerializer,
    LoginSerializer,
    PerfilUpdateSerializer,
    PostulacionSerializer,
    RegistroSerializer,
    UsuarioSerializer,
    permisos_de,
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
            "postulaciones": reverse("api:postulacion-list", request=request),
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


def abrir_sesion(usuario, request):
    """
    Emite el par de tokens y registra la sesión. Lo comparten el acceso y el
    alta de cuenta, que entra autenticada.
    """
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

    return token_response(usuario, refresh)


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    usuario = serializer.validated_data["usuario"]

    return Response(abrir_sesion(usuario, request), status=HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
@transaction.atomic
def registro(request):
    """Da de alta una cuenta con su expediente y la deja autenticada."""
    serializer = RegistroSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    usuario = serializer.save()

    return Response(abrir_sesion(usuario, request), status=HTTP_201_CREATED)


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
    """
    Expedientes de aspirantes.

    Quien tiene `aspirantes:consultar` ve todos; el resto sólo ve el suyo. El
    filtrado va en el queryset, así que también aplica al detalle: pedir el
    folio de otra persona responde 404, no 403, para no confirmar que existe.
    """

    serializer_class = AspiranteSerializer

    def get_queryset(self):
        base = (
            Aspirante.objects.filter(eliminado_en__isnull=True)
            .select_related("convocatoria", "perfil_profesional")
            .order_by("id")
        )

        if "aspirantes:consultar" in permisos_de(self.request.user):
            return base

        return base.filter(usuario=self.request.user)


class PuedeConsultarPostulaciones(BasePermission):
    """
    Puerta del módulo de postulaciones.

    Sin `postulaciones:consultar` no se entra, ni siquiera a lo propio. Hoy eso
    deja fuera al rol certificador, que no participa en el proceso de
    selección.
    """

    message = "No tienes permiso para consultar postulaciones."

    def has_permission(self, request, view):
        return "postulaciones:consultar" in permisos_de(request.user)


class PostulacionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Postulaciones al proceso de selección.

    Quien tiene `postulaciones:administrar` ve todas; el resto sólo las suyas.
    Igual que en AspiranteViewSet el filtro va en el queryset, así que también
    cubre el detalle: pedir la postulación de otra persona responde 404, no
    403, para no confirmar que existe.

    Ojo con `postulaciones:consultar`: no sirve para decidir el alcance porque
    el rol aspirante también lo tiene, justamente para ver las suyas.
    """

    serializer_class = PostulacionSerializer
    permission_classes = [IsAuthenticated, PuedeConsultarPostulaciones]

    def get_queryset(self):
        base = (
            Postulacion.objects.filter(aspirante__eliminado_en__isnull=True)
            .select_related(
                "aspirante", "aspirante__perfil_profesional", "vacante"
            )
            # Bandeja de reclutamiento: lo más reciente primero. El id desempata
            # para que la paginación no repita ni se salte registros.
            .order_by("-registrada_en", "-id")
        )

        if "postulaciones:administrar" in permisos_de(self.request.user):
            return base

        return base.filter(aspirante__usuario=self.request.user)
