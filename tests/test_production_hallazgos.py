"""Regresiones de los hallazgos PROD03, PROD04 y PROD05."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections

from core.models import Aspirante, PropositoToken
from core.services import tokens
from tests import production_audit_checks as audit


class ProductionAuthRegressionTests(audit.ProductionAuthChecks):
    def test_invalid_refresh_never_changes_credentials(self):
        original_hash = self.user.password_hash
        for refresh in ({}, [], 1, 1.5, True, False, None):
            with self.subTest(refresh=refresh):
                response = self.client.post('/api/auth/password/', {
                    'password_actual': 'Production-Audit-Original-872!',
                    'password_nueva': 'Production-Audit-Replacement-984!',
                    'refresh': refresh,
                }, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('refresh', response.data)
                self.user.refresh_from_db()
                self.assertEqual(self.user.password_hash, original_hash)


class ProductionConcurrencyRegressionTests(audit.ProductionConcurrencyChecks):
    def test_concurrent_token_redemption_succeeds_only_once(self):
        user = self.user()
        old = tokens.emitir(user, PropositoToken.RECUPERACION)
        current = tokens.emitir(user, PropositoToken.RECUPERACION)
        with self.assertRaises(tokens.TokenInvalido):
            tokens.canjear(old, PropositoToken.RECUPERACION)
        barrier = Barrier(2)

        def redeem(_):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                try:
                    tokens.canjear(current, PropositoToken.RECUPERACION)
                    return 'redeemed'
                except tokens.TokenInvalido:
                    return 'invalid'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(redeem, range(2))), ['invalid', 'redeemed'])

    def test_cedula_database_normalizes_case_spaces_and_ignores_deleted(self):
        from django.db import IntegrityError, transaction
        from django.utils import timezone

        user = self.user()
        now = timezone.now()
        first = Aspirante.objects.create(
            id='ASP-CEDULA-FIRST', usuario=user, matricula='CEDULA-FIRST',
            nombre_completo=user.nombre_completo, email=user.email,
            cedula_profesional='  Cedula-Unique-123  ',
            estado_expediente='incompleto', registrado_en=now, actualizado_en=now,
        )
        second_user = self.user()
        data = dict(id='ASP-CEDULA-SECOND', usuario=second_user, matricula='CEDULA-SECOND',
                    nombre_completo=second_user.nombre_completo, email=second_user.email,
                    cedula_profesional='cedula-unique-123', estado_expediente='incompleto',
                    registrado_en=now, actualizado_en=now)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Aspirante.objects.create(**data)
        first.eliminado_en = now
        first.save(update_fields=['eliminado_en'])
        Aspirante.objects.create(**data)
