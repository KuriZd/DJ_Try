"""QA regression assertions: unresolved findings intentionally fail.

Run with scripts/qa_audit_run.py and the isolated PostgreSQL cluster only.
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

from core.api.serializers import PerfilUpdateSerializer, RegistroSerializer
from core.models import Aspirante, PropositoToken, Sesion, Usuario
from core.services import tokens


class AuditSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient(raise_request_exception=False)

    def login_fixture(self):
        user = Usuario.objects.get(email='aspirante@amis.org')
        user.password_hash = make_password('QA-Original-Long-924!')
        user.save(update_fields=['password_hash'])
        response = self.client.post('/api/auth/login/', {
            'email': user.email, 'password': 'QA-Original-Long-924!',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        return user, response.data

    def test_QA01_documented_demo_credentials_must_not_grant_admin(self):
        response = self.client.post('/api/auth/login/', {
            'email': 'kurizd@djtry.local', 'password': '0330',
        }, format='json')
        if response.status_code == 200:
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
            protected = self.client.get('/api/usuarios/')
            print('QA01: documented login=200; admin user directory=', protected.status_code)
        self.assertNotEqual(response.status_code, 200, 'Publicly documented demo credentials remain active')

    def test_QA02_password_reset_must_revoke_old_access_token(self):
        user, session = self.login_fixture()
        token = tokens.emitir(user, PropositoToken.RECUPERACION)
        reset = self.client.post('/api/auth/restablecer/', {
            'token': token, 'password_nueva': 'QA-Replacement-Long-835!',
        }, format='json')
        self.assertEqual(reset.status_code, 204)
        self.assertFalse(Sesion.objects.filter(usuario=user, revocada_en__isnull=True).exists())
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + session['access'])
        old_access = self.client.get('/api/auth/me/')
        print('QA02: reset=204, all sessions revoked; old access /me/=', old_access.status_code)
        self.assertEqual(old_access.status_code, 401)

    def test_QA07_valid_registration_after_migrations_must_succeed(self):
        # Pin only the year: the seed contains AM2026 matriculas.
        from datetime import datetime, timezone as datetime_timezone
        with patch('core.api.serializers.timezone.now', return_value=datetime(2026, 9, 25, tzinfo=datetime_timezone.utc)):
            response = self.client.post('/api/auth/registro/', {
                'nombre_completo': 'QA New Applicant',
                'email': 'qa-new-applicant@example.test',
                'password': 'QA-New-Applicant-Long-821!',
            }, format='json')
        print('QA07: valid registration=', response.status_code,
              response.data if response.status_code != 201 else 'created',
              'persisted users=', Usuario.objects.filter(email='qa-new-applicant@example.test').count())
        self.assertEqual(response.status_code, 201)

    def test_QA03_non_object_auth_json_must_return_400(self):
        for endpoint in ('login', 'recuperar', 'refresh'):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(f'/api/auth/{endpoint}/', ['unexpected'], format='json')
                print('QA03:', endpoint, 'array JSON status=', response.status_code)
                self.assertEqual(response.status_code, 400)

    def test_QA03_non_string_logout_refresh_must_return_400(self):
        user, _ = self.login_fixture()
        self.client.force_authenticate(user=user)
        response = self.client.post('/api/auth/logout/', {'refresh': {'unexpected': 1}}, format='json')
        print('QA03: logout object refresh status=', response.status_code)
        self.assertEqual(response.status_code, 400)

    def test_QA03_malformed_uuid_in_signed_token_must_return_401(self):
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken()
        token['user_id'] = 'not-a-uuid'
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.get('/api/auth/me/')
        print('QA03: signed malformed user_id status=', response.status_code)
        self.assertEqual(response.status_code, 401)


class AuditConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()

    def test_QA04_concurrent_first_cedula_cannot_overwrite_registered_value(self):
        now = timezone.now()
        user = Usuario.objects.create(id=uuid.uuid4(), nombre_completo='QA Race',
            email='qa-race@example.test', password_hash='!', estado='activo',
            creado_en=now, actualizado_en=now)
        Aspirante.objects.create(id='ASP-QA-RACE', usuario=user, matricula='QA-RACE',
            nombre_completo=user.nombre_completo, email=user.email,
            estado_expediente='incompleto', registrado_en=now, actualizado_en=now)
        barrier = Barrier(2)
        original = PerfilUpdateSerializer.validate_cedula_profesional

        def synchronized_validation(serializer, value):
            result = original(serializer, value)
            barrier.wait(timeout=15)
            return result

        def submit(value):
            close_old_connections()
            try:
                client = APIClient(raise_request_exception=False)
                client.force_authenticate(Usuario.objects.get(pk=user.pk))
                response = client.patch('/api/auth/me/', {'cedula_profesional': value}, format='json')
                return response.status_code
            finally:
                close_old_connections()

        with patch.object(PerfilUpdateSerializer, 'validate_cedula_profesional', synchronized_validation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, ['11111111', '22222222']))
        final = Aspirante.objects.get(usuario=user).cedula_profesional
        print('QA04: simultaneous cedula PATCH statuses=', results, 'persisted=', final)
        self.assertEqual(sorted(results), [200, 400])

    def test_QA05_concurrent_duplicate_registration_must_not_return_500(self):
        from core.models import Rol
        Rol.objects.get_or_create(clave='aspirante', defaults={'nombre': 'Aspirante'})
        now = timezone.now()
        # Avoid QA07's independent matricula collision to reach the duplicate race.
        Aspirante.objects.create(id='ASP-100', matricula='QA-CONCURRENCY-100',
            nombre_completo='QA Baseline', email='qa-baseline@example.test',
            estado_expediente='incompleto', registrado_en=now, actualizado_en=now)
        barrier = Barrier(2)
        original = RegistroSerializer.validate_email

        def synchronized_validation(serializer, value):
            result = original(serializer, value)
            barrier.wait(timeout=15)
            return result

        def submit(_):
            close_old_connections()
            try:
                client = APIClient(raise_request_exception=False)
                response = client.post('/api/auth/registro/', {
                    'nombre_completo': 'QA Duplicate', 'email': 'qa-duplicate@example.test',
                    'password': 'QA-Registration-Long-932!',
                }, format='json')
                if response.status_code == 400:
                    print('QA05 validation response:', response.data)
                return response.status_code
            finally:
                close_old_connections()

        with patch.object(RegistroSerializer, 'validate_email', synchronized_validation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, range(2)))
        print('QA05: simultaneous registration statuses=', results,
              'users=', Usuario.objects.filter(email='qa-duplicate@example.test').count())
        self.assertEqual(sorted(results), [201, 400])


class AuditWebhookTests(TestCase):
    def test_QA06_retry_after_provider_timeout_must_process_event(self):
        from tests import test_pagos_webhook as fixtures
        from core.models import EventoPagoPaypal, EstadoCompraPaquete
        from core.services.paypal import PaypalError
        helper = fixtures.WebhookPaypalTest()
        helper.setUp()
        order, purchase = helper.crear_orden()
        helper.capturar_en_pendiente(order)
        event = helper.evento('PAYMENT.CAPTURE.COMPLETED', event_id='WH-QA-RETRY')
        with patch('core.services.pagos.paypal_client.verificar_firma_webhook', return_value=True):
            with patch('core.services.pagos.paypal_client.obtener_orden', side_effect=[
                PaypalError('Simulated timeout', code='PAYPAL_CONNECTION_ERROR'),
                helper.orden_en_paypal(),
            ]) as fetch:
                first = helper.enviar(event)
                second = helper.enviar(event)
        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 200)
        purchase.refresh_from_db()
        row = EventoPagoPaypal.objects.get(paypal_event_id='WH-QA-RETRY')
        print('QA06: responses=', first.status_code, second.status_code, second.data,
              'provider reads=', fetch.call_count, 'processed=', row.procesado,
              'purchase=', purchase.estado)
        self.assertEqual(purchase.estado, EstadoCompraPaquete.PAGADA)


class AuditReportPermissionTests(TestCase):
    def test_QA09_own_upload_must_not_replace_platform_report(self):
        import tempfile
        from django.test import override_settings
        from django.core.files.uploadedfile import SimpleUploadedFile
        from core.models import ReportePsicometrico, EstadoReportePsicometrico
        admin = Usuario.objects.get(email='admin@amis.org')
        user = Usuario.objects.get(email='aspirante@amis.org')
        applicant = Aspirante.objects.get(usuario=user)
        client = APIClient()

        def payload():
            return {'aspirante': applicant.pk, 'referencia_evaluacion_externa': 'QA-OFFICIAL-001',
                    'archivo': SimpleUploadedFile('qa.pdf', b'%PDF-1.4\nQA\n%%EOF', content_type='application/pdf')}

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            client.force_authenticate(admin)
            official = client.post('/api/reportes-psicometricos/', payload(), format='multipart')
            self.assertEqual(official.status_code, 201)
            client.force_authenticate(user)
            forbidden_delete = client.delete(f"/api/reportes-psicometricos/{official.data['id']}/")
            self.assertEqual(forbidden_delete.status_code, 403)
            uploaded = client.post('/api/reportes-psicometricos/', payload(), format='multipart')
            report = ReportePsicometrico.objects.get(pk=official.data['id'])
            print('QA09: official DELETE=', forbidden_delete.status_code, 'own POST=', uploaded.status_code,
                  'official state=', report.estado, 'for sale=', report.disponible_para_compra)
            self.assertEqual(report.estado, EstadoReportePsicometrico.DISPONIBLE)
            self.assertTrue(report.disponible_para_compra)
