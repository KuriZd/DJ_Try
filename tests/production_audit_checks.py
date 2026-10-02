"""Auditoría de producción: ejecutar explícitamente, fuera de la suite base.

Estas aserciones documentan el comportamiento esperado; los fallos no se
marcan como esperados. Usar únicamente scripts/qa_audit_run.py.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.api.serializers import PerfilUpdateSerializer
from core.models import (
    Aspirante, EstadoCompraPaquete, EstadoPagoPaypal, TokenRecuperacion, Usuario,
)
from core.services import pagos, tokens
from tests import test_pagos_webhook as fixtures


class ProductionPaymentChecks(TestCase):
    def setUp(self):
        self.helper = fixtures.WebhookPaypalTest()
        self.helper.setUp()

    def test_PROD01_capture_in_flight_must_not_reactivate_refunded_purchase(self):
        order, purchase = self.helper.crear_orden()
        self.helper.capturar_en_pendiente(order)
        completed_response = self.helper.orden_en_paypal()

        def capture_in_flight(**kwargs):
            # PayPal respondió COMPLETED; antes de aplicar esa respuesta,
            # el webhook de devolución se procesa en otra solicitud.
            refund = self.helper.evento('PAYMENT.CAPTURE.REFUNDED', event_id='WH-PROD-REFUND')
            refund['resource']['id'] = 'REFUND-PROD-1'
            pagos._reembolsar_orden(order, refund, timezone.now())
            return completed_response

        with patch('core.services.pagos.paypal_client.capturar_orden', side_effect=capture_in_flight):
            with patch('core.services.avisos.enviar_comprobante'):
                pagos.capturar_pago_paypal(orden=order)
        order.refresh_from_db()
        purchase.refresh_from_db()
        print('PROD01: after refund and in-flight completion:', order.estado, purchase.estado)
        self.assertEqual(order.estado, EstadoPagoPaypal.REFUNDED)
        self.assertEqual(purchase.estado, EstadoCompraPaquete.REEMBOLSADA)

    def test_PROD02_stale_pending_must_not_downgrade_completed_order(self):
        order, purchase = self.helper.crear_orden()
        with patch('core.services.avisos.enviar_comprobante'):
            pagos.aplicar_resultado_de_captura(
                order, self.helper.orden_en_paypal(), estado_anterior=order.estado,
            )
            pagos.aplicar_resultado_de_captura(
                order, self.helper.orden_en_paypal('PENDING'), estado_anterior=EstadoPagoPaypal.APPROVED,
            )
        order.refresh_from_db()
        purchase.refresh_from_db()
        print('PROD02: after completion and stale pending:', order.estado, purchase.estado)
        self.assertEqual(order.estado, EstadoPagoPaypal.COMPLETED)


class ProductionAuthChecks(TestCase):
    def setUp(self):
        cache.clear()
        self.user = Usuario.objects.get(email='aspirante@amis.org')
        self.user.password_hash = make_password('Production-Audit-Original-872!')
        self.user.save(update_fields=['password_hash'])
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(self.user)

    def test_PROD03_password_change_non_string_refresh_must_return_400(self):
        response = self.client.post('/api/auth/password/', {
            'password_actual': 'Production-Audit-Original-872!',
            'password_nueva': 'Production-Audit-Replacement-984!',
            'refresh': {'unexpected': 1},
        }, format='json')
        print('PROD03: password change malformed refresh:', response.status_code)
        self.assertEqual(response.status_code, 400)


class ProductionConcurrencyChecks(TransactionTestCase):
    def setUp(self):
        cache.clear()

    def user(self):
        now = timezone.now()
        return Usuario.objects.create(
            id=uuid.uuid4(), nombre_completo='Production Audit',
            email=f'prod-audit-{uuid.uuid4()}@example.test', password_hash='!', estado='activo',
            creado_en=now, actualizado_en=now,
        )

    def test_PROD04_concurrent_recovery_requests_must_not_return_500(self):
        user = self.user()
        barrier = Barrier(2)
        original = tokens.emitir

        def emit(*args, **kwargs):
            # Sincronizar antes del bloqueo; hacerlo dentro impediria que
            # la segunda solicitud avance y provocaria un bloqueo artificial.
            barrier.wait(timeout=15)
            return original(*args, **kwargs)

        def submit(_):
            close_old_connections()
            try:
                response = APIClient(raise_request_exception=False).post(
                    '/api/auth/recuperar/', {'email': user.email}, format='json',
                )
                return response.status_code
            finally:
                close_old_connections()

        with patch.object(tokens, 'emitir', emit):
            with patch('core.services.avisos.enviar_recuperacion'):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(submit, range(2)))
        print('PROD04: concurrent recovery:', results)
        self.assertEqual(results, [204, 204])
        self.assertEqual(TokenRecuperacion.objects.filter(usuario=user, usado_en__isnull=True).count(), 1)
        self.assertEqual(TokenRecuperacion.objects.filter(usuario=user).count(), 2)

    def test_PROD05_two_users_must_not_register_the_same_cedula_concurrently(self):
        users = [self.user(), self.user()]
        now = timezone.now()
        for user in users:
            suffix = uuid.uuid4().hex[:12]
            Aspirante.objects.create(
                id=f'ASP-PROD-{suffix}', usuario=user, matricula=f'PROD-{suffix}',
                nombre_completo=user.nombre_completo, email=user.email,
                estado_expediente='incompleto', registrado_en=now, actualizado_en=now,
            )
        barrier = Barrier(2)
        original = PerfilUpdateSerializer.validate_cedula_profesional

        def validate(serializer, value):
            result = original(serializer, value)
            barrier.wait(timeout=15)
            return result

        def submit(user):
            close_old_connections()
            try:
                client = APIClient(raise_request_exception=False)
                client.force_authenticate(Usuario.objects.get(pk=user.pk))
                return client.patch('/api/auth/me/', {'cedula_profesional': 'PROD-DUPLICATE-998'}, format='json').status_code
            finally:
                close_old_connections()

        with patch.object(PerfilUpdateSerializer, 'validate_cedula_profesional', validate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, users))
        print('PROD05: concurrent cedula across users:', results,
              'persisted duplicates=', Aspirante.objects.filter(cedula_profesional='PROD-DUPLICATE-998').count())
        self.assertEqual(sorted(results), [200, 400])
