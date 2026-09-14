import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    CompraPaquetePsicometrico,
    EstadoCompraPaquete,
    EstadoPagoPaypal,
    EventoPagoPaypal,
    OrdenPagoPaypal,
    PaquetePsicometrico,
    TipoTransaccionPaypal,
    TransaccionPagoPaypal,
    Usuario,
)
from core.services.paypal import PaypalConfigurationError


PAYPAL_ORDER_ID = "5O190127TN364715T"
CAPTURE_ID = "3C679366HH908993F"


class WebhookPaypalTest(TestCase):
    """El webhook es el único endpoint público que mueve dinero."""

    def setUp(self):
        self.comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.paquete = PaquetePsicometrico.objects.get(clave="perfil")
        self.cliente = APIClient()

    # -- armado -----------------------------------------------------------

    def crear_orden(self, estado=EstadoPagoPaypal.APPROVED):
        ahora = timezone.now()
        compra = CompraPaquetePsicometrico.objects.create(
            id=uuid.uuid4(),
            comprador=self.comprador,
            paquete=self.paquete,
            paquete_nombre=self.paquete.nombre,
            cantidad_pruebas=3,
            monto=Decimal("2190.00"),
            moneda="MXN",
            estado=EstadoCompraPaquete.PENDIENTE,
            creditos_totales=3,
            creditos_consumidos=0,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        orden = OrdenPagoPaypal.objects.create(
            id=uuid.uuid4(),
            referencia_interna="PAY-20260827-WEBHOOK00001",
            comprador=self.comprador,
            compra=compra,
            paypal_order_id=PAYPAL_ORDER_ID,
            paypal_request_id=str(uuid.uuid4()),
            monto=Decimal("2190.00"),
            moneda="MXN",
            estado=estado,
            pagado_en=(
                ahora if estado == EstadoPagoPaypal.COMPLETED else None
            ),
            creado_en=ahora,
            actualizado_en=ahora,
        )
        return orden, compra

    def capturar_en_pendiente(self, orden):
        """El cobro que PayPal retuvo para revisar: anotado y sin entregar."""
        ahora = timezone.now()
        return TransaccionPagoPaypal.objects.create(
            id=uuid.uuid4(),
            orden=orden,
            tipo=TipoTransaccionPaypal.CAPTURE,
            paypal_capture_id=CAPTURE_ID,
            monto=Decimal("2190.00"),
            moneda="MXN",
            estado=EstadoPagoPaypal.PENDING,
            procesada_en=ahora,
            creado_en=ahora,
            actualizado_en=ahora,
        )

    @staticmethod
    def evento(tipo, *, event_id="WH-1", recurso=None):
        return {
            "id": event_id,
            "event_type": tipo,
            "resource": recurso
            if recurso is not None
            else {
                "id": CAPTURE_ID,
                "status": "COMPLETED",
                "amount": {"currency_code": "MXN", "value": "2190.00"},
                "supplementary_data": {
                    "related_ids": {"order_id": PAYPAL_ORDER_ID}
                },
            },
        }

    @staticmethod
    def orden_en_paypal(estado_captura="COMPLETED"):
        return {
            "paypal_order_id": PAYPAL_ORDER_ID,
            "estado": "COMPLETED",
            "paypal_capture_id": CAPTURE_ID,
            "estado_captura": estado_captura,
            "monto": "2190.00",
            "moneda": "MXN",
            "comision": "80.31",
            "monto_neto": "2109.69",
            "respuesta": {"id": PAYPAL_ORDER_ID, "status": "COMPLETED"},
        }

    def url(self):
        return reverse("api:webhook-paypal")

    def enviar(self, evento):
        return self.cliente.post(self.url(), evento, format="json")

    # -- firma ------------------------------------------------------------

    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_un_evento_sin_firma_valida_no_toca_nada(self, verificar):
        # Sin esta comprobación cualquiera podría anunciar un cobro que no
        # existe y llevarse las pruebas.
        verificar.return_value = False
        orden, compra = self.crear_orden()

        respuesta = self.enviar(self.evento("PAYMENT.CAPTURE.COMPLETED"))

        self.assertEqual(respuesta.status_code, 400)
        orden.refresh_from_db()
        compra.refresh_from_db()
        self.assertEqual(orden.estado, EstadoPagoPaypal.APPROVED)
        self.assertEqual(compra.estado, EstadoCompraPaquete.PENDIENTE)
        self.assertFalse(EventoPagoPaypal.objects.exists())

    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_sin_webhook_id_pide_a_paypal_que_reintente(self, verificar):
        verificar.side_effect = PaypalConfigurationError(
            "Falta PAYPAL_WEBHOOK_ID: no se puede verificar el webhook.",
            code="MISSING_PAYPAL_WEBHOOK_ID",
        )
        self.crear_orden()

        respuesta = self.enviar(self.evento("PAYMENT.CAPTURE.COMPLETED"))

        # 503 y no 400: el evento es bueno, el que está mal configurado somos
        # nosotros, así que conviene que PayPal vuelva a intentarlo.
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.data["code"], "MISSING_PAYPAL_WEBHOOK_ID")

    # -- cobro liberado ---------------------------------------------------

    @patch("core.services.pagos.paypal_client.obtener_orden")
    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_un_cobro_liberado_entrega_los_creditos(self, verificar, obtener):
        # Es para lo que existe el webhook: PayPal retuvo el cobro, lo libera
        # después, y sin este aviso la compra se quedaba pendiente para siempre.
        verificar.return_value = True
        obtener.return_value = self.orden_en_paypal()
        orden, compra = self.crear_orden()
        transaccion = self.capturar_en_pendiente(orden)

        respuesta = self.enviar(self.evento("PAYMENT.CAPTURE.COMPLETED"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["resultado"], "procesado")

        orden.refresh_from_db()
        compra.refresh_from_db()
        transaccion.refresh_from_db()
        self.assertEqual(orden.estado, EstadoPagoPaypal.COMPLETED)
        self.assertIsNotNone(orden.pagado_en)
        self.assertEqual(compra.estado, EstadoCompraPaquete.PAGADA)
        self.assertEqual(compra.creditos_disponibles, 3)
        self.assertIsNotNone(compra.vigente_hasta)

        # La captura no se duplica: la misma fila pasa de PENDING a COMPLETED
        # y recoge la comisión, que sólo aparece cuando el dinero se liquida.
        self.assertEqual(TransaccionPagoPaypal.objects.count(), 1)
        self.assertEqual(transaccion.estado, EstadoPagoPaypal.COMPLETED)
        self.assertEqual(transaccion.comision, Decimal("80.31"))
        self.assertEqual(transaccion.monto_neto, Decimal("2109.69"))

    @patch("core.services.pagos.paypal_client.obtener_orden")
    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_el_mismo_aviso_dos_veces_entrega_una_vez(self, verificar, obtener):
        # PayPal reintenta hasta recibir un 2xx, así que el mismo cobro llega
        # varias veces por diseño.
        verificar.return_value = True
        obtener.return_value = self.orden_en_paypal()
        orden, compra = self.crear_orden()
        self.capturar_en_pendiente(orden)

        primera = self.enviar(self.evento("PAYMENT.CAPTURE.COMPLETED"))
        segunda = self.enviar(self.evento("PAYMENT.CAPTURE.COMPLETED"))

        self.assertEqual(primera.data["resultado"], "procesado")
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.data["resultado"], "duplicado")
        self.assertEqual(obtener.call_count, 1)
        self.assertEqual(TransaccionPagoPaypal.objects.count(), 1)

        compra.refresh_from_db()
        self.assertEqual(compra.creditos_totales, 3)

    @patch("core.services.pagos.paypal_client.obtener_orden")
    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_un_cobro_aun_retenido_no_entrega_nada(self, verificar, obtener):
        verificar.return_value = True
        obtener.return_value = self.orden_en_paypal(estado_captura="PENDING")
        orden, compra = self.crear_orden()

        respuesta = self.enviar(self.evento("PAYMENT.CAPTURE.PENDING"))

        self.assertEqual(respuesta.status_code, 200)
        compra.refresh_from_db()
        self.assertEqual(compra.estado, EstadoCompraPaquete.PENDIENTE)

    # -- red de seguridad -------------------------------------------------

    @patch("core.services.pagos.paypal_client.capturar_orden")
    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_una_orden_aprobada_se_cobra_aunque_el_navegador_muera(
        self, verificar, capturar
    ):
        # Si el navegador se cierra entre la aprobación y el cobro, la persona
        # cree que pagó y el dinero nunca se tomó. Este aviso lo cierra.
        verificar.return_value = True
        capturar.return_value = self.orden_en_paypal()
        orden, compra = self.crear_orden(estado=EstadoPagoPaypal.CREATED)

        respuesta = self.enviar(
            self.evento(
                "CHECKOUT.ORDER.APPROVED",
                recurso={
                    "id": PAYPAL_ORDER_ID,
                    "status": "APPROVED",
                    "purchase_units": [
                        {"custom_id": "PAY-20260827-WEBHOOK00001"}
                    ],
                },
            )
        )

        self.assertEqual(respuesta.status_code, 200)
        capturar.assert_called_once()
        orden.refresh_from_db()
        compra.refresh_from_db()
        self.assertEqual(orden.estado, EstadoPagoPaypal.COMPLETED)
        self.assertEqual(compra.estado, EstadoCompraPaquete.PAGADA)

    @patch("core.services.pagos.paypal_client.capturar_orden")
    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_una_orden_ya_cobrada_por_el_front_no_se_cobra_otra_vez(
        self, verificar, capturar
    ):
        verificar.return_value = True
        orden, _ = self.crear_orden(estado=EstadoPagoPaypal.COMPLETED)

        respuesta = self.enviar(self.evento("CHECKOUT.ORDER.APPROVED"))

        self.assertEqual(respuesta.status_code, 200)
        capturar.assert_not_called()

    # -- rechazos y devoluciones ------------------------------------------

    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_un_cobro_denegado_cierra_la_compra(self, verificar):
        verificar.return_value = True
        orden, compra = self.crear_orden()

        respuesta = self.enviar(self.evento("PAYMENT.CAPTURE.DENIED"))

        self.assertEqual(respuesta.status_code, 200)
        orden.refresh_from_db()
        compra.refresh_from_db()
        self.assertEqual(orden.estado, EstadoPagoPaypal.FAILED)
        self.assertEqual(compra.estado, EstadoCompraPaquete.CANCELADA)

    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_una_devolucion_retira_lo_entregado(self, verificar):
        verificar.return_value = True
        orden, compra = self.crear_orden(estado=EstadoPagoPaypal.COMPLETED)
        captura = self.capturar_en_pendiente(orden)
        TransaccionPagoPaypal.objects.filter(pk=captura.pk).update(
            estado=EstadoPagoPaypal.COMPLETED
        )
        CompraPaquetePsicometrico.objects.filter(pk=compra.pk).update(
            estado=EstadoCompraPaquete.PAGADA, pagada_en=timezone.now()
        )

        respuesta = self.enviar(
            self.evento(
                "PAYMENT.CAPTURE.REFUNDED",
                recurso={
                    "id": "8RT12345KL678901M",
                    "status": "COMPLETED",
                    "amount": {"currency_code": "MXN", "value": "2190.00"},
                    "supplementary_data": {
                        "related_ids": {
                            "order_id": PAYPAL_ORDER_ID,
                            "capture_id": CAPTURE_ID,
                        }
                    },
                },
            )
        )

        self.assertEqual(respuesta.status_code, 200)
        orden.refresh_from_db()
        compra.refresh_from_db()
        self.assertEqual(orden.estado, EstadoPagoPaypal.REFUNDED)
        self.assertEqual(compra.estado, EstadoCompraPaquete.REEMBOLSADA)

        devolucion = TransaccionPagoPaypal.objects.get(
            tipo=TipoTransaccionPaypal.REFUND
        )
        self.assertEqual(devolucion.paypal_refund_id, "8RT12345KL678901M")
        # La base exige que un reembolso diga de qué captura sale.
        self.assertEqual(devolucion.paypal_capture_id, CAPTURE_ID)

    # -- eventos que no llevan a ningún lado ------------------------------

    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_un_evento_que_no_atendemos_se_archiva_sin_error(self, verificar):
        # Contestar un error haría que PayPal lo reintentara durante días.
        verificar.return_value = True

        respuesta = self.enviar(
            self.evento("BILLING.SUBSCRIPTION.CREATED", event_id="WH-OTRO")
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["resultado"], "ignorado")
        self.assertTrue(
            EventoPagoPaypal.objects.filter(paypal_event_id="WH-OTRO").exists()
        )

    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_un_evento_de_orden_desconocida_queda_anotado(self, verificar):
        verificar.return_value = True

        respuesta = self.enviar(
            self.evento(
                "PAYMENT.CAPTURE.COMPLETED",
                recurso={
                    "id": "OTRA-CAPTURA",
                    "supplementary_data": {
                        "related_ids": {"order_id": "ORDEN-QUE-NO-ES-NUESTRA"}
                    },
                },
            )
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["resultado"], "sin-orden")
        anotado = EventoPagoPaypal.objects.get(paypal_event_id="WH-1")
        self.assertFalse(anotado.procesado)

    @patch("core.services.pagos.paypal_client.obtener_orden")
    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_encuentra_la_orden_por_la_referencia_que_pusimos(
        self, verificar, obtener
    ):
        # `custom_id` lo puso el backend al crear la orden. Es lo único que no
        # depende de que PayPal adjunte sus ids relacionados.
        verificar.return_value = True
        obtener.return_value = self.orden_en_paypal()
        orden, compra = self.crear_orden()
        self.capturar_en_pendiente(orden)

        respuesta = self.enviar(
            self.evento(
                "PAYMENT.CAPTURE.COMPLETED",
                recurso={
                    "id": CAPTURE_ID,
                    "status": "COMPLETED",
                    "custom_id": "PAY-20260827-WEBHOOK00001",
                    "amount": {"currency_code": "MXN", "value": "2190.00"},
                },
            )
        )

        self.assertEqual(respuesta.data["resultado"], "procesado")
        compra.refresh_from_db()
        self.assertEqual(compra.estado, EstadoCompraPaquete.PAGADA)

    @patch("core.services.pagos.paypal_client.obtener_orden")
    @patch("core.services.pagos.paypal_client.verificar_firma_webhook")
    def test_un_importe_distinto_no_entrega_y_queda_para_revision(
        self, verificar, obtener
    ):
        verificar.return_value = True
        resultado = self.orden_en_paypal()
        resultado["monto"] = "1.00"
        obtener.return_value = resultado
        orden, compra = self.crear_orden()

        respuesta = self.enviar(self.evento("PAYMENT.CAPTURE.COMPLETED"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["resultado"], "sin-efecto")

        orden.refresh_from_db()
        compra.refresh_from_db()
        self.assertNotEqual(orden.estado, EstadoPagoPaypal.COMPLETED)
        self.assertEqual(compra.estado, EstadoCompraPaquete.PENDIENTE)
        self.assertEqual(orden.codigo_error, "MONTO_CAPTURADO_DISTINTO")

    def test_no_pide_sesion(self):
        # PayPal no trae credenciales nuestras: lo que autentica el aviso es
        # la firma, no un token.
        with patch(
            "core.services.pagos.paypal_client.verificar_firma_webhook",
            return_value=False,
        ):
            respuesta = self.enviar(self.evento("PAYMENT.CAPTURE.COMPLETED"))

        self.assertNotEqual(respuesta.status_code, 401)
        self.assertNotEqual(respuesta.status_code, 403)
