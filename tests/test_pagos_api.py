import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Aspirante,
    EstadoPagoPaypal,
    EventoPagoPaypal,
    OrdenPagoPaypal,
    OrigenReportePsicometrico,
    ReportePsicometrico,
    Usuario,
)
from core.services.paypal import PaypalError


class CrearOrdenPaypalApiTest(TestCase):
    def setUp(self):
        self.comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.aspirante = Aspirante.objects.get(usuario=self.comprador)
        ahora = timezone.now()
        self.reporte = ReportePsicometrico.objects.create(
            id=uuid.uuid4(),
            aspirante=self.aspirante,
            referencia_evaluacion_externa="EVAL-PAY-001",
            nombre_original="reporte.pdf",
            archivo="reportes_psicometricos/prueba/reporte.pdf",
            mime_type="application/pdf",
            tamano_bytes=128,
            checksum_sha256="a" * 64,
            precio=Decimal("499.00"),
            moneda="MXN",
            origen=OrigenReportePsicometrico.PLATAFORMA,
            disponible_para_compra=True,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.comprador)

    @staticmethod
    def respuesta_paypal():
        return {
            "paypal_order_id": "5O190127TN364715T",
            "estado": "CREATED",
            "approval_url": (
                "https://www.sandbox.paypal.com/checkoutnow?token="
                "5O190127TN364715T"
            ),
            "respuesta": {
                "id": "5O190127TN364715T",
                "status": "CREATED",
                "links": [],
            },
        }

    def url(self):
        return reverse("api:ordenes-paypal")

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_crea_orden_con_precio_del_backend(self, crear_orden):
        crear_orden.return_value = self.respuesta_paypal()

        respuesta = self.cliente.post(
            self.url(),
            {"reporte_id": str(self.reporte.id), "monto": "0.01"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="checkout-001",
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data["monto"], "499.00")
        self.assertEqual(respuesta.data["moneda"], "MXN")
        self.assertEqual(respuesta.data["estado"], EstadoPagoPaypal.CREATED)
        self.assertFalse(respuesta.data["reutilizada"])
        self.assertEqual(
            crear_orden.call_args.kwargs["monto"], Decimal("499.00")
        )

        orden = OrdenPagoPaypal.objects.get(reporte=self.reporte)
        self.assertEqual(orden.comprador, self.comprador)
        self.assertEqual(orden.paypal_order_id, "5O190127TN364715T")
        self.assertTrue(
            EventoPagoPaypal.objects.filter(
                orden=orden, tipo_evento="ORDER.CREATED"
            ).exists()
        )

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_reutiliza_orden_activa(self, crear_orden):
        crear_orden.return_value = self.respuesta_paypal()
        payload = {"reporte_id": str(self.reporte.id)}

        primera = self.cliente.post(self.url(), payload, format="json")
        segunda = self.cliente.post(self.url(), payload, format="json")

        self.assertEqual(primera.status_code, 201)
        self.assertEqual(segunda.status_code, 200)
        self.assertTrue(segunda.data["reutilizada"])
        self.assertEqual(OrdenPagoPaypal.objects.count(), 1)
        crear_orden.assert_called_once()

    def test_rechaza_reporte_ajeno(self):
        ajeno = ReportePsicometrico.objects.create(
            id=uuid.uuid4(),
            aspirante=Aspirante.objects.exclude(pk=self.aspirante.pk).first(),
            nombre_original="ajeno.pdf",
            archivo="reportes_psicometricos/prueba/ajeno.pdf",
            mime_type="application/pdf",
            tamano_bytes=128,
            checksum_sha256="b" * 64,
            precio=Decimal("499.00"),
            moneda="MXN",
            origen=OrigenReportePsicometrico.PLATAFORMA,
            disponible_para_compra=True,
            creado_en=timezone.now(),
            actualizado_en=timezone.now(),
        )

        respuesta = self.cliente.post(
            self.url(), {"reporte_id": str(ajeno.id)}, format="json"
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(OrdenPagoPaypal.objects.exists())

    def test_rechaza_reporte_no_comercializable(self):
        self.reporte.disponible_para_compra = False
        self.reporte.save(update_fields=["disponible_para_compra"])

        respuesta = self.cliente.post(
            self.url(), {"reporte_id": str(self.reporte.id)}, format="json"
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(OrdenPagoPaypal.objects.exists())

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_timeout_paypal_deja_orden_pendiente_para_recuperarla(self, crear_orden):
        crear_orden.side_effect = PaypalError(
            "PayPal no disponible.",
            code="PAYPAL_CONNECTION_ERROR",
        )

        respuesta = self.cliente.post(
            self.url(), {"reporte_id": str(self.reporte.id)}, format="json"
        )

        self.assertEqual(respuesta.status_code, 502)
        orden = OrdenPagoPaypal.objects.get(reporte=self.reporte)
        self.assertEqual(orden.estado, EstadoPagoPaypal.PENDING)
        self.assertEqual(orden.codigo_error, "PAYPAL_CONNECTION_ERROR")
        self.assertTrue(
            orden.eventos.filter(tipo_evento="ORDER.CREATE_FAILED").exists()
        )

    def test_requiere_autenticacion(self):
        respuesta = APIClient().post(
            self.url(), {"reporte_id": str(self.reporte.id)}, format="json"
        )

        self.assertEqual(respuesta.status_code, 401)
