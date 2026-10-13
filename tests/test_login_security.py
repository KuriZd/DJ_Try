"""Regresiones contra acceso sin credenciales y tokens manipulados."""
from unittest.mock import patch
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Sesion, Usuario
from core.api.throttles import LoginAccountRateThrottle, LoginIpRateThrottle


class LoginSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = Usuario.objects.get(email='aspirante@amis.org')
        self.password = 'QA-Login-Security-926!'
        self.user.password_hash = make_password(self.password)
        self.user.save(update_fields=['password_hash'])

    @patch.object(LoginIpRateThrottle, 'get_rate', return_value='2/minute')
    def test_changing_email_does_not_bypass_ip_limit(self, rate):
        client = APIClient()
        for index in range(2):
            response = client.post('/api/auth/login/', {
                'email': f'unknown-{index}@example.invalid', 'password': self.password,
            }, format='json', REMOTE_ADDR='192.0.2.10')
            self.assertEqual(response.status_code, 400)
        response = client.post('/api/auth/login/', {
            'email': self.user.email, 'password': self.password,
        }, format='json', REMOTE_ADDR='192.0.2.10')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)

    @patch.object(LoginAccountRateThrottle, 'get_rate', return_value='2/minute')
    def test_changing_ip_and_email_case_does_not_bypass_account_limit(self, rate):
        client = APIClient()
        for index, email in enumerate((self.user.email, self.user.email.upper())):
            response = client.post('/api/auth/login/', {
                'email': email, 'password': 'incorrect-password',
            }, format='json', REMOTE_ADDR=f'192.0.2.{index + 1}')
            self.assertEqual(response.status_code, 400)
        response = client.post('/api/auth/login/', {
            'email': self.user.email, 'password': self.password,
        }, format='json', REMOTE_ADDR='192.0.2.3')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Sesion.objects.filter(usuario=self.user).count(), 0)

    @patch('core.api.serializers.make_password', wraps=make_password)
    def test_nonexistent_user_runs_configured_password_hasher(self, hasher):
        response = APIClient().post('/api/auth/login/', {
            'email': 'unknown@example.invalid', 'password': self.password,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        hasher.assert_called_once_with(self.password)

    def test_invalid_credentials_never_create_session_or_return_tokens(self):
        before = Sesion.objects.count()
        attempts = [
            {'email': 'not-registered@example.invalid', 'password': self.password},
            {'email': self.user.email, 'password': 'incorrect-password'},
            {'email': self.user.email, 'password': ''},
            {'email': "' OR 1=1 --", 'password': self.password},
            {'email': {'$ne': None}, 'password': {'$ne': None}},
            {'email': self.user.email},
        ]
        for payload in attempts:
            with self.subTest(payload=payload):
                cache.clear()
                response = APIClient().post('/api/auth/login/', payload, format='json')
                self.assertIn(response.status_code, (400, 401))
                self.assertNotIn('access', response.data)
                self.assertNotIn('refresh', response.data)
                self.assertEqual(Sesion.objects.count(), before)

    def test_modified_token_signature_is_rejected(self):
        response = APIClient().post('/api/auth/login/', {
            'email': self.user.email, 'password': self.password,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        header, payload, signature = response.data['access'].split('.')
        signature = ('A' if signature[0] != 'A' else 'B') + signature[1:]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {header}.{payload}.{signature}')
        self.assertEqual(client.get('/api/auth/me/').status_code, 401)
