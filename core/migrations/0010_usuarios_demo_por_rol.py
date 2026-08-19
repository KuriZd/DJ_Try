from django.db import migrations


CREAR_USUARIOS_SQL = """
INSERT INTO usuarios (
  id, nombre_completo, email, password_hash, estado, email_verificado_en
) VALUES
  ('b0fd0e98-6ed3-418c-8f0a-872eabc240a0', 'KuriZd Administrador', 'KuriZd@administrador.com', 'pbkdf2_sha256$600000$p6BtgfsiXTUC$q93qU84TWURm0l59XsRPVRhV6x0XcDTlMnPGt861wnY=', 'activo', now()),
  ('68af39e3-ebf9-49a2-90d7-1f925180909f', 'KuriZd Reclutador', 'KuriZd@reclutador.com', 'pbkdf2_sha256$600000$QpfkXs9oOpp2$XIbPiM6zQv7DQDZVJYGw9aLovhOduwwz/mIh9HgF+HA=', 'activo', now()),
  ('3910bf18-4dce-4d9b-930a-70a58f8e5008', 'KuriZd Empresa', 'KuriZd@empresa.com', 'pbkdf2_sha256$600000$JJKomP0E9S6u$XGFPsbnHOxQvkg9V3/QqK7NE0JBf++d455Znv+6bbRc=', 'activo', now()),
  ('4f744939-bb96-43ec-8d22-79c09d9672a4', 'KuriZd Consulta', 'KuriZd@consulta.com', 'pbkdf2_sha256$600000$6wkG3dhQoos2$UkfVpMM0Fe0EzrOJOW8/jTL/HkudVJgJa3LF4MxLTUE=', 'activo', now()),
  ('a206bb8a-eb96-4d23-9807-e234c030ba84', 'KuriZd Aspirante', 'KuriZd@aspirante.com', 'pbkdf2_sha256$600000$kdbmcYOAfWni$ej6FDIzlXAq2KYrFv4P8OpS5bQcrkb0p9NkYMRZIBEk=', 'activo', now())
ON CONFLICT DO NOTHING;

INSERT INTO usuarios_roles (usuario_id, rol_id)
SELECT u.id, r.id
FROM usuarios u
JOIN roles r ON r.clave = split_part(split_part(lower(u.email), '@', 2), '.', 1)
WHERE lower(u.email) IN (
  'kurizd@administrador.com',
  'kurizd@reclutador.com',
  'kurizd@empresa.com',
  'kurizd@consulta.com',
  'kurizd@aspirante.com'
)
ON CONFLICT (usuario_id, rol_id) DO NOTHING;
"""


BORRAR_USUARIOS_SQL = """
DELETE FROM usuarios
WHERE lower(email) IN (
  'kurizd@administrador.com',
  'kurizd@reclutador.com',
  'kurizd@empresa.com',
  'kurizd@consulta.com',
  'kurizd@aspirante.com'
);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_renombrar_certificador_empresa"),
    ]

    operations = [
        migrations.RunSQL(
            CREAR_USUARIOS_SQL,
            reverse_sql=BORRAR_USUARIOS_SQL,
        ),
    ]
