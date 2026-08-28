import threading
import time
from urllib.parse import urlparse

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

    def _peticion(self, metodo, ruta, *, mensaje_error, request_id=None, cuerpo=None):
        """Llamada autenticada a la API de PayPal.

        Devuelve la respuesta y su JSON sin interpretarlos: cada operacion
        sabe que codigos son un exito para ella. Lo unico que se resuelve
        aqui es el fallo de red, que significa lo mismo en todas.
        """
        access_token = self._obtener_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id

        kwargs = {"headers": headers, "timeout": settings.PAYPAL_HTTP_TIMEOUT}
        if cuerpo is not None:
            kwargs["json"] = cuerpo

        # Se resuelve por nombre y se falla si no existe: un `else: get` deja
        # que un metodo mal escrito haga una consulta en vez de la escritura
        # que se pedia, devuelva 200, y parezca que funciono.
        llamar = getattr(requests, metodo.lower(), None)
        if llamar is None:
            raise PaypalError(
                f"Método HTTP no soportado: {metodo}.",
                code="METODO_NO_SOPORTADO",
            )

        try:
            response = llamar(f"{settings.PAYPAL_API_BASE_URL}{ruta}", **kwargs)
        except requests.RequestException as error:
            raise PaypalError(
                mensaje_error, code="PAYPAL_CONNECTION_ERROR"
            ) from error

        return response, self._json_seguro(response)

    @staticmethod
    def _lectura_de_captura(data):
        """Los datos de la captura dentro de una orden de PayPal.

        La respuesta anida el cobro en `purchase_units[0].payments.captures[0]`
        y cualquier eslabon puede faltar —una orden aun sin capturar no trae
        ninguno—, asi que se navega con cuidado y se devuelve None en vez de
        reventar. La comision y el neto solo llegan cuando PayPal ya los
        liquido.
        """
        unidades = data.get("purchase_units") or [{}]
        pagos = unidades[0].get("payments") or {}
        capturas = pagos.get("captures") or []
        captura = capturas[0] if capturas else {}
        desglose = captura.get("seller_receivable_breakdown") or {}
        monto = captura.get("amount") or {}
        return {
            "paypal_order_id": data.get("id"),
            "estado": data.get("status"),
            "paypal_capture_id": captura.get("id"),
            "estado_captura": captura.get("status"),
            "monto": monto.get("value"),
            "moneda": monto.get("currency_code"),
            "comision": (desglose.get("paypal_fee") or {}).get("value"),
            "monto_neto": (desglose.get("net_amount") or {}).get("value"),
            "respuesta": data,
        }

    def crear_orden(
        self,
        *,
        referencia,
        request_id,
        monto,
        moneda,
        descripcion,
    ):
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
            # Sin `payment_source`: fijarlo en `paypal` ata la orden a la
            # cartera de PayPal, y entonces el boton de tarjeta que pinta el
            # SDK en el front no puede cobrarla. Las URLs de retorno siguen
            # declaradas porque el SDK cae al camino de redireccion cuando el
            # navegador bloquea su ventana emergente.
            "application_context": {
                "shipping_preference": "NO_SHIPPING",
                "user_action": "PAY_NOW",
                "payment_method": {
                    "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED"
                },
                "return_url": settings.PAYPAL_RETURN_URL,
                "cancel_url": settings.PAYPAL_CANCEL_URL,
            },
        }
        response, data = self._peticion(
            "POST",
            "/v2/checkout/orders",
            request_id=request_id,
            cuerpo=payload,
            mensaje_error="No fue posible crear la orden en PayPal.",
        )
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

    def capturar_orden(self, *, paypal_order_id, request_id):
        """Cobra una orden que el pagador ya aprobo.

        Va con el mismo `PayPal-Request-Id` cada vez que se reintenta: si la
        red se cayo despues de que PayPal cobrara, el reintento devuelve la
        captura original en lugar de cobrar dos veces.

        `ORDER_ALREADY_CAPTURED` no se trata como fallo generico: sale con su
        propio codigo para que quien llama pueda ir a leer la orden y quedarse
        con la captura que ya existe.
        """
        response, data = self._peticion(
            "POST",
            f"/v2/checkout/orders/{paypal_order_id}/capture",
            request_id=request_id,
            cuerpo={},
            mensaje_error="No fue posible confirmar el cobro en PayPal.",
        )

        if response.status_code in (200, 201) and data.get("id"):
            return self._lectura_de_captura(data)

        detalles = data.get("details") or []
        issue = detalles[0].get("issue") if detalles else None
        raise PaypalError(
            "PayPal no pudo cobrar la orden.",
            code=issue or data.get("name", "PAYPAL_CAPTURE_ERROR"),
            status_code=response.status_code,
            payload=data,
        )

    def obtener_orden(self, *, paypal_order_id):
        """Estado de una orden segun PayPal, que es la version que manda."""
        response, data = self._peticion(
            "GET",
            f"/v2/checkout/orders/{paypal_order_id}",
            mensaje_error="No fue posible consultar la orden en PayPal.",
        )

        if response.status_code != 200 or not data.get("id"):
            raise PaypalError(
                "PayPal no devolvio la orden.",
                code=data.get("name", "PAYPAL_GET_ORDER_ERROR"),
                status_code=response.status_code,
                payload=data,
            )

        return self._lectura_de_captura(data)


    def verificar_firma_webhook(self, *, cabeceras, evento):
        """¿PayPal firmó este evento?

        La verificación la hace PayPal contra el id de webhook que dimos de
        alta: el endpoint es público y sin esta comprobación cualquiera podría
        anunciar un cobro que nunca ocurrió y llevarse la mercancía.

        Se falla cerrado. Si falta el id de webhook, si faltan cabeceras o si
        la llamada de verificación no responde, el evento **no** se da por
        bueno.
        """
        if not settings.PAYPAL_WEBHOOK_ID:
            raise PaypalConfigurationError(
                "Falta PAYPAL_WEBHOOK_ID: no se puede verificar el webhook.",
                code="MISSING_PAYPAL_WEBHOOK_ID",
            )

        faltantes = [
            nombre
            for nombre, valor in cabeceras.items()
            if not valor
        ]
        if faltantes:
            return False

        # `cert_url` viene en la petición, así que la manda quien la envía.
        # PayPal la valida de su lado, pero apuntar la verificación a un host
        # ajeno no debe salir ni de aquí.
        cert_url = cabeceras["cert_url"]
        host = urlparse(cert_url).hostname or ""
        if not (host == "paypal.com" or host.endswith(".paypal.com")):
            return False

        response, data = self._peticion(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            cuerpo={
                "auth_algo": cabeceras["auth_algo"],
                "cert_url": cert_url,
                "transmission_id": cabeceras["transmission_id"],
                "transmission_sig": cabeceras["transmission_sig"],
                "transmission_time": cabeceras["transmission_time"],
                "webhook_id": settings.PAYPAL_WEBHOOK_ID,
                "webhook_event": evento,
            },
            mensaje_error="No fue posible verificar la firma del webhook.",
        )

        if response.status_code != 200:
            raise PaypalError(
                "PayPal no pudo verificar la firma del webhook.",
                code=data.get("name", "PAYPAL_VERIFY_WEBHOOK_ERROR"),
                status_code=response.status_code,
                payload=data,
            )

        return data.get("verification_status") == "SUCCESS"


paypal_client = PaypalClient()
