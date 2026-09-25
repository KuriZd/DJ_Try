"""Configuracion aislada para reproducir la auditoria; no lee archivos .env."""
import os
from unittest.mock import patch

with patch('dotenv.load_dotenv'):
    from config.settings import *  # noqa: F403,F401

SECRET_KEY = 'qa-audit-only-not-a-production-secret-key-20260925'
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': 'postgres', 'USER': 'qa_audit', 'PASSWORD': '',
    'HOST': '127.0.0.1', 'PORT': '55439',
    'TEST': {'NAME': 'test_qa_' + os.environ.get('QA_RUN', 'audit')},
}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
EMAIL_REDIRIGIR_A = ''
MEDIA_ROOT = BASE_DIR / '.qa_audit' / 'media'
PAYPAL_CLIENT_ID = PAYPAL_CLIENT_SECRET = PAYPAL_WEBHOOK_ID = ''
AWS_STORAGE_BUCKET_NAME = 'qa-audit-invalid'
SECURE_SSL_REDIRECT = False
os.environ['AWS_EC2_METADATA_DISABLED'] = 'true'
