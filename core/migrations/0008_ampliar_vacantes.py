from django.db import migrations


AMPLIAR_VACANTES_SQL = r"""
ALTER TABLE vacantes
    ADD COLUMN IF NOT EXISTS empresa VARCHAR(150),
    ADD COLUMN IF NOT EXISTS contratacion VARCHAR(150),
    ADD COLUMN IF NOT EXISTS duracion_min_semanas SMALLINT,
    ADD COLUMN IF NOT EXISTS duracion_max_semanas SMALLINT,
    ADD COLUMN IF NOT EXISTS email_contacto VARCHAR(254),
    ADD COLUMN IF NOT EXISTS etiquetas JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS requisitos JSONB NOT NULL DEFAULT '[]'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'vacantes_duracion_min_positiva'
    ) THEN
        ALTER TABLE vacantes
            ADD CONSTRAINT vacantes_duracion_min_positiva
            CHECK (duracion_min_semanas IS NULL OR duracion_min_semanas > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'vacantes_duracion_rango_valido'
    ) THEN
        ALTER TABLE vacantes
            ADD CONSTRAINT vacantes_duracion_rango_valido
            CHECK (
                duracion_max_semanas IS NULL
                OR duracion_min_semanas IS NULL
                OR duracion_max_semanas >= duracion_min_semanas
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'vacantes_etiquetas_array'
    ) THEN
        ALTER TABLE vacantes
            ADD CONSTRAINT vacantes_etiquetas_array
            CHECK (jsonb_typeof(etiquetas) = 'array');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'vacantes_requisitos_array'
    ) THEN
        ALTER TABLE vacantes
            ADD CONSTRAINT vacantes_requisitos_array
            CHECK (jsonb_typeof(requisitos) = 'array');
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS vacantes_publicadas_fecha_idx
    ON vacantes (publicada_en DESC)
    WHERE estado = 'publicada';

CREATE INDEX IF NOT EXISTS vacantes_etiquetas_idx
    ON vacantes USING GIN (etiquetas);

INSERT INTO vacantes (
    titulo, empresa, departamento, descripcion, estado, publicada_en,
    modalidad, jornada, contratacion, duracion_min_semanas,
    duracion_max_semanas, email_contacto, etiquetas, requisitos
)
SELECT
    'Consultor SAP Customer Checkout (POS SAP)',
    'Ene8',
    'Consultoría SAP',
    'En Ene8 estamos en búsqueda de un Consultor SAP Customer Checkout (POS SAP) para participar en un proyecto de implementación.',
    'publicada',
    '2026-08-05T00:00:00-06:00'::timestamptz,
    'hibrido',
    'tiempo_completo',
    'Por proyecto / Prestación de servicios / Freelance',
    6,
    10,
    'recursoshumanos@ene8.com.mx',
    '["SAP CCO", "SAP Business One", "S/4HANA", "SAP ECC", "Punto de Venta"]'::jsonb,
    '["Experiencia en SAP Customer Checkout (CCO).", "Implementación e integración con SAP Business One, SAP S/4HANA o SAP ECC.", "Conocimiento de procesos de Punto de Venta (POS).", "Configuración de medios de pago, impuestos, promociones, clientes e inventarios.", "Experiencia en pruebas funcionales, capacitación y soporte a usuarios."]'::jsonb
WHERE NOT EXISTS (
    SELECT 1
    FROM vacantes
    WHERE titulo = 'Consultor SAP Customer Checkout (POS SAP)'
      AND empresa = 'Ene8'
);
"""


REVERTIR_VACANTES_SQL = r"""
DROP INDEX IF EXISTS vacantes_etiquetas_idx;
DROP INDEX IF EXISTS vacantes_publicadas_fecha_idx;

ALTER TABLE vacantes
    DROP CONSTRAINT IF EXISTS vacantes_requisitos_array,
    DROP CONSTRAINT IF EXISTS vacantes_etiquetas_array,
    DROP CONSTRAINT IF EXISTS vacantes_duracion_rango_valido,
    DROP CONSTRAINT IF EXISTS vacantes_duracion_min_positiva,
    DROP COLUMN IF EXISTS requisitos,
    DROP COLUMN IF EXISTS etiquetas,
    DROP COLUMN IF EXISTS email_contacto,
    DROP COLUMN IF EXISTS duracion_max_semanas,
    DROP COLUMN IF EXISTS duracion_min_semanas,
    DROP COLUMN IF EXISTS contratacion,
    DROP COLUMN IF EXISTS empresa;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_fecha_nacimiento_aspirante"),
    ]

    operations = [
        migrations.RunSQL(
            AMPLIAR_VACANTES_SQL,
            reverse_sql=REVERTIR_VACANTES_SQL,
        ),
    ]
