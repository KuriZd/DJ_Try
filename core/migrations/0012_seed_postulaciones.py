from django.db import migrations


# `database/seed.sql` deja vacantes, aspirantes y perfiles, pero ninguna
# postulación: la tabla quedaba vacía y `GET /api/postulaciones/` respondía
# `[]`, así que el panel de reclutamiento no se podía probar de punta a punta.
#
# Va como migración y no como cambio en seed.sql porque el seed sólo corre
# dentro de 0001_load_database_sql, sobre una base recién creada; una base que
# ya existe nunca lo volvería a leer.
#
# `PostulacionViewSet` es de sólo lectura y no hay endpoint de alta, de modo
# que hoy éste es el único camino para tener datos con los que trabajar.
#
# Los cinco registros cubren un valor de `estado_postulacion` cada uno, y
# ASP-003 va con `match_score`, `experiencia_meses` y `ultimo_empleo` en NULL
# a propósito: son columnas nullable y la UI tiene que aguantarlas.
SEED_POSTULACIONES_SQL = """
INSERT INTO postulaciones (
  aspirante_id, vacante_id, estado, etapa, progreso, match_score,
  experiencia_meses, ultimo_empleo, expectativas_salariales, horas_deseadas,
  disponibilidad, registrada_en, ultima_actividad_en
)
SELECT
  datos.aspirante_id,
  v.id,
  datos.estado::estado_postulacion,
  datos.etapa,
  datos.progreso,
  datos.match_score,
  datos.experiencia_meses,
  datos.ultimo_empleo,
  datos.expectativas_salariales,
  datos.horas_deseadas,
  datos.disponibilidad::jsonb,
  datos.registrada_en::timestamptz,
  datos.ultima_actividad_en::timestamptz
FROM vacantes v
CROSS JOIN (VALUES
  ('ASP-001', 'shortlist', 'Entrevista técnica', 75, 92, 60,
   'Analista Sr. @ GNP Seguros', 48000.00, 40,
   '["Lunes a viernes", "Turno matutino"]',
   '2026-08-14T09:30:00-06:00', '2026-08-17T11:00:00-06:00'),
  ('ASP-002', 'revision', 'Screening telefónico', 40, 71, 96,
   'Jefe de turno @ Grupo Bimbo', 38000.00, 45,
   '["Lunes a sábado"]',
   '2026-08-12T10:15:00-06:00', '2026-08-16T08:20:00-06:00'),
  ('ASP-003', 'nuevo', 'Postulación recibida', 10, NULL, NULL,
   NULL, NULL, NULL,
   '[]',
   '2026-08-16T14:05:00-06:00', '2026-08-16T14:05:00-06:00'),
  ('ASP-004', 'contratado', 'Oferta aceptada', 100, 88, 42,
   'Desarrolladora full stack @ Kavak', 55000.00, 40,
   '["Lunes a viernes"]',
   '2026-07-28T09:00:00-06:00', '2026-08-11T17:40:00-06:00'),
  ('ASP-005', 'rechazado', 'Descartado en evaluación', 100, 45, 24,
   'Operador @ Cementos Mexicanos', 22000.00, 48,
   '["Rotativo"]',
   '2026-08-03T16:30:00-06:00', '2026-08-09T12:00:00-06:00')
) AS datos (
  aspirante_id, estado, etapa, progreso, match_score, experiencia_meses,
  ultimo_empleo, expectativas_salariales, horas_deseadas, disponibilidad,
  registrada_en, ultima_actividad_en
)
WHERE v.titulo = 'Consultor SAP Customer Checkout (POS SAP)'
  AND EXISTS (
    SELECT 1 FROM aspirantes a WHERE a.id = datos.aspirante_id
  )
ON CONFLICT (aspirante_id, vacante_id) DO NOTHING;
"""


# Sólo borra lo que esta migración pudo haber insertado. Si alguien capturó
# otra postulación para los mismos aspirantes en otra vacante, se queda.
BORRAR_POSTULACIONES_SQL = """
DELETE FROM postulaciones
WHERE aspirante_id IN ('ASP-001', 'ASP-002', 'ASP-003', 'ASP-004', 'ASP-005')
  AND vacante_id IN (
    SELECT id FROM vacantes
    WHERE titulo = 'Consultor SAP Customer Checkout (POS SAP)'
  );
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_empresa_permisos_administrador"),
    ]

    operations = [
        migrations.RunSQL(
            SEED_POSTULACIONES_SQL, reverse_sql=BORRAR_POSTULACIONES_SQL
        ),
    ]
