from django.db import migrations


CREAR_PAQUETES_SQL = """
-- Catalogo de paquetes. El precio vive aqui y no en el frontend: es el
-- unico numero que puede cobrarse, y el cliente no puede proponerlo.
--
-- Una fila describe un paquete cerrado (cantidad y total fijos) o uno a la
-- medida (el comprador elige la cantidad dentro del rango y el total sale de
-- multiplicar por el precio unitario). El CHECK impide filas a medias.
CREATE TABLE IF NOT EXISTS paquetes_psicometricos (
  clave VARCHAR(40) PRIMARY KEY,
  nombre VARCHAR(120) NOT NULL,
  descripcion TEXT,
  incluye JSONB NOT NULL DEFAULT '[]'::jsonb,
  cantidad_pruebas SMALLINT CHECK (cantidad_pruebas > 0),
  precio_total NUMERIC(12,2) CHECK (precio_total > 0),
  cantidad_minima SMALLINT CHECK (cantidad_minima > 0),
  cantidad_maxima SMALLINT CHECK (cantidad_maxima > 0),
  precio_unitario NUMERIC(12,2) CHECK (precio_unitario > 0),
  moneda CHAR(3) NOT NULL DEFAULT 'MXN' CHECK (moneda ~ '^[A-Z]{3}$'),
  vigencia_meses SMALLINT CHECK (vigencia_meses IS NULL OR vigencia_meses > 0),
  activo BOOLEAN NOT NULL DEFAULT true,
  orden_visual SMALLINT NOT NULL DEFAULT 0,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT paquete_psicometrico_forma_valida CHECK (
    (
      cantidad_pruebas IS NOT NULL
      AND precio_total IS NOT NULL
      AND cantidad_minima IS NULL
      AND cantidad_maxima IS NULL
      AND precio_unitario IS NULL
    )
    OR (
      cantidad_pruebas IS NULL
      AND precio_total IS NULL
      AND cantidad_minima IS NOT NULL
      AND cantidad_maxima IS NOT NULL
      AND precio_unitario IS NOT NULL
      AND cantidad_maxima >= cantidad_minima
    )
  )
);

CREATE INDEX IF NOT EXISTS paquetes_psicometricos_visibles_idx
  ON paquetes_psicometricos (orden_visual, clave)
  WHERE activo;

-- Compra de un paquete. Se crea al reservar la orden, en PENDIENTE, y pasa a
-- PAGADA cuando PayPal confirma la captura. Guarda una fotografia del nombre
-- y del precio: el catalogo cambia y el comprobante no puede cambiar con el.
CREATE TABLE IF NOT EXISTS compras_paquete_psicometrico (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  comprador_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
  paquete_clave VARCHAR(40) NOT NULL
    REFERENCES paquetes_psicometricos(clave) ON DELETE RESTRICT,
  paquete_nombre VARCHAR(120) NOT NULL,
  cantidad_pruebas SMALLINT NOT NULL CHECK (cantidad_pruebas > 0),
  precio_unitario NUMERIC(12,2) CHECK (precio_unitario > 0),
  monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
  moneda CHAR(3) NOT NULL CHECK (moneda ~ '^[A-Z]{3}$'),
  estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE'
    CHECK (estado IN ('PENDIENTE', 'PAGADA', 'CANCELADA', 'REEMBOLSADA')),
  creditos_totales SMALLINT NOT NULL CHECK (creditos_totales > 0),
  creditos_consumidos SMALLINT NOT NULL DEFAULT 0
    CHECK (creditos_consumidos >= 0),
  vigente_hasta TIMESTAMPTZ,
  pagada_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT compra_paquete_creditos_coherentes
    CHECK (creditos_consumidos <= creditos_totales),
  CONSTRAINT compra_paquete_pagada_con_fecha
    CHECK (estado <> 'PAGADA' OR pagada_en IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS compras_paquete_comprador_idx
  ON compras_paquete_psicometrico (comprador_id, creado_en DESC);

-- Para resolver "cuantas pruebas le quedan" sin recorrer todo el historial.
CREATE INDEX IF NOT EXISTS compras_paquete_con_saldo_idx
  ON compras_paquete_psicometrico (comprador_id)
  WHERE estado = 'PAGADA' AND creditos_consumidos < creditos_totales;

-- La orden de pago pasa a cobrar una de dos cosas: un reporte suelto que ya
-- esta en el expediente, o la compra de un paquete. Exactamente una.
ALTER TABLE ordenes_pago_paypal
  ALTER COLUMN reporte_id DROP NOT NULL;

ALTER TABLE ordenes_pago_paypal
  ADD COLUMN IF NOT EXISTS compra_id UUID
    REFERENCES compras_paquete_psicometrico(id) ON DELETE RESTRICT;

-- ADD CONSTRAINT no acepta IF NOT EXISTS: se pregunta antes.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'orden_pago_paypal_objeto_unico'
  ) THEN
    ALTER TABLE ordenes_pago_paypal
      ADD CONSTRAINT orden_pago_paypal_objeto_unico
        CHECK (num_nonnulls(reporte_id, compra_id) = 1);
  END IF;
END
$$;

-- El indice de orden activa por reporte no cubre las de paquete: en un indice
-- unico los NULL no chocan entre si. Cada compra lleva su propia orden.
CREATE UNIQUE INDEX IF NOT EXISTS orden_pago_paypal_compra_activa_unica
  ON ordenes_pago_paypal (compra_id)
  WHERE compra_id IS NOT NULL
    AND estado IN ('PENDING', 'CREATED', 'APPROVED', 'COMPLETED');

INSERT INTO paquetes_psicometricos (
  clave, nombre, descripcion, incluye,
  cantidad_pruebas, precio_total,
  cantidad_minima, cantidad_maxima, precio_unitario,
  moneda, vigencia_meses, orden_visual
) VALUES
  (
    'unica', 'Prueba unica',
    'Cuando ya sabes que area quieres medir.',
    '["La prueba que elijas", "Informe individual en 48 horas"]'::jsonb,
    1, 890.00, NULL, NULL, NULL, 'MXN', NULL, 1
  ),
  (
    'perfil', 'Perfil',
    'Una lectura redonda de tu candidatura.',
    '["Tres pruebas complementarias", "Informe comparado entre areas", "Vigencia de 12 meses"]'::jsonb,
    3, 2190.00, NULL, NULL, NULL, 'MXN', 12, 2
  ),
  (
    'bateria', 'Bateria completa',
    'Las cinco areas que evalua el expediente.',
    '["Las cinco areas del perfil", "Informe ejecutivo para reclutamiento", "Vigencia de 12 meses"]'::jsonb,
    5, 3290.00, NULL, NULL, NULL, 'MXN', 12, 3
  ),
  (
    'medida', 'A tu medida',
    'Elige cuantas pruebas necesitas y paga por volumen.',
    '["Precio por volumen", "Vigencia de 12 meses"]'::jsonb,
    NULL, NULL, 6, 30, 640.00, 'MXN', 12, 4
  )
ON CONFLICT (clave) DO NOTHING;
"""


BORRAR_PAQUETES_SQL = """
DROP INDEX IF EXISTS orden_pago_paypal_compra_activa_unica;

ALTER TABLE ordenes_pago_paypal
  DROP CONSTRAINT IF EXISTS orden_pago_paypal_objeto_unico;

DELETE FROM ordenes_pago_paypal WHERE reporte_id IS NULL;

ALTER TABLE ordenes_pago_paypal DROP COLUMN IF EXISTS compra_id;

ALTER TABLE ordenes_pago_paypal
  ALTER COLUMN reporte_id SET NOT NULL;

DROP TABLE IF EXISTS compras_paquete_psicometrico;
DROP TABLE IF EXISTS paquetes_psicometricos;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_reportes_psicometricos_notas"),
    ]

    operations = [
        migrations.RunSQL(CREAR_PAQUETES_SQL, reverse_sql=BORRAR_PAQUETES_SQL),
    ]
