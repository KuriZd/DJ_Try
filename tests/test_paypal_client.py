from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from core.services.paypal import (
    PaypalClient,
    PaypalConfigurationError,
    PaypalError,
)


@override_settings(
    PAYPAL_MODE="sandbox",
    PAYPAL_CLIENT_ID="client-id",
    PAYPAL_CLIENT_SECRET="client-secret",
    PAYPAL_API_BASE_URL="https://api-m.sandbox.paypal.com",
    PAYPAL_RETURN_URL="https://frontend.test/paypal/return",
    PAYPAL_CANCEL_URL="https://frontend.test/paypal/cancel",
    PAYPAL_HTTP_TIMEOUT=5,
)
class PaypalClientTest(SimpleTestCase):
    @staticmethod
    def response(status_code, data):
        respuesta = Mock()
        respuesta.status_code = status_code
        respuesta.json.return_value = data
        return respuesta

    @patch("core.services.paypal.requests.post")
    def test_autentica_y_crea_orden_v2(self, post):
        post.side_effect = [
            self.response(
                200,
                {"access_token": "access-token", "expires_in": 3600},
            ),
            self.response(
                201,
                {
                    "id": "5O190127TN364715T",
                    "status": "CREATED",
                    "links": [
                        {
                            "rel": "payer-action",
                            "href": "https://paypal.test/approve",
                        }
                    ],
                },
            ),
        ]

        resultado = PaypalClient().crear_orden(
            referencia="PAY-20260826-ABC",
            request_id="request-id",
            monto=Decimal("499.00"),
            moneda="MXN",
            descripcion="Reporte psicométrico",
        )

        self.assertEqual(resultado["paypal_order_id"], "5O190127TN364715T")
        self.assertEqual(resultado["approval_url"], "https://paypal.test/approve")
        llamada = post.call_args_list[1]
        self.assertTrue(llamada.args[0].endswith("/v2/checkout/orders"))
        self.assertEqual(llamada.kwargs["headers"]["PayPal-Request-Id"], "request-id")
        self.assertEqual(
            llamada.kwargs["json"]["purchase_units"][0]["amount"],
            {"currency_code": "MXN", "value": "499.00"},
        )
        # La orden no se ata a la cartera de PayPal: si lo hiciera, el boton de
        # tarjeta del SDK quedaria sin poder cobrarla.
        self.assertNotIn("payment_source", llamada.kwargs["json"])
        contexto = llamada.kwargs["json"]["application_context"]
        self.assertEqual(contexto["return_url"], "https://frontend.test/paypal/return")
        self.assertEqual(contexto["cancel_url"], "https://frontend.test/paypal/cancel")

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")
    def test_rechaza_configuracion_sin_credenciales(self):
        with self.assertRaises(PaypalConfigurationError):
            PaypalClient().crear_orden(
                referencia="PAY-1",
                request_id="request-id",
                monto=Decimal("499.00"),
                moneda="MXN",
                descripcion="Reporte",
            )

    @patch("core.services.paypal.requests.post")
    def test_convierte_timeout_en_error_controlado(self, post):
        post.side_effect = requests.Timeout()

        with self.assertRaises(PaypalError) as contexto:
            PaypalClient().crear_orden(
                referencia="PAY-1",
                request_id="request-id",
                monto=Decimal("499.00"),
                moneda="MXN",
                descripcion="Reporte",
            )

        self.assertEqual(contexto.exception.code, "PAYPAL_CONNECTION_ERROR")

    @staticmethod
    def respuesta_capturada():
        return {
            "id": "5O190127TN364715T",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "3C679366HH908993F",
                                "status": "COMPLETED",
                                "amount": {
                                    "currency_code": "MXN",
                                    "value": "2190.00",
                                },
                                "seller_receivable_breakdown": {
                                    "paypal_fee": {
                                        "currency_code": "MXN",
                                        "value": "80.31",
                                    },
                                    "net_amount": {
                                        "currency_code": "MXN",
                                        "value": "2109.69",
                                    },
                                },
                            }
                        ]
                    }
                }
            ],
        }

    @patch("core.services.paypal.requests.post")
    def test_captura_devuelve_el_cobro_y_su_desglose(self, post):
        post.side_effect = [
            self.response(200, {"access_token": "access-token", "expires_in": 3600}),
            self.response(201, self.respuesta_capturada()),
        ]

        resultado = PaypalClient().capturar_orden(
            paypal_order_id="5O190127TN364715T",
            request_id="request-id",
        )

        self.assertEqual(resultado["estado"], "COMPLETED")
        self.assertEqual(resultado["estado_captura"], "COMPLETED")
        self.assertEqual(resultado["paypal_capture_id"], "3C679366HH908993F")
        self.assertEqual(resultado["monto"], "2190.00")
        self.assertEqual(resultado["comision"], "80.31")
        self.assertEqual(resultado["monto_neto"], "2109.69")
        llamada = post.call_args_list[1]
        self.assertTrue(
            llamada.args[0].endswith(
                "/v2/checkout/orders/5O190127TN364715T/capture"
            )
        )
        # El mismo request id en cada reintento: es lo que impide que un
        # reintento tras un corte de red cobre dos veces.
        self.assertEqual(
            llamada.kwargs["headers"]["PayPal-Request-Id"], "request-id"
        )

    @patch("core.services.paypal.requests.post")
    def test_orden_ya_capturada_sale_con_su_propio_codigo(self, post):
        post.side_effect = [
            self.response(200, {"access_token": "access-token", "expires_in": 3600}),
            self.response(
                422,
                {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "ORDER_ALREADY_CAPTURED"}],
                },
            ),
        ]

        with self.assertRaises(PaypalError) as contexto:
            PaypalClient().capturar_orden(
                paypal_order_id="5O190127TN364715T",
                request_id="request-id",
            )

        self.assertEqual(contexto.exception.code, "ORDER_ALREADY_CAPTURED")

    @patch("core.services.paypal.requests.get")
    @patch("core.services.paypal.requests.post")
    def test_consultar_orden_lee_la_captura_que_ya_existe(self, post, get):
        post.return_value = self.response(
            200, {"access_token": "access-token", "expires_in": 3600}
        )
        get.return_value = self.response(200, self.respuesta_capturada())

        resultado = PaypalClient().obtener_orden(
            paypal_order_id="5O190127TN364715T"
        )

        self.assertEqual(resultado["paypal_capture_id"], "3C679366HH908993F")
        self.assertTrue(
            get.call_args.args[0].endswith(
                "/v2/checkout/orders/5O190127TN364715T"
            )
        )

    @patch("core.services.paypal.requests.post")
    def test_orden_sin_capturar_no_inventa_datos_de_cobro(self, post):
        post.side_effect = [
            self.response(200, {"access_token": "access-token", "expires_in": 3600}),
            self.response(
                201,
                {
                    "id": "5O190127TN364715T",
                    "status": "COMPLETED",
                    "purchase_units": [{}],
                },
            ),
        ]

        resultado = PaypalClient().capturar_orden(
            paypal_order_id="5O190127TN364715T",
            request_id="request-id",
        )

        self.assertIsNone(resultado["paypal_capture_id"])
        self.assertIsNone(resultado["estado_captura"])
        self.assertIsNone(resultado["monto"])

    @staticmethod
    def cabeceras(cert_url="https://api.sandbox.paypal.com/v1/notifications/certs/CERT-abc"):
        return {
            "auth_algo": "SHA256withRSA",
            "cert_url": cert_url,
            "transmission_id": "b1c02710-1234-11ee-9c0d-0f0f0f0f0f0f",
            "transmission_sig": "firma-en-base64",
            "transmission_time": "2026-08-27T12:00:00Z",
        }

    @override_settings(PAYPAL_WEBHOOK_ID="WH-PRUEBA")
    @patch("core.services.paypal.requests.post")
    def test_firma_valida_se_acepta(self, post):
        post.side_effect = [
            self.response(200, {"access_token": "access-token", "expires_in": 3600}),
            self.response(200, {"verification_status": "SUCCESS"}),
        ]

        valida = PaypalClient().verificar_firma_webhook(
            cabeceras=self.cabeceras(),
            evento={"id": "WH-EVENTO-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"},
        )

        self.assertTrue(valida)
        cuerpo = post.call_args_list[1].kwargs["json"]
        self.assertEqual(cuerpo["webhook_id"], "WH-PRUEBA")
        self.assertEqual(cuerpo["webhook_event"]["id"], "WH-EVENTO-1")

    @override_settings(PAYPAL_WEBHOOK_ID="WH-PRUEBA")
    @patch("core.services.paypal.requests.post")
    def test_firma_invalida_se_rechaza(self, post):
        post.side_effect = [
            self.response(200, {"access_token": "access-token", "expires_in": 3600}),
            self.response(200, {"verification_status": "FAILURE"}),
        ]

        self.assertFalse(
            PaypalClient().verificar_firma_webhook(
                cabeceras=self.cabeceras(), evento={"id": "WH-EVENTO-1"}
            )
        )

    @override_settings(PAYPAL_WEBHOOK_ID="WH-PRUEBA")
    @patch("core.services.paypal.requests.post")
    def test_no_verifica_contra_un_host_ajeno(self, post):
        # `cert_url` la manda quien envía la petición. Apuntarla a un servidor
        # propio sería la forma de que el atacante firmara sus propios avisos.
        valida = PaypalClient().verificar_firma_webhook(
            cabeceras=self.cabeceras(cert_url="https://paypal.com.atacante.test/cert"),
            evento={"id": "WH-EVENTO-1"},
        )

        self.assertFalse(valida)
        post.assert_not_called()

    @override_settings(PAYPAL_WEBHOOK_ID="WH-PRUEBA")
    @patch("core.services.paypal.requests.post")
    def test_sin_cabeceras_de_firma_no_hay_nada_que_verificar(self, post):
        cabeceras = self.cabeceras()
        cabeceras["transmission_sig"] = None

        self.assertFalse(
            PaypalClient().verificar_firma_webhook(
                cabeceras=cabeceras, evento={"id": "WH-EVENTO-1"}
            )
        )
        post.assert_not_called()

    @override_settings(PAYPAL_WEBHOOK_ID="")
    @patch("core.services.paypal.requests.post")
    def test_sin_webhook_id_falla_cerrado(self, post):
        # No devuelve False ni True: revienta. Un webhook que no se puede
        # verificar es un problema de configuración, no un evento inválido, y
        # tratarlo como inválido lo dejaría pasar en silencio si alguien
        # cambiara el criterio.
        with self.assertRaises(PaypalConfigurationError):
            PaypalClient().verificar_firma_webhook(
                cabeceras=self.cabeceras(), evento={"id": "WH-EVENTO-1"}
            )
        post.assert_not_called()
