"""Unicidad de cedulas activas, sin alterar credenciales existentes."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('core', '0034_reportes')]

    operations = [migrations.RunSQL(
        sql="""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM aspirantes
                WHERE eliminado_en IS NULL AND NULLIF(BTRIM(cedula_profesional), '') IS NOT NULL
                GROUP BY LOWER(BTRIM(cedula_profesional)) HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION 'Hay cedulas profesionales activas duplicadas. Resolverlas administrativamente antes de aplicar 0035_cedula_unica; no se modificaron credenciales.';
            END IF;
        END $$;
        CREATE UNIQUE INDEX uq_aspirantes_cedula_activa
        ON aspirantes (LOWER(BTRIM(cedula_profesional)))
        WHERE eliminado_en IS NULL AND NULLIF(BTRIM(cedula_profesional), '') IS NOT NULL;
        """,
        reverse_sql='DROP INDEX IF EXISTS uq_aspirantes_cedula_activa;',
    )]
