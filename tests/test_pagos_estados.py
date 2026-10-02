"""Regresiones de respuestas de captura que llegan fuera de orden."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from core.models import EstadoCompraPaquete, EstadoPagoPaypal, OrdenPagoPaypal, TransaccionPagoPaypal
from core.services import pagos
from core.services.paypal import PaypalError
from tests import production_audit_checks as audit
from tests import test_qa_fixes as qa_fixtures


class PaymentStateTests(audit.ProductionPaymentChecks):
    def terminal_order(self, state):
        order, purchase = self.helper.crear_orden()
        with patch('core.services.avisos.enviar_comprobante'):
            pagos.aplicar_resultado_de_captura(
                order, self.helper.orden_en_paypal(), estado_anterior=order.estado,
            )
        if state == EstadoPagoPaypal.REFUNDED:
            refund = self.helper.evento('PAYMENT.CAPTURE.REFUNDED')
            refund['resource']['id'] = 'REFUND-STATE-TEST'
            pagos._reembolsar_orden(order, refund, timezone.now())
        order.refresh_from_db()
        purchase.refresh_from_db()
        return order, purchase

    def assert_preserved(self, order, purchase, operation):
        before_order = (order.estado, order.pagado_en, order.actualizado_en,
                        order.codigo_error, order.respuesta_proveedor)
        before_purchase = (purchase.estado, purchase.pagada_en, purchase.vigente_hasta)
        before_transactions = list(TransaccionPagoPaypal.objects.filter(
            orden=order).values('id', 'estado', 'actualizado_en'))
        with patch('core.services.avisos.enviar_comprobante') as receipt:
            operation()
            receipt.assert_not_called()
        order.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(before_order, (order.estado, order.pagado_en, order.actualizado_en,
                                      order.codigo_error, order.respuesta_proveedor))
        self.assertEqual(before_purchase, (purchase.estado, purchase.pagada_en, purchase.vigente_hasta))
        self.assertEqual(before_transactions, list(TransaccionPagoPaypal.objects.filter(
            orden=order).values('id', 'estado', 'actualizado_en')))

    def check_late_responses(self, state):
        order, purchase = self.terminal_order(state)
        for capture_state in ('PENDING', 'COMPLETED'):
            with self.subTest(capture_state=capture_state):
                self.assert_preserved(order, purchase, lambda: pagos.aplicar_resultado_de_captura(
                    order, self.helper.orden_en_paypal(capture_state),
                    estado_anterior=EstadoPagoPaypal.APPROVED,
                ))
        self.assert_preserved(order, purchase, lambda: pagos._denegar_orden(order, timezone.now()))
        for code in ('PAYPAL_CONNECTION_ERROR', 'PAYPAL_CAPTURE_DENIED'):
            with self.subTest(code=code):
                self.assert_preserved(order, purchase, lambda: pagos._guardar_fallo_al_capturar(
                    order, PaypalError('Late error', code=code),
                ))

    def test_completed_order_preserves_payment_and_delivery(self):
        self.check_late_responses(EstadoPagoPaypal.COMPLETED)

    def test_refunded_order_preserves_refund_and_revocation(self):
        self.check_late_responses(EstadoPagoPaypal.REFUNDED)


class ConcurrentPaymentStateTests(TransactionTestCase):
    def test_refund_commits_while_capture_request_is_in_flight(self):
        helper, purchase = qa_fixtures.QAConcurrentFixesTests.webhook_fixture(self)
        order = OrdenPagoPaypal.objects.get(compra=purchase)
        result = helper.orden_en_paypal()
        pending = dict(result, estado_captura='PENDING')
        pagos.aplicar_resultado_de_captura(order, pending, estado_anterior=order.estado)
        requested, release = Event(), Event()

        def provider(**kwargs):
            requested.set()
            if not release.wait(timeout=15):
                raise TimeoutError('Refund did not finish')
            return result

        def capture():
            close_old_connections()
            try:
                return pagos.capturar_pago_paypal(orden=order)
            finally:
                close_old_connections()

        with patch('core.services.pagos.paypal_client.capturar_orden', side_effect=provider):
            with patch('core.services.avisos.enviar_comprobante') as receipt:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(capture)
                    try:
                        self.assertTrue(requested.wait(timeout=15))
                        refund = helper.evento('PAYMENT.CAPTURE.REFUNDED')
                        refund['resource']['id'] = f'REFUND-{order.pk}'
                        pagos._reembolsar_orden(order, refund, timezone.now())
                    finally:
                        release.set()
                    returned_order, delivered = future.result(timeout=15)
                receipt.assert_not_called()
        order.refresh_from_db()
        purchase.refresh_from_db()
        self.assertFalse(delivered)
        self.assertEqual(returned_order.estado, EstadoPagoPaypal.REFUNDED)
        self.assertEqual(order.estado, EstadoPagoPaypal.REFUNDED)
        self.assertEqual(purchase.estado, EstadoCompraPaquete.REEMBOLSADA)
