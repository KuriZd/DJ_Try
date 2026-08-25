from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AspiranteViewSet,
    PostulacionViewSet,
    ReportePsicometricoViewSet,
    VacanteAdminViewSet,
    VacantePublicaViewSet,
    api_root,
    cambiar_password,
    health_check,
    login,
    logout,
    refresh_token,
    registro,
    usuario_actual,
)


app_name = "api"

router = SimpleRouter()
router.register("aspirantes", AspiranteViewSet, basename="aspirante")
router.register("postulaciones", PostulacionViewSet, basename="postulacion")
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
    path("", include(router.urls)),
]
