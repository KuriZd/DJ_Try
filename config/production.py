"""Perfil de producción: exclusivamente variables del proceso, sin .env local."""
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403,F401


def requerido(nombre):
    valor = os.getenv(nombre, '').strip()
    if not valor:
        raise ImproperlyConfigured(f'Producción requiere {nombre}.')
    return valor


DEBUG = False
SECRET_KEY = requerido('DJANGO_SECRET_KEY')
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5:
    raise ImproperlyConfigured('DJANGO_SECRET_KEY debe ser larga y aleatoria.')
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('Producción requiere ALLOWED_HOSTS explícitos.')

for nombre in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_HOST'):
    requerido(nombre)
DATABASES['default'].update(
    CONN_MAX_AGE=int(os.getenv('POSTGRES_CONN_MAX_AGE', '60')),
    CONN_HEALTH_CHECKS=True,
    OPTIONS={
        'sslmode': os.getenv('POSTGRES_SSLMODE', 'verify-full'),
        'connect_timeout': 10,
    },
)
if DATABASES['default']['OPTIONS']['sslmode'] not in ('require', 'verify-ca', 'verify-full'):
    raise ImproperlyConfigured('Producción requiere TLS para PostgreSQL.')
if os.getenv('POSTGRES_SSLROOTCERT'):
    DATABASES['default']['OPTIONS']['sslrootcert'] = os.environ['POSTGRES_SSLROOTCERT']

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
# Activar solo con backend privado y un proxy que sobrescribe la cabecera.
SECURE_PROXY_SSL_HEADER = (
    ('HTTP_X_FORWARDED_PROTO', 'https')
    if env_bool('DJANGO_TRUST_PROXY_HTTPS', False) else None
)
CSRF_TRUSTED_ORIGINS = [
    origen.strip() for origen in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origen.strip()
]
CORS_ALLOWED_ORIGINS = [
    origen.strip() for origen in requerido('CORS_ALLOWED_ORIGINS').split(',') if origen.strip()
]

for nombre in ('FRONTEND_BASE_URL', 'PAYPAL_RETURN_URL', 'PAYPAL_CANCEL_URL'):
    url = requerido(nombre)
    partes = urlsplit(url)
    if partes.scheme != 'https' or not partes.hostname or partes.username or partes.password:
        raise ImproperlyConfigured(f'{nombre} debe ser una URL HTTPS válida.')
for origen in CORS_ALLOWED_ORIGINS + CSRF_TRUSTED_ORIGINS:
    partes = urlsplit(origen)
    if (partes.scheme != 'https' or not partes.hostname or partes.path not in ('', '/')
            or partes.username or partes.password or partes.query or partes.fragment):
        raise ImproperlyConfigured('Los orígenes de producción deben usar HTTPS.')
if PAYPAL_MODE not in ('sandbox', 'live'):
    raise ImproperlyConfigured('PAYPAL_MODE debe ser sandbox o live.')
for nombre in ('PAYPAL_CLIENT_ID', 'PAYPAL_CLIENT_SECRET', 'PAYPAL_WEBHOOK_ID',
               'PRIVATE_MEDIA_ROOT', 'EMAIL_HOST', 'DEFAULT_FROM_EMAIL'):
    requerido(nombre)
for nombre in (('AZURE_ACCOUNT_NAME', 'AZURE_ACCOUNT_KEY', 'AZURE_CONTAINER')
               if MEDIA_STORAGE_PROVIDER == 'azure' else ('AWS_STORAGE_BUCKET_NAME',)):
    requerido(nombre)
if not MEDIA_ROOT.is_absolute():
    raise ImproperlyConfigured('PRIVATE_MEDIA_ROOT debe ser una ruta absoluta persistente.')
if EMAIL_REDIRIGIR_A:
    raise ImproperlyConfigured('Producción no debe redirigir los correos a una cuenta de prueba.')
if EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
    raise ImproperlyConfigured('Este perfil de producción requiere el backend SMTP.')

MIDDLEWARE = list(MIDDLEWARE)
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
STORAGES = dict(STORAGES)
# drf-yasg incluye referencias a sourcemaps ausentes. Compresión sin manifest
# permite recopilar sus assets sin ignorar errores de referencias CSS propias.
STORAGES['staticfiles'] = {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'}
REST_FRAMEWORK = dict(REST_FRAMEWORK)
REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] = ['rest_framework.renderers.JSONRenderer']
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'operacion': {'format': '{asctime} {levelname} {name} {message}', 'style': '{'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'operacion'}},
    'root': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
}
