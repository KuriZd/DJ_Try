from django.db import migrations


# Infraestructura de correo transaccional.
#
# Dos piezas, y ninguna inventa nada:
#
# - `envios_correo` es el registro de entregas, calcado de `envios_certificado`
#   —que ya existia sin usar— y reutilizando su mismo enum `estado_envio`. Sin
#   una fila por intento no hay forma de saber que un correo no llego: el envio
#   ocurre dentro de la peticion y su fallo no se propaga.
#
# - `proposito` en `tokens_recuperacion` deja que la misma tabla sirva a la
#   verificacion de correo y a la recuperacion de contrasena. Son el mismo
#   mecanismo —token con hash, caducidad y un solo uso— y partirlo en dos
#   tablas casi identicas solo duplicaria el codigo que las cuida. La tabla
#   estaba vacia y sin uso, asi que anadir la columna no toca nada vivo.
#
# `IF NOT EXISTS` en todo, como el resto de migraciones del proyecto: en una
# base nueva `schema.sql` ya lo creo dentro de 0001, y esto debe pasar de largo.

CREAR_ENVIOS_CORREO = """
CREATE TABLE IF NOT EXISTS envios_correo (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plantilla VARCHAR(60) NOT NULL,
  -- El destinatario que el sistema QUISO usar. Cuando EMAIL_REDIRIGIR_A esta
  -- puesto el mensaje va a otra parte, pero aqui se guarda el real: el
  -- registro cuenta lo que la aplicacion decidio, no lo que hizo la jaula.
  destinatario_email VARCHAR(254) NOT NULL,
  asunto VARCHAR(255) NOT NULL,
  -- A que se refiere el envio. Permite responder "¿ya se mando el comprobante
  -- de esta orden?" sin adivinar por el asunto.
  entidad VARCHAR(60),
  entidad_id TEXT,
  estado estado_envio NOT NULL DEFAULT 'pendiente',
  numero_intento SMALLINT NOT NULL DEFAULT 1
    CHECK (numero_intento > 0),
  proveedor_id VARCHAR(180),
  mensaje_error TEXT,
  enviado_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lo que busca el comando de reintentos: los fallidos, en orden de llegada.
CREATE INDEX IF NOT EXISTS envios_correo_estado_idx
  ON envios_correo (estado, creado_en);

-- Lo que busca la comprobacion de "esto ya se envio una vez".
CREATE INDEX IF NOT EXISTS envios_correo_entidad_idx
  ON envios_correo (entidad, entidad_id);
"""

BORRAR_ENVIOS_CORREO = """
DROP TABLE IF EXISTS envios_correo;
"""

# Postgres no admite CREATE TYPE IF NOT EXISTS, de ahi el bloque.
CREAR_PROPOSITO = """
DO $$ BEGIN
  CREATE TYPE proposito_token AS ENUM ('verificacion', 'recuperacion');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE tokens_recuperacion
  ADD COLUMN IF NOT EXISTS proposito proposito_token
  NOT NULL DEFAULT 'recuperacion';

-- Un token vivo por cuenta y proposito: emitir uno nuevo invalida el anterior,
-- y este indice lo hace cumplir en la base y no solo en el codigo.
CREATE UNIQUE INDEX IF NOT EXISTS tokens_recuperacion_vigente_idx
  ON tokens_recuperacion (usuario_id, proposito)
  WHERE usado_en IS NULL;
"""

BORRAR_PROPOSITO = """
DROP INDEX IF EXISTS tokens_recuperacion_vigente_idx;
ALTER TABLE tokens_recuperacion DROP COLUMN IF EXISTS proposito;
DROP TYPE IF EXISTS proposito_token;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_expedientes_de_cuentas_aspirante"),
    ]

    operations = [
        migrations.RunSQL(CREAR_ENVIOS_CORREO, reverse_sql=BORRAR_ENVIOS_CORREO),
        migrations.RunSQL(CREAR_PROPOSITO, reverse_sql=BORRAR_PROPOSITO),
    ]
