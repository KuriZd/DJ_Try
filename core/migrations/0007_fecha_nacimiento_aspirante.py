from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_permiso_postulaciones_consultar_todas"),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE aspirantes ADD COLUMN IF NOT EXISTS fecha_nacimiento DATE;",
            reverse_sql=(
                "ALTER TABLE aspirantes DROP COLUMN IF EXISTS fecha_nacimiento;"
            ),
        ),
    ]
