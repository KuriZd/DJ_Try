"""Pruebas con PostgreSQL efímero de CI y sin leer secretos locales."""
from unittest.mock import patch

with patch('dotenv.load_dotenv'):
    from config.settings import *  # noqa: F403,F401

SECRET_KEY = 'ci-only-key-never-use-in-production-01234567890123456789'
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
EMAIL_REDIRIGIR_A = ''
MEDIA_ROOT = BASE_DIR / '.ci_media'
PAYPAL_CLIENT_ID = PAYPAL_CLIENT_SECRET = PAYPAL_WEBHOOK_ID = ''
AWS_STORAGE_BUCKET_NAME = 'ci-invalid'
SECURE_SSL_REDIRECT = False
os.environ['AWS_EC2_METADATA_DISABLED'] = 'true'
