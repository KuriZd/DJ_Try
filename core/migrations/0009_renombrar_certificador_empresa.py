from django.db import migrations


RENOMBRAR_ROL_SQL = """
UPDATE roles
SET clave = 'empresa',
    nombre = 'Empresa'
WHERE clave = 'certificador';
"""


REVERTIR_ROL_SQL = """
UPDATE roles
SET clave = 'certificador',
    nombre = 'Certificador'
WHERE clave = 'empresa';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_ampliar_vacantes"),
    ]

    operations = [
        migrations.RunSQL(
            RENOMBRAR_ROL_SQL,
            reverse_sql=REVERTIR_ROL_SQL,
        ),
    ]
