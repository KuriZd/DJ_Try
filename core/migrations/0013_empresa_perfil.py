from django.db import migrations


CREAR_EMPRESAS_SQL = """
CREATE TABLE IF NOT EXISTS empresas (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  usuario_id UUID NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE,
  razon_social VARCHAR(180) NOT NULL,
  nombre_comercial VARCHAR(180),
  rfc VARCHAR(13),
  email_contacto VARCHAR(254),
  telefono VARCHAR(30),
  sitio_web VARCHAR(255),
  sector VARCHAR(120),
  descripcion TEXT,
  direccion TEXT,
  ciudad VARCHAR(120),
  estado_region VARCHAR(120),
  codigo_postal VARCHAR(12),
  logo_url TEXT,
  registrado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  eliminado_en TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS empresas_rfc_unico
  ON empresas (lower(rfc))
  WHERE rfc IS NOT NULL AND eliminado_en IS NULL;

INSERT INTO empresas (
  usuario_id, razon_social, nombre_comercial, email_contacto
)
SELECT
  id, 'Empresa Demo, S.A. de C.V.', 'Empresa Demo', email
FROM usuarios
WHERE lower(email) = 'kurizd@empresa.com'
ON CONFLICT (usuario_id) DO NOTHING;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0012_seed_postulaciones"),
    ]

    operations = [
        migrations.RunSQL(
            CREAR_EMPRESAS_SQL,
            reverse_sql="DROP TABLE empresas;",
        ),
    ]
