"""Run Django QA tests only against the dedicated local cluster, offline."""
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.qa_settings'
os.environ['DJANGO_SECRET_KEY'] = 'qa-audit-only-not-a-production-secret-key-20260925'
original_connect = socket.socket.connect


def isolated_connect(sock, address):
    if not isinstance(address, tuple) or address[:2] != ('127.0.0.1', 55439):
        raise RuntimeError('QA blocked a connection outside the isolated PostgreSQL cluster')
    return original_connect(sock, address)


socket.socket.connect = isolated_connect
from django.core.management import execute_from_command_line

execute_from_command_line([sys.argv[0], *sys.argv[1:]])
