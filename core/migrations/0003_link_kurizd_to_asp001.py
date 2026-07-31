from django.db import migrations


LINK_USER_SQL = """
UPDATE aspirantes
SET usuario_id = NULL,
    actualizado_en = now()
WHERE usuario_id = (
    SELECT id
    FROM usuarios
    WHERE lower(email) = 'kurizd@djtry.local'
      AND eliminado_en IS NULL
    ORDER BY creado_en
    LIMIT 1
)
AND id <> 'ASP-001';

UPDATE aspirantes
SET usuario_id = (
        SELECT id
        FROM usuarios
        WHERE lower(email) = 'kurizd@djtry.local'
          AND eliminado_en IS NULL
        ORDER BY creado_en
        LIMIT 1
    ),
    actualizado_en = now()
WHERE id = 'ASP-001'
  AND eliminado_en IS NULL
  AND EXISTS (
      SELECT 1
      FROM usuarios
      WHERE lower(email) = 'kurizd@djtry.local'
        AND eliminado_en IS NULL
  );
"""


UNLINK_USER_SQL = """
UPDATE aspirantes
SET usuario_id = NULL,
    actualizado_en = now()
WHERE id = 'ASP-001'
  AND usuario_id = (
      SELECT id
      FROM usuarios
      WHERE lower(email) = 'kurizd@djtry.local'
        AND eliminado_en IS NULL
      ORDER BY creado_en
      LIMIT 1
  );
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_seed_test_user"),
    ]

    operations = [
        migrations.RunSQL(LINK_USER_SQL, reverse_sql=UNLINK_USER_SQL),
    ]
