from django.db import migrations


CREAR_USUARIOS_AMIS_SQL = """
INSERT INTO usuarios (
  id, nombre_completo, email, password_hash, estado, email_verificado_en
) VALUES
  ('6dc07f5a-358c-4bd4-9f58-40fe36604393', 'Administrador AMIS', 'admin@amis.org', 'pbkdf2_sha256$1500000$Zrb4ZOEiEMlB58WPN5N8sA$EaQp2gpEC2kqG5/y4WSqIsHfPwgKk8DiJCLBbVoMlcA=', 'activo', now()),
  ('a5434e6e-8073-4f02-aabf-a0e92f79d4ae', 'Aspirante AMIS', 'aspirante@amis.org', 'pbkdf2_sha256$1500000$MdLUbAiuonT4HXi5AO4TJ5$vyBvnOYQCyvzlnoPY7EWkQe6puZDlL0z15GynulgBjM=', 'activo', now())
ON CONFLICT DO NOTHING;

INSERT INTO usuarios_roles (usuario_id, rol_id)
SELECT u.id, r.id
FROM usuarios u
JOIN roles r ON r.clave = CASE lower(u.email)
  WHEN 'admin@amis.org' THEN 'administrador'
  WHEN 'aspirante@amis.org' THEN 'aspirante'
END
WHERE lower(u.email) IN ('admin@amis.org', 'aspirante@amis.org')
ON CONFLICT (usuario_id, rol_id) DO NOTHING;

INSERT INTO aspirantes (
  id, usuario_id, matricula, nombre_completo, email, estado_expediente
)
SELECT
  'ASP-AMIS-001', u.id, 'AM2026-0007', 'Aspirante AMIS',
  'aspirante@amis.org', 'incompleto'
FROM usuarios u
WHERE lower(u.email) = 'aspirante@amis.org'
ON CONFLICT DO NOTHING;
"""


BORRAR_USUARIOS_AMIS_SQL = """
DELETE FROM aspirantes WHERE id = 'ASP-AMIS-001';
DELETE FROM usuarios
WHERE lower(email) IN ('admin@amis.org', 'aspirante@amis.org');
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_empresa_perfil"),
    ]

    operations = [
        migrations.RunSQL(
            CREAR_USUARIOS_AMIS_SQL,
            reverse_sql=BORRAR_USUARIOS_AMIS_SQL,
        ),
    ]
