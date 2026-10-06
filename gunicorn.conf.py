"""Arranque Linux: gunicorn config.wsgi:application -c gunicorn.conf.py."""
import os

bind = '0.0.0.0:' + os.getenv('PORT', '8000')
workers = int(os.getenv('WEB_CONCURRENCY', '2'))
timeout = int(os.getenv('GUNICORN_TIMEOUT', '60'))
accesslog = '-'
errorlog = '-'
# Evita registrar query strings: pueden contener tokens o URLs firmadas.
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
forwarded_allow_ips = os.getenv('GUNICORN_FORWARDED_ALLOW_IPS', '127.0.0.1')
