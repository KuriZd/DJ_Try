from django.db import migrations


# Una cuenta con rol 'aspirante' pero sin fila en `aspirantes` no se puede
# postular, y falla de la peor manera: en silencio.
#
# `PuedePostularse` resuelve el expediente con `expediente_de(request)`, que
# busca por `aspirantes.usuario_id`. Sin expediente devuelve None, `auth/me`
# manda `aspirante: null`, y el frontend deja de ofrecer el formulario y cae
# al `mailto:` de la vacante. Quien pulsa "Enviar mi CV" no ve error ninguno
# —a lo sumo se le abre el correo—, así que parece que la postulación se envió
# y simplemente no aparece.
#
# En el seed pasa con `KuriZd@aspirante.com`: se crea la cuenta y se le asigna
# el rol, pero nunca se le crea el expediente. `aspirante@amis.org` sí lo
# tiene, ligado por el UPDATE del final de seed.sql. Y las bases que ya
# existían pudieron quedar a medias, porque el INSERT de 0014 lleva
# `ON CONFLICT DO NOTHING` sin columna de conflicto: si la fila ya estaba por
# cualquier índice único, el enlace no se hizo y nadie se enteró.
#
# Va como migración y no como cambio en seed.sql porque el seed sólo corre
# dentro de 0001_load_database_sql, sobre una base recién creada; una base que
# ya existe nunca lo volvería a leer.

# 1) Lo primero es ligar, no crear: si ya hay un expediente con ese correo,
#    duplicarlo partiría en dos el historial de la misma persona.
LIGAR_EXPEDIENTES_SUELTOS = r"""
UPDATE aspirantes a
SET usuario_id = u.id
FROM usuarios u
WHERE a.usuario_id IS NULL
  AND a.eliminado_en IS NULL
  AND u.eliminado_en IS NULL
  AND lower(a.email) = lower(u.email)
  -- `usuario_id` es UNIQUE: una cuenta que ya tiene expediente no admite otro.
  AND NOT EXISTS (
    SELECT 1 FROM aspirantes otro WHERE otro.usuario_id = u.id
  );
"""

# 2) Y sólo entonces se crea el que falte.
#
#    Los consecutivos se recalculan dentro del bucle porque cada inserción
#    mueve el máximo. El de la matrícula va aparte del de la clave: hoy no
#    coinciden —ASP-AMIS-001 tiene AM2026-0007— y dar por hecho que sí
#    chocaría contra el índice único de `matricula`.
CREAR_EXPEDIENTES_FALTANTES = r"""
DO $$
DECLARE
  cuenta RECORD;
  clave_siguiente INT;
  matricula_siguiente INT;
BEGIN
  FOR cuenta IN
    SELECT u.id, u.nombre_completo, u.email
    FROM usuarios u
    JOIN usuarios_roles ur ON ur.usuario_id = u.id
    JOIN roles r ON r.id = ur.rol_id
    WHERE r.clave = 'aspirante'
      AND u.eliminado_en IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM aspirantes a
        WHERE a.usuario_id = u.id AND a.eliminado_en IS NULL
      )
      -- El correo tiene su propio índice único entre los expedientes vivos.
      -- Si ya está tomado por uno ajeno, este INSERT reventaría; se deja para
      -- que lo revise una persona en vez de adivinar a quién pertenece.
      AND NOT EXISTS (
        SELECT 1 FROM aspirantes a2
        WHERE lower(a2.email) = lower(u.email) AND a2.eliminado_en IS NULL
      )
    ORDER BY u.creado_en, u.id
  LOOP
    SELECT COALESCE(MAX((substring(id FROM '^ASP-(\d+)$'))::int), 0) + 1
      INTO clave_siguiente
      FROM aspirantes
      WHERE id ~ '^ASP-\d+$';

    SELECT COALESCE(MAX((substring(matricula FROM '^AM\d{4}-(\d+)$'))::int), 0) + 1
      INTO matricula_siguiente
      FROM aspirantes
      WHERE matricula ~ '^AM\d{4}-\d+$';

    INSERT INTO aspirantes (
      id, usuario_id, matricula, nombre_completo, email, estado_expediente
    ) VALUES (
      'ASP-' || lpad(clave_siguiente::text, 3, '0'),
      cuenta.id,
      'AM' || to_char(now(), 'YYYY') || '-' || lpad(matricula_siguiente::text, 4, '0'),
      cuenta.nombre_completo,
      cuenta.email,
      'incompleto'
    );
  END LOOP;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_initial"),
    ]

    operations = [
        migrations.RunSQL(
            [LIGAR_EXPEDIENTES_SUELTOS, CREAR_EXPEDIENTES_FALTANTES],
            # Sin vuelta atrás a propósito. Deshacer esto significaría borrar
            # expedientes que para entonces ya pueden tener postulaciones y
            # documentos colgando, y no hay forma de distinguir los que creó
            # esta migración de los que capturó alguien después.
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
