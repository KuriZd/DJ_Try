from django.db import migrations


AMPLIAR_REPORTES_SQL = """
-- El archivero del aspirante es histórico: conserva un reporte por evaluación
-- aplicada, no uno solo. El índice anterior permitía un único reporte
-- disponible por persona, así que se sustituye por uno que sigue impidiendo
-- duplicados de la MISMA evaluación externa.
DROP INDEX IF EXISTS reporte_psicometrico_disponible_unico;

CREATE UNIQUE INDEX IF NOT EXISTS reporte_psicometrico_evaluacion_unico
  ON reportes_psicometricos (aspirante_id, referencia_evaluacion_externa)
  WHERE estado = 'available' AND referencia_evaluacion_externa IS NOT NULL;

-- Quién produjo el documento. Determina también si la plataforma puede
-- ponerlo a la venta: lo que sube el propio aspirante no se comercializa.
ALTER TABLE reportes_psicometricos
  ADD COLUMN IF NOT EXISTS origen VARCHAR(20) NOT NULL DEFAULT 'plataforma'
    CHECK (origen IN ('plataforma', 'propia'));

-- Metadatos de la evaluación. Sin ellos el reporte es un PDF suelto: no se
-- puede agrupar por área, ni saber si sigue vigente, ni dibujar el perfil.
ALTER TABLE reportes_psicometricos
  ADD COLUMN IF NOT EXISTS area_clave VARCHAR(40) NOT NULL DEFAULT 'otra',
  ADD COLUMN IF NOT EXISTS aplicada_en TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS vigente_hasta TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS puntaje SMALLINT
    CHECK (puntaje IS NULL OR (puntaje >= 0 AND puntaje <= 100)),
  ADD COLUMN IF NOT EXISTS nivel VARCHAR(40),
  ADD COLUMN IF NOT EXISTS escalas JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS paginas INTEGER
    CHECK (paginas IS NULL OR paginas >= 0);

-- La vigencia no puede terminar antes de la aplicación.
ALTER TABLE reportes_psicometricos
  DROP CONSTRAINT IF EXISTS reporte_psicometrico_vigencia_coherente;
ALTER TABLE reportes_psicometricos
  ADD CONSTRAINT reporte_psicometrico_vigencia_coherente
  CHECK (
    vigente_hasta IS NULL
    OR aplicada_en IS NULL
    OR vigente_hasta >= aplicada_en
  );

-- El archivero se abre por área y por fecha de aplicación.
CREATE INDEX IF NOT EXISTS reportes_psicometricos_area_idx
  ON reportes_psicometricos (aspirante_id, area_clave, aplicada_en DESC);

-- Subir el reporte propio no es administrar los de todos: son dos permisos.
INSERT INTO permisos (clave, descripcion)
VALUES (
  'reportes-psicometricos:subir-propio',
  'Subir reportes psicometricos al expediente propio'
)
ON CONFLICT (clave) DO NOTHING;

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permisos p
WHERE r.clave IN ('administrador', 'aspirante')
  AND p.clave = 'reportes-psicometricos:subir-propio'
ON CONFLICT (rol_id, permiso_id) DO NOTHING;
"""


REVERTIR_REPORTES_SQL = """
DELETE FROM roles_permisos
WHERE permiso_id = (
  SELECT id FROM permisos WHERE clave = 'reportes-psicometricos:subir-propio'
);
DELETE FROM permisos WHERE clave = 'reportes-psicometricos:subir-propio';

DROP INDEX IF EXISTS reportes_psicometricos_area_idx;

ALTER TABLE reportes_psicometricos
  DROP CONSTRAINT IF EXISTS reporte_psicometrico_vigencia_coherente;

ALTER TABLE reportes_psicometricos
  DROP COLUMN IF EXISTS paginas,
  DROP COLUMN IF EXISTS escalas,
  DROP COLUMN IF EXISTS nivel,
  DROP COLUMN IF EXISTS puntaje,
  DROP COLUMN IF EXISTS vigente_hasta,
  DROP COLUMN IF EXISTS aplicada_en,
  DROP COLUMN IF EXISTS area_clave,
  DROP COLUMN IF EXISTS origen;

DROP INDEX IF EXISTS reporte_psicometrico_evaluacion_unico;

-- Al volver atrás sólo puede quedar un reporte disponible por aspirante: se
-- conserva el más reciente y el resto se marca como reemplazado, o el índice
-- único no se podría crear.
UPDATE reportes_psicometricos AS r
SET estado = 'replaced', disponible_para_compra = false, actualizado_en = now()
WHERE estado = 'available'
  AND EXISTS (
    SELECT 1 FROM reportes_psicometricos AS otro
    WHERE otro.aspirante_id = r.aspirante_id
      AND otro.estado = 'available'
      AND (otro.creado_en, otro.id) > (r.creado_en, r.id)
  );

CREATE UNIQUE INDEX IF NOT EXISTS reporte_psicometrico_disponible_unico
  ON reportes_psicometricos (aspirante_id)
  WHERE estado = 'available';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_reportes_psicometricos"),
    ]

    operations = [
        migrations.RunSQL(AMPLIAR_REPORTES_SQL, reverse_sql=REVERTIR_REPORTES_SQL),
    ]
