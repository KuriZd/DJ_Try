"""El perfil de producción se verifica sin secretos ni servicios externos."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase


class ProductionConfigTests(TestCase):
    def entorno(self):
        # No heredar credenciales de la terminal ni archivos .env.
        entorno = {k: v for k, v in os.environ.items() if k.upper() in (
            'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC',
        )}
        entorno.update({
            'DJANGO_SETTINGS_MODULE': 'config.production',
            'DJANGO_SECRET_KEY': 'release-probe-only-0123456789-abcdefghijklmnopqrstuvwxyz',
            'ALLOWED_HOSTS': 'api.example.test',
            'CORS_ALLOWED_ORIGINS': 'https://app.example.test',
            'POSTGRES_DB': 'probe', 'POSTGRES_USER': 'probe',
            'POSTGRES_PASSWORD': 'probe', 'POSTGRES_HOST': 'db.example.test',
            'FRONTEND_BASE_URL': 'https://app.example.test',
            'PAYPAL_RETURN_URL': 'https://app.example.test/pagos/paypal/return',
            'PAYPAL_CANCEL_URL': 'https://app.example.test/pagos/paypal/cancel',
            'PAYPAL_CLIENT_ID': 'probe', 'PAYPAL_CLIENT_SECRET': 'probe',
            'PAYPAL_WEBHOOK_ID': 'probe', 'AWS_STORAGE_BUCKET_NAME': 'probe',
            'PRIVATE_MEDIA_ROOT': str(Path.cwd() / '.qa_audit' / 'media'),
            'EMAIL_HOST': 'smtp.example.test', 'DEFAULT_FROM_EMAIL': 'probe@example.test',
            'AWS_EC2_METADATA_DISABLED': 'true',
            'SECURE_HSTS_INCLUDE_SUBDOMAINS': 'true', 'SECURE_HSTS_PRELOAD': 'true',
        })
        return entorno

    def run_probe(self, code, **cambios):
        entorno = self.entorno()
        entorno.update(cambios)
        return subprocess.run([sys.executable, '-c', code], env=entorno,
                              capture_output=True, text=True, timeout=60)

    def test_deploy_checks_pass(self):
        resultado = self.run_probe(
            "import django; django.setup(); from django.core.management import call_command; "
            "call_command('check', deploy=True, fail_level='WARNING')"
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    def test_azure_does_not_require_aws_bucket_but_requires_azure_credentials(self):
        config = {'MEDIA_STORAGE_PROVIDER': 'azure', 'AWS_STORAGE_BUCKET_NAME': '',
                  'AZURE_ACCOUNT_NAME': 'probe', 'AZURE_ACCOUNT_KEY': 'probe',
                  'AZURE_CONTAINER': 'private-media'}
        result = self.run_probe('from config.production import DEBUG', **config)
        self.assertEqual(result.returncode, 0, result.stderr)
        config['AZURE_ACCOUNT_KEY'] = ''
        result = self.run_probe('from config.production import DEBUG', **config)
        self.assertNotEqual(result.returncode, 0)

    def test_debug_false_proxy_opt_in_and_database_tls(self):
        codigo = (
            "import json; from django.conf import settings as s; "
            "print(json.dumps([s.DEBUG, s.SECURE_PROXY_SSL_HEADER, "
            "s.REST_FRAMEWORK['NUM_PROXIES'], s.DATABASES['default']['OPTIONS']['sslmode']]))"
        )
        resultado = self.run_probe(codigo)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(json.loads(resultado.stdout), [False, None, 0, 'verify-full'])
        resultado = self.run_probe(codigo, DJANGO_TRUST_PROXY_HTTPS='true', DJANGO_NUM_PROXIES='1')
        self.assertEqual(json.loads(resultado.stdout)[1:3], [['HTTP_X_FORWARDED_PROTO', 'https'], 1])

    def test_collectstatic_and_serving_admin_assets(self):
        codigo = (
            "import os, tempfile; "
            "directorio = tempfile.TemporaryDirectory(); "
            "os.environ['STATIC_ROOT'] = directorio.name; "
            "import django; django.setup(); "
            "from django.core.management import call_command; "
            "call_command('collectstatic', interactive=False, verbosity=0); "
            "from django.test import Client; "
            "respuesta = Client().get('/static/admin/css/base.css', secure=True, "
            "HTTP_HOST='api.example.test'); "
            "assert respuesta.status_code == 200, respuesta.status_code; "
            "respuesta.close(); directorio.cleanup()"
        )
        resultado = self.run_probe(codigo)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    def test_rejects_incomplete_or_unsafe_configuration(self):
        for cambio in ({'DJANGO_SECRET_KEY': ''}, {'ALLOWED_HOSTS': '*'},
                       {'POSTGRES_SSLMODE': 'disable'}, {'FRONTEND_BASE_URL': 'http://localhost'},
                       {'PAYPAL_WEBHOOK_ID': ''}, {'EMAIL_REDIRIGIR_A': 'test@example.test'}):
            with self.subTest(cambio=cambio):
                resultado = self.run_probe('from config.production import DEBUG', **cambio)
                self.assertNotEqual(resultado.returncode, 0)
