"""Variantes de seguridad y concurrencia de los hallazgos de auditoría."""
import importlib
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from threading import Barrier
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.api.serializers import RegistroSerializer
from core.models import (
    Aspirante, CompraPaquetePsicometrico, EstadoCompraPaquete, EstadoPagoPaypal,
    EventoPagoPaypal, OrdenPagoPaypal, PaquetePsicometrico, Rol, Sesion, Usuario,
)
from tests import test_pagos_webhook as webhook_fixtures


class QAFixesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient(raise_request_exception=False)

    def test_todas_las_claves_demo_quedan_inutilizables(self):
        cuentas = [
            ('kurizd@djtry.local', '0330'),
            ('admin@amis.org', 'Amis2026!'),
            ('aspirante@amis.org', 'Amis2026!'),
            *[(f'kurizd@{rol}.com', '1234') for rol in
              ('administrador', 'reclutador', 'empresa', 'consulta', 'aspirante')],
        ]
        for email, password in cuentas:
            with self.subTest(email=email):
                response = self.client.post('/api/auth/login/', {
                    'email': email, 'password': password,
                }, format='json')
                self.assertEqual(response.status_code, 400)

    def test_migracion_revoca_demos_y_conserva_claves_rotadas_y_sesiones(self):
        migracion = importlib.import_module('core.migrations.0033_desactivar_claves_demo')
        demo = Usuario.objects.get(email='kurizd@djtry.local')
        demo.password_hash = migracion.DEMO_HASHES[0]
        demo.save(update_fields=['password_hash'])
        rotada = Usuario.objects.get(email='admin@amis.org')
        rotada.password_hash = make_password('QA-Rotated-Password-983!')
        rotada.save(update_fields=['password_hash'])
        ahora = timezone.now()
        sesiones = [Sesion.objects.create(
            id=uuid.uuid4(), usuario=user, refresh_token_hash=str(uuid.uuid4()),
            creado_en=ahora, expira_en=ahora + timedelta(days=1),
        ) for user in (demo, rotada)]
        with connection.schema_editor() as editor:
            migracion.desactivar_claves_demo(apps, editor)
        demo.refresh_from_db()
        rotada.refresh_from_db()
        for sesion in sesiones:
            sesion.refresh_from_db()
        self.assertTrue(demo.password_hash.startswith('!'))
        self.assertTrue(check_password('QA-Rotated-Password-983!', rotada.password_hash))
        self.assertIsNotNone(sesiones[0].revocada_en)
        self.assertIsNone(sesiones[1].revocada_en)

    def test_aprovisionar_admin_valida_clave_y_no_muestra_secretos(self):
        password = 'QA-Unique-Admin-Secret-735!'
        salida = io.StringIO()
        with patch('core.management.commands.crear_administrador.getpass', return_value=password):
            call_command('crear_administrador', email='qa-admin@example.test', nombre='QA Admin', stdout=salida)
        user = Usuario.objects.get(email='qa-admin@example.test')
        self.assertTrue(check_password(password, user.password_hash))
        self.assertTrue(user.usuarios_roles.filter(rol__clave='administrador').exists())
        self.assertNotIn(password, salida.getvalue())
        with self.assertRaises(CommandError):
            call_command('crear_administrador', email='qa-admin@example.test', nombre='Otro', stdout=salida)

    def test_migracion_retira_clave_demo_con_hash_actualizado(self):
        migracion = importlib.import_module('core.migrations.0033_desactivar_claves_demo')
        demo = Usuario.objects.get(email='kurizd@djtry.local')
        demo.password_hash = make_password('0330')
        demo.save(update_fields=['password_hash'])
        with connection.schema_editor() as editor:
            migracion.desactivar_claves_demo(apps, editor)
        demo.refresh_from_db()
        self.assertTrue(demo.password_hash.startswith('!'))

    def test_aprovisionar_admin_rechaza_claves_debiles_y_no_crea_cuenta(self):
        with patch('core.management.commands.crear_administrador.getpass', return_value='1234'):
            with self.assertRaises(CommandError):
                call_command('crear_administrador', email='qa-weak@example.test', nombre='QA Admin')
        self.assertFalse(Usuario.objects.filter(email='qa-weak@example.test').exists())

    def test_json_no_objeto_en_autenticacion(self):
        for endpoint in ('login', 'recuperar', 'refresh'):
            for body in ('null', '42', 'true', '"texto"', '[]'):
                with self.subTest(endpoint=endpoint, body=body):
                    cache.clear()
                    response = self.client.post(f'/api/auth/{endpoint}/', body, content_type='application/json')
                    self.assertEqual(response.status_code, 400)

    def test_refresh_y_logout_rechazan_tokens_de_otro_tipo(self):
        for endpoint in ('refresh', 'logout'):
            client = APIClient(raise_request_exception=False)
            if endpoint == 'logout':
                client.force_authenticate(Usuario.objects.get(email='aspirante@amis.org'))
            for value in (None, [], {}, True, 123):
                with self.subTest(endpoint=endpoint, value=value):
                    cache.clear()
                    response = client.post(f'/api/auth/{endpoint}/', {'refresh': value}, format='json')
                    self.assertEqual(response.status_code, 400)

    def test_matricula_independiente_de_folio_y_del_ano_anterior(self):
        ahora = timezone.now()
        Aspirante.objects.create(
            id='LEGACY-NO-NUMERICO', matricula='AM2026-9500', nombre_completo='Legacy',
            email='legacy-qa@example.test', estado_expediente='incompleto',
            registrado_en=ahora, actualizado_en=ahora,
        )
        for year, esperado in ((2026, 'AM2026-9501'), (2027, 'AM2027-0001')):
            with self.subTest(year=year):
                email = f'qa-year-{year}@example.test'
                with patch('core.api.serializers.timezone.now', return_value=datetime(year, 10, 1, tzinfo=datetime_timezone.utc)):
                    response = self.client.post('/api/auth/registro/', {
                        'nombre_completo': 'QA Year', 'email': email, 'password': 'QA-New-Year-Strong-934!',
                    }, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(Aspirante.objects.get(email=email).matricula, esperado)


class QAConcurrentFixesTests(TransactionTestCase):
    def setUp(self):
        cache.clear()

    def test_registros_concurrentes_distintos_generan_folios_y_matriculas_unicos(self):
        Rol.objects.get_or_create(clave='aspirante', defaults={'nombre': 'Aspirante', 'creado_en': timezone.now()})
        barrier = Barrier(2)
        original = RegistroSerializer.validate_email

        def validate(serializer, value):
            result = original(serializer, value)
            barrier.wait(timeout=15)
            return result

        def submit(index):
            close_old_connections()
            try:
                client = APIClient(raise_request_exception=False)
                response = client.post('/api/auth/registro/', {
                    'nombre_completo': 'QA Concurrent', 'email': f'qa-concurrent-{index}@example.test',
                    'password': 'QA-Parallel-Registration-783!',
                }, format='json')
                return response.status_code
            finally:
                close_old_connections()

        with patch.object(RegistroSerializer, 'validate_email', validate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(submit, range(2)))
        self.assertEqual(responses, [201, 201])
        filas = list(Aspirante.objects.filter(email__startswith='qa-concurrent-').values_list('id', 'matricula'))
        self.assertEqual(len({folio for folio, _ in filas}), 2)
        self.assertEqual(len({matricula for _, matricula in filas}), 2)

    def webhook_fixture(self):
        ahora = timezone.now()
        helper = webhook_fixtures.WebhookPaypalTest()
        helper.comprador = Usuario.objects.create(
            id=uuid.uuid4(), nombre_completo='QA Buyer', email=f'qa-buyer-{uuid.uuid4()}@example.test',
            password_hash='!', estado='activo', creado_en=ahora, actualizado_en=ahora,
        )
        helper.paquete, _ = PaquetePsicometrico.objects.get_or_create(clave='perfil', defaults={
            'nombre': 'Perfil', 'cantidad_pruebas': 3, 'precio_total': '2190.00',
            'creado_en': ahora, 'actualizado_en': ahora,
        })
        # Los modelos unmanaged no se vacían entre TransactionTestCase.
        # Reservar identificadores de proveedor y referencia distintos.
        suffix = uuid.uuid4().hex[:12]
        purchase = CompraPaquetePsicometrico.objects.create(
            id=uuid.uuid4(), comprador=helper.comprador, paquete=helper.paquete,
            paquete_nombre=helper.paquete.nombre, cantidad_pruebas=3,
            monto='2190.00', moneda='MXN', estado=EstadoCompraPaquete.PENDIENTE,
            creditos_totales=3, creditos_consumidos=0, creado_en=ahora, actualizado_en=ahora,
        )
        order = OrdenPagoPaypal.objects.create(
            id=uuid.uuid4(), referencia_interna=f'PAY-QA-{suffix}',
            comprador=helper.comprador, compra=purchase, paypal_order_id=f'ORDER-{suffix}',
            paypal_request_id=str(uuid.uuid4()), monto='2190.00', moneda='MXN',
            estado=EstadoPagoPaypal.APPROVED, creado_en=ahora, actualizado_en=ahora,
        )
        helper.evento = lambda tipo, **kwargs: webhook_fixtures.WebhookPaypalTest.evento(
            tipo, recurso={'id': f'CAPTURE-{suffix}', 'supplementary_data': {
                'related_ids': {'order_id': order.paypal_order_id},
            }}, **kwargs,
        )
        original_result = helper.orden_en_paypal()
        original_result.update(paypal_order_id=order.paypal_order_id, paypal_capture_id=f'CAPTURE-{suffix}')
        helper.orden_en_paypal = lambda: original_result
        return helper, purchase

    def test_webhooks_simultaneos_entregan_una_sola_vez(self):
        from core.services.pagos import procesar_evento_paypal
        helper, purchase = self.webhook_fixture()
        event = helper.evento('PAYMENT.CAPTURE.COMPLETED', event_id='WH-QA-CONCURRENT')
        barrier = Barrier(2)

        def verify(**kwargs):
            barrier.wait(timeout=15)
            return True

        def submit(_):
            close_old_connections()
            try:
                return procesar_evento_paypal(evento=event, cabeceras={})
            finally:
                close_old_connections()

        with patch('core.services.pagos.paypal_client.verificar_firma_webhook', side_effect=verify):
            with patch('core.services.pagos.paypal_client.obtener_orden', return_value=helper.orden_en_paypal()) as fetch:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(responses), ['duplicado', 'procesado'])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(EventoPagoPaypal.objects.filter(paypal_event_id=event['id']).count(), 1)
        purchase.refresh_from_db()
        self.assertEqual(purchase.estado, EstadoCompraPaquete.PAGADA)
        self.assertEqual(purchase.creditos_totales, 3)

    def test_fallo_inesperado_revierte_reclamo_y_permite_reintento(self):
        from core.services.pagos import procesar_evento_paypal
        helper, purchase = self.webhook_fixture()
        event = helper.evento('PAYMENT.CAPTURE.COMPLETED', event_id='WH-QA-CRASH')
        with patch('core.services.pagos.paypal_client.verificar_firma_webhook', return_value=True):
            with patch('core.services.pagos.paypal_client.obtener_orden', side_effect=RuntimeError('QA crash')):
                with self.assertRaises(RuntimeError):
                    procesar_evento_paypal(evento=event, cabeceras={})
            self.assertFalse(EventoPagoPaypal.objects.filter(paypal_event_id=event['id']).exists())
            with patch('core.services.pagos.paypal_client.obtener_orden', return_value=helper.orden_en_paypal()):
                self.assertEqual(procesar_evento_paypal(evento=event, cabeceras={}), 'procesado')
        purchase.refresh_from_db()
        self.assertEqual(purchase.estado, EstadoCompraPaquete.PAGADA)
