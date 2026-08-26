from django.db import migrations


CREAR_PAGOS_PAYPAL_SQL = """
CREATE TABLE ordenes_pago_paypal (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  referencia_interna VARCHAR(50) NOT NULL UNIQUE,
  comprador_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
  reporte_id UUID NOT NULL REFERENCES reportes_psicometricos(id) ON DELETE RESTRICT,
  referencia_evaluacion_externa VARCHAR(180),
  paypal_order_id VARCHAR(64) UNIQUE,
  monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
  moneda CHAR(3) NOT NULL CHECK (moneda ~ '^[A-Z]{3}$'),
  estado VARCHAR(20) NOT NULL DEFAULT 'PENDING'
    CHECK (estado IN (
      'PENDING', 'CREATED', 'APPROVED', 'COMPLETED',
      'FAILED', 'CANCELLED', 'REFUNDED'
    )),
  clave_idempotencia VARCHAR(128),
  approval_url TEXT,
  paypal_request_id VARCHAR(64),
  respuesta_proveedor JSONB NOT NULL DEFAULT '{}'::jsonb,
  codigo_error VARCHAR(100),
  mensaje_error TEXT,
  ip INET,
  user_agent TEXT,
  expira_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  pagado_en TIMESTAMPTZ,
  CHECK (estado <> 'COMPLETED' OR pagado_en IS NOT NULL)
);

CREATE UNIQUE INDEX orden_pago_paypal_activa_unica
  ON ordenes_pago_paypal (comprador_id, reporte_id)
  WHERE estado IN ('PENDING', 'CREATED', 'APPROVED', 'COMPLETED');

CREATE UNIQUE INDEX orden_pago_paypal_idempotencia_unica
  ON ordenes_pago_paypal (comprador_id, clave_idempotencia)
  WHERE clave_idempotencia IS NOT NULL;

CREATE INDEX ordenes_pago_paypal_reporte_idx
  ON ordenes_pago_paypal (reporte_id, creado_en DESC);

CREATE TABLE transacciones_pago_paypal (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id UUID NOT NULL REFERENCES ordenes_pago_paypal(id) ON DELETE RESTRICT,
  tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('CAPTURE', 'REFUND')),
  paypal_capture_id VARCHAR(64),
  paypal_refund_id VARCHAR(64) UNIQUE,
  monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
  moneda CHAR(3) NOT NULL CHECK (moneda ~ '^[A-Z]{3}$'),
  estado VARCHAR(20) NOT NULL CHECK (estado IN (
    'PENDING', 'CREATED', 'APPROVED', 'COMPLETED',
    'FAILED', 'CANCELLED', 'REFUNDED'
  )),
  comision NUMERIC(12,2) CHECK (comision IS NULL OR comision >= 0),
  monto_neto NUMERIC(12,2),
  respuesta_proveedor JSONB NOT NULL DEFAULT '{}'::jsonb,
  procesada_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (tipo = 'CAPTURE' AND paypal_refund_id IS NULL)
    OR (tipo = 'REFUND' AND paypal_capture_id IS NOT NULL)
  )
);

CREATE INDEX transacciones_pago_paypal_orden_idx
  ON transacciones_pago_paypal (orden_id, creado_en);

CREATE UNIQUE INDEX transaccion_captura_paypal_unica
  ON transacciones_pago_paypal (paypal_capture_id)
  WHERE tipo = 'CAPTURE' AND paypal_capture_id IS NOT NULL;

CREATE TABLE eventos_pago_paypal (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id UUID REFERENCES ordenes_pago_paypal(id) ON DELETE SET NULL,
  transaccion_id UUID REFERENCES transacciones_pago_paypal(id) ON DELETE SET NULL,
  paypal_event_id VARCHAR(100) UNIQUE,
  tipo_evento VARCHAR(100) NOT NULL,
  origen VARCHAR(20) NOT NULL CHECK (origen IN ('API', 'WEBHOOK', 'SYSTEM')),
  estado_anterior VARCHAR(20),
  estado_nuevo VARCHAR(20),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  procesado BOOLEAN NOT NULL DEFAULT false,
  mensaje_error TEXT,
  recibido_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  procesado_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX eventos_pago_paypal_orden_idx
  ON eventos_pago_paypal (orden_id, creado_en);

CREATE INDEX eventos_pago_paypal_pendientes_idx
  ON eventos_pago_paypal (recibido_en)
  WHERE procesado = false;
"""


BORRAR_PAGOS_PAYPAL_SQL = """
DROP TABLE IF EXISTS eventos_pago_paypal;
DROP TABLE IF EXISTS transacciones_pago_paypal;
DROP TABLE IF EXISTS ordenes_pago_paypal;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_reportes_psicometricos_metadatos"),
    ]

    operations = [
        migrations.RunSQL(
            CREAR_PAGOS_PAYPAL_SQL,
            reverse_sql=BORRAR_PAGOS_PAYPAL_SQL,
        ),
    ]
