from django.db import migrations


AGREGAR_NOTAS_SQL = """
-- Nota libre de quien archiva el documento: para qué proceso fue, quién lo
-- aplicó o cualquier contexto que deba conservarse junto al reporte.
ALTER TABLE reportes_psicometricos
  ADD COLUMN IF NOT EXISTS notas TEXT;
"""


QUITAR_NOTAS_SQL = """
ALTER TABLE reportes_psicometricos
  DROP COLUMN IF EXISTS notas;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_infraestructura_pagos_paypal"),
    ]

    operations = [
        migrations.RunSQL(AGREGAR_NOTAS_SQL, reverse_sql=QUITAR_NOTAS_SQL),
    ]
