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


class PaypalWebhookRateThrottle(IpRateThrottle):
    scope = "paypal_webhook"
