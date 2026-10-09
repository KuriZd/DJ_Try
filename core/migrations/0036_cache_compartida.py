"""Tabla de la cache compartida (ver CACHES en settings).

`createcachetable` lee CACHES: con Redis configurado no crea nada, y si la
tabla ya existe no la toca. Se corre desde una migracion para que ningun
despliegue dependa de recordar un comando aparte.
"""

from django.core.management import call_command
from django.db import migrations


def crear_tabla(apps, schema_editor):
    call_command('createcachetable', database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [('core', '0035_cedula_unica')]

    operations = [
        migrations.RunPython(crear_tabla, migrations.RunPython.noop),
    ]
