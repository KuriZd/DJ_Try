from django.db import migrations


CREATE_TEST_USER_SQL = """
INSERT INTO usuarios (
  id, nombre_completo, email, password_hash, estado, email_verificado_en
)
SELECT
  '7a15fc4f-d972-48f7-b3d4-4d5f1b877dab',
  'kurizd',
  'kurizd@djtry.local',
  'pbkdf2_sha256$600000$ELwd6GWDUmPQobJklIVC0c$K+o8b+nDTCjhXq3jUFWm7Yf+0KZSC/SwcX9UCsbX15k=',
  'activo',
  now()
WHERE NOT EXISTS (
  SELECT 1 FROM usuarios WHERE lower(email) = 'kurizd@djtry.local'
);

INSERT INTO usuarios_roles (usuario_id, rol_id)
SELECT u.id, r.id
FROM usuarios u
CROSS JOIN roles r
WHERE lower(u.email) = 'kurizd@djtry.local'
  AND r.clave = 'administrador'
ON CONFLICT (usuario_id, rol_id) DO NOTHING;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_load_database_sql"),
    ]

    operations = [
        migrations.RunSQL(CREATE_TEST_USER_SQL),
    ]
