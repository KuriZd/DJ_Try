import hashlib
import uuid
from datetime import datetime, timezone as datetime_timezone

from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_409_CONFLICT,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import (
    Aspirante,
    EstadoReportePsicometrico,
    EstadoVacante,
    HistorialReportePsicometrico,
    OrigenReportePsicometrico,
    Postulacion,
    ReportePsicometrico,
    Sesion,
    Vacante,
)
from core.services.pagos import (
    IdempotenciaEnConflicto,
    PagoNoDisponible,
    iniciar_pago_paypal,
)
from core.services.paypal import PaypalConfigurationError, PaypalError

from .serializers import (
    AspiranteSerializer,
    CambioPasswordSerializer,
    CrearOrdenPagoPaypalSerializer,
    LoginSerializer,
    PerfilUpdateSerializer,
    PostulacionCrearSerializer,
    PostulacionSerializer,
    ReportePsicometricoSerializer,
    OrdenPagoPaypalSerializer,
    RegistroSerializer,
    UsuarioSerializer,
    VacanteAdminSerializer,
    VacantePublicaSerializer,
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
            "reportes_psicometricos": reverse(
                "api:reporte-psicometrico-list", request=request
            ),
            "crear_orden_paypal": reverse(
                "api:crear-orden-paypal", request=request
            ),
            "vacantes": reverse("api:vacante-list", request=request),
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


@swagger_auto_schema(
    method="post",
    request_body=CrearOrdenPagoPaypalSerializer,
    manual_parameters=[
        openapi.Parameter(
            "Idempotency-Key",
            openapi.IN_HEADER,
            description="Clave opcional para reintentar la misma solicitud.",
            type=openapi.TYPE_STRING,
            max_length=128,
        )
    ],
    responses={
        200: OrdenPagoPaypalSerializer,
        201: OrdenPagoPaypalSerializer,
        202: OrdenPagoPaypalSerializer,
        400: "Reporte inválido o no disponible.",
        409: "Conflicto de idempotencia.",
        502: "PayPal rechazó o no respondió la solicitud.",
        503: "Credenciales de PayPal sin configurar.",
    },
)
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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def crear_orden_paypal(request):
    """Reserva o reutiliza una orden y la crea en PayPal Orders v2."""
    serializer = CrearOrdenPagoPaypalSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)

    clave_idempotencia = (request.headers.get("Idempotency-Key") or "").strip()
    if len(clave_idempotencia) > 128:
        return Response(
            {"detail": "Idempotency-Key no puede superar 128 caracteres."},
            status=HTTP_400_BAD_REQUEST,
        )

    try:
        orden, nueva = iniciar_pago_paypal(
            reporte=serializer.context["reporte"],
            comprador=request.user,
            clave_idempotencia=clave_idempotencia or None,
            ip=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT"),
        )
    except IdempotenciaEnConflicto as error:
        return Response({"detail": str(error)}, status=HTTP_409_CONFLICT)
    except PagoNoDisponible as error:
        return Response({"detail": str(error)}, status=HTTP_400_BAD_REQUEST)
    except PaypalConfigurationError as error:
        return Response(
            {"detail": str(error), "code": error.code},
            status=HTTP_503_SERVICE_UNAVAILABLE,
        )
    except PaypalError as error:
        return Response(
            {"detail": str(error), "code": error.code},
            status=HTTP_502_BAD_GATEWAY,
        )

    data = OrdenPagoPaypalSerializer(orden).data
    data["reutilizada"] = not nueva
    if nueva:
        status_code = HTTP_201_CREATED
    elif orden.estado == "PENDING":
        status_code = 202
    else:
        status_code = HTTP_200_OK
    return Response(data, status=status_code)


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


class VacantePublicaViewSet(viewsets.ReadOnlyModelViewSet):
    """Vacantes visibles en la sección pública del frontend."""

    serializer_class = VacantePublicaSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        ahora = timezone.now()
        return (
            Vacante.objects.filter(
                estado=EstadoVacante.PUBLICADA,
                publicada_en__lte=ahora,
            )
            .filter(Q(cierra_en__isnull=True) | Q(cierra_en__gt=ahora))
            .order_by("-publicada_en", "-id")
        )


class PuedeAdministrarVacantes(BasePermission):
    """Alta y edición de vacantes: administrador y reclutador."""

    message = "No tienes permiso para administrar vacantes."

    def has_permission(self, request, view):
        return "vacantes:administrar" in permisos_de(request.user)


class VacanteAdminViewSet(viewsets.ModelViewSet):
    """
    Gestión de vacantes para el equipo interno.

    Va aparte de VacantePublicaViewSet a propósito: aquél es de sólo lectura y
    abierto, y conviene que siga siéndolo en vez de mezclar escrituras tras un
    permiso. Aquí se ve el catálogo completo —borradores y cerradas incluidas—,
    que es lo que hace falta para administrarlo.

    No se expone DELETE: `postulaciones.vacante_id` es PROTECT, así que borrar
    una vacante con postulaciones fallaría, y borrarla sin ellas perdería el
    historial. Retirar una vacante es pasarla a `cerrada`.
    """

    serializer_class = VacanteAdminSerializer
    permission_classes = [IsAuthenticated, PuedeAdministrarVacantes]
    queryset = Vacante.objects.all().order_by("-creado_en", "-id")
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class PuedeConsultarPostulaciones(BasePermission):
    """
    Puerta del módulo de postulaciones.

    Sin `postulaciones:consultar` no se entra, ni siquiera a lo propio.
    """

    message = "No tienes permiso para consultar postulaciones."

    def has_permission(self, request, view):
        return "postulaciones:consultar" in permisos_de(request.user)


def expediente_de(request):
    """
    Expediente de aspirante ligado a la cuenta, o None si no tiene.

    Se memoiza en la request porque el flujo de alta lo consulta dos veces: al
    revisar el permiso y al armar el contexto del serializer.
    """
    if not hasattr(request, "_expediente_aspirante"):
        request._expediente_aspirante = (
            Aspirante.objects.filter(
                usuario=request.user, eliminado_en__isnull=True
            )
            .select_related("perfil_profesional")
            .first()
        )
    return request._expediente_aspirante


class PuedePostularse(BasePermission):
    """
    Alta de postulaciones: sólo quien tiene expediente de aspirante.

    Se decide por identidad y no por permiso. `postulaciones:administrar` no
    sirve —lo tienen reclutador y administrador, que no se postulan— y
    `postulaciones:consultar` tampoco alcanza, porque el rol de sólo consulta
    lee el proceso completo y no debería poder escribir en él.

    Quien se registra desde el frontend público sí queda con expediente
    (RegistroSerializer lo crea), que es justo el público de la bolsa.
    """

    message = "Necesitas un expediente de aspirante para postularte."

    def has_permission(self, request, view):
        if request.method != "POST":
            return True
        return expediente_de(request) is not None


class PostulacionViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    """
    Postulaciones al proceso de selección.

    Quien tiene `postulaciones:consultar-todas` ve todas; el resto sólo las
    suyas. Igual que en AspiranteViewSet el filtro va en el queryset, así que
    también cubre el detalle: pedir la postulación de otra persona responde
    404, no 403, para no confirmar que existe.

    El alcance se decide con `consultar-todas` y no con `administrar` porque
    son cosas distintas: el rol de sólo consulta lee el proceso completo sin
    poder tocarlo. `postulaciones:consultar` tampoco sirve para esto, ya que
    el rol aspirante lo tiene para ver las suyas.
    """

    serializer_class = PostulacionSerializer
    permission_classes = [
        IsAuthenticated,
        PuedeConsultarPostulaciones,
        PuedePostularse,
    ]

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

        if "postulaciones:consultar-todas" in permisos_de(self.request.user):
            return base

        return base.filter(aspirante__usuario=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return PostulacionCrearSerializer
        return PostulacionSerializer

    def get_serializer_context(self):
        contexto = super().get_serializer_context()
        if self.action == "create":
            contexto["expediente"] = expediente_de(self.request)
        return contexto

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            # El atomic acota el fallo: sin él, un IntegrityError dentro de una
            # transacción abierta la deja rota y la respuesta de error tampoco
            # podría escribirse.
            with transaction.atomic():
                postulacion = serializer.save()
        except IntegrityError:
            # Dos envíos simultáneos pasan la validación a la vez y sólo uno
            # gana el índice único. El segundo es un 400, no un 500.
            raise ValidationError(
                {"vacante": "Ya te postulaste a esta vacante."}
            )

        # Se responde con la forma de lectura para que el frontend reciba la
        # postulación tal como la verá después en el listado.
        salida = PostulacionSerializer(
            postulacion, context=self.get_serializer_context()
        )
        return Response(salida.data, status=HTTP_201_CREATED)


PERMISO_ADMIN_REPORTES = "reportes-psicometricos:administrar"
PERMISO_SUBIR_REPORTE_PROPIO = "reportes-psicometricos:subir-propio"


class PuedeSubirReportesPsicometricos(BasePermission):
    """La consulta es personal; para escribir hace falta uno de dos permisos.

    El administrador archiva el informe de cualquier aspirante; el aspirante
    archiva el suyo. Quién es cada quien lo resuelve el serializer, que es
    donde se conoce el expediente destino; y para retirar un documento, el
    propio ViewSet.
    """

    message = "No tienes permiso para archivar reportes psicométricos."

    def has_permission(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        permisos = permisos_de(request.user)
        return bool(
            permisos & {PERMISO_ADMIN_REPORTES, PERMISO_SUBIR_REPORTE_PROPIO}
        )


class ReportePsicometricoViewSet(
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """Carga administrativa y consulta privada de reportes externos."""

    serializer_class = ReportePsicometricoSerializer
    permission_classes = [IsAuthenticated, PuedeSubirReportesPsicometricos]

    def get_queryset(self):
        # La fecha de aplicacion manda; los reportes que no la traen no se
        # cuelan al principio por ser NULL, que es el orden natural de Postgres.
        base = ReportePsicometrico.objects.select_related(
            "aspirante", "subido_por"
        ).order_by(F("aplicada_en").desc(nulls_last=True), "-creado_en")

        if PERMISO_ADMIN_REPORTES in permisos_de(self.request.user):
            aspirante = self.request.query_params.get("aspirante")
            return base.filter(aspirante=aspirante) if aspirante else base

        # El expediente propio es histórico: se ven todos los reportes, no
        # sólo el último. Los deshabilitados no, que para eso se deshabilitan.
        return base.filter(aspirante__usuario=self.request.user).exclude(
            estado=EstadoReportePsicometrico.DESHABILITADO
        )

    def perform_destroy(self, instance):
        """Retira el documento del expediente sin borrar el rastro.

        No se elimina la fila: el reporte pasa a `disabled` —que el queryset
        ya excluye— y queda constancia de quién lo retiró. Un expediente que
        pierde registros sin dejar huella no sirve para rendir cuentas, y el
        archivo puede haberse cobrado.

        Sólo se retira lo que subió la propia persona; los informes que aplicó
        la plataforma los administra quien tiene el permiso de gestión.
        """
        permisos = permisos_de(self.request.user)
        es_admin = PERMISO_ADMIN_REPORTES in permisos

        if not es_admin and instance.origen != OrigenReportePsicometrico.PROPIA:
            raise PermissionDenied(
                "Sólo puedes retirar los informes que tú archivaste."
            )

        ahora = timezone.now()
        with transaction.atomic():
            ReportePsicometrico.objects.filter(pk=instance.pk).update(
                estado=EstadoReportePsicometrico.DESHABILITADO,
                disponible_para_compra=False,
                actualizado_en=ahora,
            )
            HistorialReportePsicometrico.objects.create(
                id=uuid.uuid4(),
                reporte=instance,
                accion="disabled",
                realizado_por=self.request.user,
                realizado_por_email=self.request.user.email,
                metadata={"origen": instance.origen},
                creado_en=ahora,
            )
