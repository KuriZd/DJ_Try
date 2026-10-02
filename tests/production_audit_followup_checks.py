"""Reproducciones explícitas del seguimiento de auditoría del 2 de octubre.

Fuera de discovery habitual hasta corregir los nuevos hallazgos.
Ejecutar solamente con scripts/qa_audit_run.py.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, OperationalError, close_old_connections
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core.models import EstadoCompraPaquete, OrdenPagoPaypal, TransaccionPagoPaypal
from core.services import pagos
from tests import test_pagos_webhook as fixtures
from tests import test_qa_fixes as qa_fixtures


class PaymentFollowupChecks(TestCase):
    def setUp(self):
        self.helper = fixtures.WebhookPaypalTest()
        self.helper.setUp()

    def test_PROD06_missing_amount_and_currency_must_not_deliver(self):
        order, purchase = self.helper.crear_orden()
        response = self.helper.orden_en_paypal()
        response.pop('monto')
        response.pop('moneda')
        with patch('core.services.avisos.enviar_comprobante'):
            with self.assertRaises(pagos.MontoCapturadoDistinto):
                pagos.aplicar_resultado_de_captura(order, response, estado_anterior=order.estado)
        purchase.refresh_from_db()
        self.assertEqual(purchase.estado, EstadoCompraPaquete.PENDIENTE)

    def test_PROD07_email_log_failure_must_not_fail_committed_payment(self):
        order, purchase = self.helper.crear_orden()
        # La transacción de TestCase permite comprobar el callback sin SMTP.
        with patch('core.services.correo.EnvioCorreo.objects.create',
                   side_effect=OperationalError('QA simulated email log failure')):
            with self.captureOnCommitCallbacks(execute=True):
                pagos.aplicar_resultado_de_captura(
                    order, self.helper.orden_en_paypal(), estado_anterior=order.estado,
                )
        purchase.refresh_from_db()
        self.assertEqual(purchase.estado, EstadoCompraPaquete.PAGADA)


class RefundConcurrencyChecks(TransactionTestCase):
    def test_PROD08_same_refund_concurrent_delivery_must_be_idempotent(self):
        helper, purchase = qa_fixtures.QAConcurrentFixesTests.webhook_fixture(self)
        order = OrdenPagoPaypal.objects.get(compra=purchase)
        with patch('core.services.avisos.enviar_comprobante'):
            pagos.aplicar_resultado_de_captura(
                order, helper.orden_en_paypal(), estado_anterior=order.estado,
            )
        refund = helper.evento('PAYMENT.CAPTURE.REFUNDED')
        refund_id = f'REFUND-{order.pk}'
        refund['resource']['id'] = refund_id
        barrier = Barrier(2)
        original = QuerySet.first

        def first(queryset):
            result = original(queryset)
            refund_lookup = any(
                getattr(getattr(getattr(child, 'lhs', None), 'target', None), 'name', None)
                == 'paypal_refund_id' for child in queryset.query.where.children
            )
            if queryset.model is TransaccionPagoPaypal and refund_lookup:
                barrier.wait(timeout=15)
            return result

        def process(_):
            close_old_connections()
            try:
                try:
                    pagos._reembolsar_orden(order, refund, timezone.now())
                    return 'ok'
                except IntegrityError:
                    return 'integrity-error'
            finally:
                close_old_connections()

        with patch.object(QuerySet, 'first', first):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(process, range(2)))
        print('PROD08 concurrent refund results:', results)
        self.assertEqual(results, ['ok', 'ok'])
        self.assertEqual(TransaccionPagoPaypal.objects.filter(paypal_refund_id=refund_id).count(), 1)
