import hashlib
from collections.abc import Mapping

from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class IpRateThrottle(SimpleRateThrottle):
    """Limite por IP para endpoints publicos con trabajo sensible."""

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginRateThrottle(IpRateThrottle):
    """Combina IP y correo sin guardar el correo en claro en la cache."""

    scope = "login"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        data = request.data if isinstance(request.data, Mapping) else {}
        email = str(data.get("email", "")).strip().casefold()
        cuenta = hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]
        compuesto = f"{ident}:{cuenta}"
        return self.cache_format % {
            "scope": self.scope,
            "ident": compuesto,
        }


class RegistroRateThrottle(IpRateThrottle):
    scope = "registro"


class RefreshRateThrottle(IpRateThrottle):
    scope = "refresh"


class RecuperarRateThrottle(IpRateThrottle):
    """Igual que el de acceso: combina IP y correo sin guardarlo en claro.

    Sin la parte del correo, un atacante con una IP podria sondear cinco
    cuentas distintas por hora; con ella, cinco intentos por cuenta y por IP.
    """

    scope = "recuperar"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        data = request.data if isinstance(request.data, Mapping) else {}
        email = str(data.get("email", "")).strip().casefold()
        cuenta = hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{ident}:{cuenta}",
        }


class RestablecerRateThrottle(IpRateThrottle):
    """Frena la fuerza bruta sobre el token del enlace."""

    scope = "restablecer"


class VerificarCertificadoRateThrottle(IpRateThrottle):
    """Frena el sondeo de codigos de verificacion desde una misma IP."""

    scope = "verificar_certificado"


class PaypalWebhookRateThrottle(IpRateThrottle):
    scope = "paypal_webhook"


class PublicarRateThrottle(UserRateThrottle):
    """Cualquier cuenta publica en el muro: el limite frena el spam por cuenta."""

    scope = "publicar"


class ComentarRateThrottle(UserRateThrottle):
    """Mismo motivo que publicar, con mas holgura: una conversacion pide
    varios comentarios seguidos."""

    scope = "comentar"


class EnviarVerificacionRateThrottle(UserRateThrottle):
    """Cada envio es un correo al buzon de la cuenta: pocos por hora."""

    scope = "enviar_verificacion"


class ConfirmarVerificacionRateThrottle(IpRateThrottle):
    """Frena la fuerza bruta sobre el token del enlace, como restablecer."""

    scope = "confirmar_verificacion"


class ReportarRateThrottle(UserRateThrottle):
    """Reportar es facil a proposito; el limite evita usarlo para inundar la
    cola de moderacion."""

    scope = "reportar"


class ReaccionarRateThrottle(UserRateThrottle):
    """Poner y quitar un "me gusta" es barato para quien lo hace pero escribe en
    la base cada vez; el limite corta la rafaga sin estorbar a quien lee."""

    scope = "reaccionar"
