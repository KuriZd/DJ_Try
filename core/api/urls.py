from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AspiranteViewSet,
    CertificadoViewSet,
    ConvocatoriaViewSet,
    VacanteViewSet,
    api_root,
    cambiar_password,
    health_check,
    login,
    logout,
    refresh_token,
    usuario_actual,
)


app_name = "api"

router = SimpleRouter()
router.register("aspirantes", AspiranteViewSet, basename="aspirante")
router.register("convocatorias", ConvocatoriaViewSet, basename="convocatoria")
router.register("vacantes", VacanteViewSet, basename="vacante")
router.register("certificados", CertificadoViewSet, basename="certificado")

urlpatterns = [
    path("", api_root, name="root"),
    path("health/", health_check, name="health"),
    path("auth/login/", login, name="login"),
    path("auth/refresh/", refresh_token, name="refresh"),
    path("auth/logout/", logout, name="logout"),
    path("auth/me/", usuario_actual, name="usuario-actual"),
    path("auth/password/", cambiar_password, name="cambiar-password"),
    path("", include(router.urls)),
]
