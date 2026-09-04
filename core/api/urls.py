from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AspiranteViewSet,
    CompraPaquetePsicometricoViewSet,
    UsuarioViewSet,
    PaquetePsicometricoViewSet,
    PostulacionViewSet,
    ReportePsicometricoViewSet,
    VacanteAdminViewSet,
    VacantePublicaViewSet,
    api_root,
    cambiar_password,
    cancelar_orden_paypal,
    capturar_orden_paypal,
    health_check,
    login,
    logout,
    orden_paypal,
    ordenes_paypal,
    recuperar_password,
    refresh_token,
    registro,
    restablecer_password,
    usuario_actual,
    webhook_paypal,
)


app_name = "api"

router = SimpleRouter()
router.register("usuarios", UsuarioViewSet, basename="usuario")
router.register("aspirantes", AspiranteViewSet, basename="aspirante")
router.register("postulaciones", PostulacionViewSet, basename="postulacion")
router.register(
    "paquetes-psicometricos",
    PaquetePsicometricoViewSet,
    basename="paquete-psicometrico",
)
router.register(
    "compras-psicometricas",
    CompraPaquetePsicometricoViewSet,
    basename="compra-psicometrica",
)
router.register(
    "reportes-psicometricos",
    ReportePsicometricoViewSet,
    basename="reporte-psicometrico",
)
router.register("vacantes", VacantePublicaViewSet, basename="vacante")
router.register("admin/vacantes", VacanteAdminViewSet, basename="vacante-admin")

urlpatterns = [
    path("", api_root, name="root"),
    path("health/", health_check, name="health"),
    path("auth/login/", login, name="login"),
    path("auth/registro/", registro, name="registro"),
    path("auth/refresh/", refresh_token, name="refresh"),
    path("auth/logout/", logout, name="logout"),
    path("auth/me/", usuario_actual, name="usuario-actual"),
    path("auth/password/", cambiar_password, name="cambiar-password"),
    path(
        "auth/recuperar/", recuperar_password, name="recuperar-password"
    ),
    path(
        "auth/restablecer/", restablecer_password, name="restablecer-password"
    ),
    path("pagos/paypal/ordenes/", ordenes_paypal, name="ordenes-paypal"),
    # Las rutas literales van antes que la de referencia: si no, "capturar"
    # entraria como si fuera el nombre de una orden.
    path(
        "pagos/paypal/ordenes/capturar/",
        capturar_orden_paypal,
        name="capturar-orden-paypal",
    ),
    path(
        "pagos/paypal/ordenes/cancelar/",
        cancelar_orden_paypal,
        name="cancelar-orden-paypal",
    ),
    path(
        "pagos/paypal/ordenes/<str:referencia>/",
        orden_paypal,
        name="orden-paypal",
    ),
    path("pagos/paypal/webhook/", webhook_paypal, name="webhook-paypal"),
    path("", include(router.urls)),
]
