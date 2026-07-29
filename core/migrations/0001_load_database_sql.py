from pathlib import Path

from django.conf import settings
from django.db import migrations


def read_sql(filename):
    sql_path = Path(settings.DATABASE_SQL_DIR) / filename
    if not sql_path.is_file():
        raise FileNotFoundError(f"No se encontró el archivo SQL: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8").strip()

    # Django controla la transacción completa del schema y el seed.
    if sql.upper().startswith("BEGIN;"):
        sql = sql[len("BEGIN;"):].lstrip()
    if sql.upper().endswith("COMMIT;"):
        sql = sql[:-len("COMMIT;")].rstrip()

    return sql


def load_database_sql(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(read_sql("schema.sql"))
        cursor.execute(read_sql("seed.sql"))


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunPython(load_database_sql),
    ]
