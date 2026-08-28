from importlib import import_module

from django.test import SimpleTestCase

from core.models import (
    EstadoPagoPaypal,
    EventoPagoPaypal,
    OrdenPagoPaypal,
    TransaccionPagoPaypal,
)


CREAR_PAGOS_PAYPAL_SQL = import_module(
    "core.migrations.0017_infraestructura_pagos_paypal"
).CREAR_PAGOS_PAYPAL_SQL


class InfraestructuraPagosPaypalTest(SimpleTestCase):
    def test_estados_requeridos(self):
        self.assertEqual(
            set(EstadoPagoPaypal.values),
            {
                "PENDING",
                "CREATED",
                "APPROVED",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "REFUNDED",
            },
        )

    def test_orden_conserva_identidad_importes_y_fechas(self):
        campos = {campo.name for campo in OrdenPagoPaypal._meta.fields}
        self.assertTrue(
            {
                "comprador",
                "reporte",
                "referencia_evaluacion_externa",
                "referencia_interna",
                "paypal_order_id",
                "monto",
                "moneda",
                "estado",
                "creado_en",
                "actualizado_en",
                "pagado_en",
            }.issubset(campos)
        )

    def test_transaccion_conserva_capture_y_respuesta_de_paypal(self):
        campos = {campo.name for campo in TransaccionPagoPaypal._meta.fields}
        self.assertTrue(
            {
                "orden",
                "paypal_capture_id",
                "monto",
                "moneda",
                "estado",
                "respuesta_proveedor",
                "creado_en",
                "actualizado_en",
            }.issubset(campos)
        )

    def test_evento_conserva_payload_y_estado_de_procesamiento(self):
        campos = {campo.name for campo in EventoPagoPaypal._meta.fields}
        self.assertTrue(
            {
                "paypal_event_id",
                "tipo_evento",
                "origen",
                "estado_anterior",
                "estado_nuevo",
                "payload",
                "procesado",
                "mensaje_error",
                "recibido_en",
                "procesado_en",
            }.issubset(campos)
        )

    def test_sql_impide_ordenes_activas_duplicadas(self):
        self.assertIn("orden_pago_paypal_activa_unica", CREAR_PAGOS_PAYPAL_SQL)
        self.assertIn(
            "'PENDING', 'CREATED', 'APPROVED', 'COMPLETED'",
            CREAR_PAGOS_PAYPAL_SQL,
        )
