import hashlib

from rest_framework.throttling import SimpleRateThrottle


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
        email = str(request.data.get("email", "")).strip().casefold()
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
        email = str(request.data.get("email", "")).strip().casefold()
        cuenta = hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{ident}:{cuenta}",
        }


class RestablecerRateThrottle(IpRateThrottle):
    """Frena la fuerza bruta sobre el token del enlace."""

    scope = "restablecer"


class PaypalWebhookRateThrottle(IpRateThrottle):
    scope = "paypal_webhook"
