import threading
import time

import requests
from django.conf import settings


class PaypalError(Exception):
    def __init__(self, message, *, code="PAYPAL_ERROR", status_code=None, payload=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.payload = payload or {}


class PaypalConfigurationError(PaypalError):
    pass


class PaypalClient:
    """Cliente mínimo para OAuth y Orders v2, sin filtrar secretos a logs."""

    def __init__(self):
        self._access_token = None
        self._access_token_expira = 0.0
        self._token_lock = threading.Lock()

    def _configuracion(self):
        if settings.PAYPAL_MODE not in {"sandbox", "live"}:
            raise PaypalConfigurationError(
                "PAYPAL_MODE debe ser sandbox o live.",
                code="INVALID_PAYPAL_MODE",
            )
        if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
            raise PaypalConfigurationError(
                "Faltan las credenciales de PayPal.",
                code="MISSING_PAYPAL_CREDENTIALS",
            )

    @staticmethod
    def _json_seguro(response):
        try:
            return response.json()
        except ValueError:
            return {"message": "PayPal devolvió una respuesta no JSON."}

    def _obtener_access_token(self):
        self._configuracion()
        ahora = time.monotonic()
        if self._access_token and ahora < self._access_token_expira:
            return self._access_token

        with self._token_lock:
            ahora = time.monotonic()
            if self._access_token and ahora < self._access_token_expira:
                return self._access_token
            try:
                response = requests.post(
                    f"{settings.PAYPAL_API_BASE_URL}/v1/oauth2/token",
                    auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
                    headers={"Accept": "application/json"},
                    data={"grant_type": "client_credentials"},
                    timeout=settings.PAYPAL_HTTP_TIMEOUT,
                )
            except requests.RequestException as error:
                raise PaypalError(
                    "No fue posible autenticar con PayPal.",
                    code="PAYPAL_CONNECTION_ERROR",
                ) from error

            data = self._json_seguro(response)
            if response.status_code != 200 or not data.get("access_token"):
                raise PaypalError(
                    "PayPal rechazó las credenciales del comercio.",
                    code=data.get("error", "PAYPAL_AUTH_ERROR"),
                    status_code=response.status_code,
                    payload=data,
                )

            # Se renueva un minuto antes para no usar un token al borde de expirar.
            self._access_token = data["access_token"]
            self._access_token_expira = ahora + max(
                int(data.get("expires_in", 300)) - 60, 30
            )
            return self._access_token

    def crear_orden(
        self,
        *,
        referencia,
        request_id,
        monto,
        moneda,
        descripcion,
    ):
        access_token = self._obtener_access_token()
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": referencia,
                    "custom_id": referencia,
                    "description": descripcion[:127],
                    "amount": {
                        "currency_code": moneda,
                        "value": f"{monto:.2f}",
                    },
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "PAY_NOW",
                        "return_url": settings.PAYPAL_RETURN_URL,
                        "cancel_url": settings.PAYPAL_CANCEL_URL,
                    }
                }
            },
        }
        try:
            response = requests.post(
                f"{settings.PAYPAL_API_BASE_URL}/v2/checkout/orders",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "PayPal-Request-Id": request_id,
                    "Prefer": "return=representation",
                },
                json=payload,
                timeout=settings.PAYPAL_HTTP_TIMEOUT,
            )
        except requests.RequestException as error:
            raise PaypalError(
                "No fue posible crear la orden en PayPal.",
                code="PAYPAL_CONNECTION_ERROR",
            ) from error

        data = self._json_seguro(response)
        if response.status_code not in (200, 201) or not data.get("id"):
            raise PaypalError(
                "PayPal rechazó la creación de la orden.",
                code=data.get("name", "PAYPAL_CREATE_ORDER_ERROR"),
                status_code=response.status_code,
                payload=data,
            )

        approval_url = next(
            (
                enlace.get("href")
                for enlace in data.get("links", [])
                if enlace.get("rel") in {"payer-action", "approve"}
            ),
            None,
        )
        if not approval_url:
            raise PaypalError(
                "PayPal no devolvió una URL de aprobación.",
                code="PAYPAL_APPROVAL_URL_MISSING",
                status_code=response.status_code,
                payload=data,
            )
        return {
            "paypal_order_id": data["id"],
            "estado": data.get("status", "CREATED"),
            "approval_url": approval_url,
            "respuesta": data,
        }


paypal_client = PaypalClient()
