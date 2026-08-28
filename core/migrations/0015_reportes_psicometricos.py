from django.db import migrations


CREAR_REPORTES_SQL = """
CREATE TABLE IF NOT EXISTS reportes_psicometricos (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aspirante_id VARCHAR(30) NOT NULL REFERENCES aspirantes(id) ON DELETE RESTRICT,
  subido_por_id UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  referencia_evaluacion_externa VARCHAR(180),
  nombre_original VARCHAR(255) NOT NULL,
  archivo_clave TEXT NOT NULL,
  mime_type VARCHAR(100) NOT NULL DEFAULT 'application/pdf',
  tamano_bytes BIGINT NOT NULL CHECK (tamano_bytes > 0),
  checksum_sha256 CHAR(64) NOT NULL,
  precio NUMERIC(12,2) NOT NULL CHECK (precio >= 0),
  moneda CHAR(3) NOT NULL,
  estado VARCHAR(20) NOT NULL DEFAULT 'available'
    CHECK (estado IN ('available', 'replaced', 'disabled')),
  disponible_para_compra BOOLEAN NOT NULL DEFAULT true,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reportes_psicometricos_aspirante_idx
  ON reportes_psicometricos (aspirante_id, creado_en DESC);

CREATE UNIQUE INDEX IF NOT EXISTS reporte_psicometrico_disponible_unico
  ON reportes_psicometricos (aspirante_id)
  WHERE estado = 'available';

CREATE TABLE IF NOT EXISTS historial_reportes_psicometricos (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporte_id UUID NOT NULL REFERENCES reportes_psicometricos(id) ON DELETE CASCADE,
  accion VARCHAR(50) NOT NULL,
  realizado_por_id UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  realizado_por_email VARCHAR(254),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS historial_reportes_psicometricos_idx
  ON historial_reportes_psicometricos (reporte_id, creado_en);

INSERT INTO permisos (clave, descripcion)
VALUES (
  'reportes-psicometricos:administrar',
  'Subir y administrar reportes psicometricos'
)
ON CONFLICT (clave) DO NOTHING;

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permisos p
WHERE r.clave = 'administrador'
  AND p.clave = 'reportes-psicometricos:administrar'
ON CONFLICT (rol_id, permiso_id) DO NOTHING;
"""


BORRAR_REPORTES_SQL = """
DELETE FROM roles_permisos
WHERE permiso_id = (
  SELECT id FROM permisos WHERE clave = 'reportes-psicometricos:administrar'
);
DELETE FROM permisos WHERE clave = 'reportes-psicometricos:administrar';
DROP TABLE IF EXISTS historial_reportes_psicometricos;
DROP TABLE IF EXISTS reportes_psicometricos;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_usuarios_amis"),
    ]

    operations = [
        migrations.RunSQL(CREAR_REPORTES_SQL, reverse_sql=BORRAR_REPORTES_SQL),
    ]
