-- AISER / AMIS - Esquema inicial
-- Motor: PostgreSQL 15+
-- Ejecutar primero este archivo y después database/seed.sql.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE estado_usuario AS ENUM ('pendiente', 'activo', 'bloqueado', 'inactivo');
CREATE TYPE estado_convocatoria AS ENUM ('borrador', 'publicada', 'cerrada', 'cancelada');
CREATE TYPE estado_vacante AS ENUM ('borrador', 'publicada', 'pausada', 'cerrada', 'cancelada');
CREATE TYPE modalidad_vacante AS ENUM ('presencial', 'remoto', 'hibrido');
CREATE TYPE jornada_vacante AS ENUM ('tiempo_completo', 'medio_tiempo', 'estacional');
CREATE TYPE estado_postulacion AS ENUM ('nuevo', 'revision', 'shortlist', 'rechazado', 'contratado');
CREATE TYPE estado_expediente AS ENUM ('activo', 'incompleto', 'suspendido', 'cerrado');
CREATE TYPE estado_certificado AS ENUM ('en_proceso', 'emitido', 'enviado', 'reenviado', 'cancelado', 'revocado');
CREATE TYPE tipo_generacion AS ENUM ('automatica', 'manual');
CREATE TYPE estado_envio AS ENUM ('pendiente', 'enviado', 'fallido');

-- Autenticación y autorización
CREATE TABLE roles (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  clave VARCHAR(50) NOT NULL UNIQUE,
  nombre VARCHAR(100) NOT NULL,
  descripcion TEXT,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE permisos (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  clave VARCHAR(100) NOT NULL UNIQUE,
  descripcion TEXT
);

CREATE TABLE roles_permisos (
  rol_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permiso_id BIGINT NOT NULL REFERENCES permisos(id) ON DELETE CASCADE,
  PRIMARY KEY (rol_id, permiso_id)
);

CREATE TABLE usuarios (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre_completo VARCHAR(180) NOT NULL,
  email VARCHAR(254) NOT NULL,
  password_hash TEXT NOT NULL,
  estado estado_usuario NOT NULL DEFAULT 'pendiente',
  email_verificado_en TIMESTAMPTZ,
  ultimo_acceso_en TIMESTAMPTZ,
  intentos_fallidos SMALLINT NOT NULL DEFAULT 0 CHECK (intentos_fallidos >= 0),
  bloqueado_hasta TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  eliminado_en TIMESTAMPTZ
);

CREATE UNIQUE INDEX usuarios_email_unico
  ON usuarios (lower(email))
  WHERE eliminado_en IS NULL;

CREATE TABLE usuarios_roles (
  usuario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  rol_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
  asignado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (usuario_id, rol_id)
);

-- Se guarda únicamente el hash del token, nunca el token en claro.
CREATE TABLE sesiones (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  refresh_token_hash TEXT NOT NULL UNIQUE,
  ip INET,
  user_agent TEXT,
  expira_en TIMESTAMPTZ NOT NULL,
  revocada_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX sesiones_usuario_idx ON sesiones (usuario_id, expira_en);

CREATE TABLE tokens_recuperacion (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expira_en TIMESTAMPTZ NOT NULL,
  usado_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Procesos, vacantes y personas
CREATE TABLE convocatorias (
  id VARCHAR(30) PRIMARY KEY,
  nombre VARCHAR(180) NOT NULL,
  descripcion TEXT,
  estado estado_convocatoria NOT NULL DEFAULT 'borrador',
  inicia_en TIMESTAMPTZ,
  termina_en TIMESTAMPTZ,
  creado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (termina_en IS NULL OR inicia_en IS NULL OR termina_en >= inicia_en)
);

CREATE TABLE vacantes (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  convocatoria_id VARCHAR(30) REFERENCES convocatorias(id) ON DELETE SET NULL,
  titulo VARCHAR(180) NOT NULL,
  departamento VARCHAR(120),
  descripcion TEXT,
  modalidad modalidad_vacante NOT NULL,
  jornada jornada_vacante NOT NULL DEFAULT 'tiempo_completo',
  ciudad VARCHAR(120),
  estado_region VARCHAR(120),
  salario_min NUMERIC(12,2),
  salario_max NUMERIC(12,2),
  moneda CHAR(3) NOT NULL DEFAULT 'MXN',
  estado estado_vacante NOT NULL DEFAULT 'borrador',
  publicada_en TIMESTAMPTZ,
  cierra_en TIMESTAMPTZ,
  creado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (salario_min IS NULL OR salario_min >= 0),
  CHECK (salario_max IS NULL OR salario_max >= 0),
  CHECK (salario_max IS NULL OR salario_min IS NULL OR salario_max >= salario_min)
);

CREATE INDEX vacantes_busqueda_idx ON vacantes (estado, departamento, modalidad);

CREATE TABLE aspirantes (
  id VARCHAR(30) PRIMARY KEY,
  usuario_id UUID UNIQUE REFERENCES usuarios(id) ON DELETE SET NULL,
  matricula VARCHAR(40) NOT NULL UNIQUE,
  nombre_completo VARCHAR(180) NOT NULL,
  email VARCHAR(254) NOT NULL,
  telefono VARCHAR(30),
  cedula_profesional VARCHAR(30),
  direccion TEXT,
  ciudad VARCHAR(120),
  codigo_postal VARCHAR(12),
  estado_region VARCHAR(120),
  puesto_aspirado VARCHAR(180),
  folio_aplicacion VARCHAR(40) UNIQUE,
  estado_expediente estado_expediente NOT NULL DEFAULT 'incompleto',
  convocatoria_id VARCHAR(30) REFERENCES convocatorias(id) ON DELETE SET NULL,
  registrado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  eliminado_en TIMESTAMPTZ
);

CREATE UNIQUE INDEX aspirantes_email_unico
  ON aspirantes (lower(email))
  WHERE eliminado_en IS NULL;

CREATE TABLE perfiles_profesionales (
  aspirante_id VARCHAR(30) PRIMARY KEY REFERENCES aspirantes(id) ON DELETE CASCADE,
  nivel_educativo VARCHAR(100),
  institucion VARCHAR(180),
  carrera_especialidad VARCHAR(180),
  certificaciones JSONB NOT NULL DEFAULT '[]'::jsonb,
  cursos_completados JSONB NOT NULL DEFAULT '[]'::jsonb,
  empresas JSONB NOT NULL DEFAULT '[]'::jsonb,
  puestos_anteriores JSONB NOT NULL DEFAULT '[]'::jsonb,
  experiencia_meses INTEGER CHECK (experiencia_meses IS NULL OR experiencia_meses >= 0),
  area_profesional VARCHAR(180),
  habilidades_declaradas JSONB NOT NULL DEFAULT '[]'::jsonb,
  habilidades_tecnicas JSONB NOT NULL DEFAULT '[]'::jsonb,
  habilidades_blandas JSONB NOT NULL DEFAULT '[]'::jsonb,
  resultado_evaluaciones TEXT,
  puntaje NUMERIC(5,2) CHECK (puntaje IS NULL OR puntaje BETWEEN 0 AND 100),
  compatibilidad_perfil NUMERIC(5,2)
    CHECK (compatibilidad_perfil IS NULL OR compatibilidad_perfil BETWEEN 0 AND 100),
  validacion_academica BOOLEAN NOT NULL DEFAULT false,
  validacion_laboral BOOLEAN NOT NULL DEFAULT false,
  validacion_competencias BOOLEAN NOT NULL DEFAULT false,
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE catalogo_requisitos (
  clave VARCHAR(50) PRIMARY KEY,
  nombre VARCHAR(140) NOT NULL,
  descripcion TEXT,
  activo BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE aspirantes_requisitos (
  aspirante_id VARCHAR(30) NOT NULL REFERENCES aspirantes(id) ON DELETE CASCADE,
  requisito_clave VARCHAR(50) NOT NULL REFERENCES catalogo_requisitos(clave) ON DELETE RESTRICT,
  cumplido BOOLEAN NOT NULL DEFAULT false,
  validado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  validado_en TIMESTAMPTZ,
  observaciones TEXT,
  PRIMARY KEY (aspirante_id, requisito_clave)
);

CREATE TABLE postulaciones (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  aspirante_id VARCHAR(30) NOT NULL REFERENCES aspirantes(id) ON DELETE RESTRICT,
  vacante_id BIGINT NOT NULL REFERENCES vacantes(id) ON DELETE RESTRICT,
  estado estado_postulacion NOT NULL DEFAULT 'nuevo',
  etapa VARCHAR(120) NOT NULL DEFAULT 'Postulación recibida',
  progreso SMALLINT NOT NULL DEFAULT 0 CHECK (progreso BETWEEN 0 AND 100),
  match_score SMALLINT CHECK (match_score IS NULL OR match_score BETWEEN 0 AND 100),
  experiencia_meses INTEGER CHECK (experiencia_meses IS NULL OR experiencia_meses >= 0),
  ultimo_empleo VARCHAR(220),
  expectativas_salariales NUMERIC(12,2),
  horas_deseadas SMALLINT CHECK (horas_deseadas IS NULL OR horas_deseadas > 0),
  disponibilidad JSONB NOT NULL DEFAULT '[]'::jsonb,
  registrada_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  ultima_actividad_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (aspirante_id, vacante_id)
);

CREATE INDEX postulaciones_vacante_estado_idx ON postulaciones (vacante_id, estado);

CREATE TABLE documentos_aspirante (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aspirante_id VARCHAR(30) NOT NULL REFERENCES aspirantes(id) ON DELETE CASCADE,
  postulacion_id BIGINT REFERENCES postulaciones(id) ON DELETE SET NULL,
  tipo VARCHAR(50) NOT NULL,
  nombre_original VARCHAR(255) NOT NULL,
  almacenamiento_clave TEXT NOT NULL,
  mime_type VARCHAR(100) NOT NULL,
  tamano_bytes BIGINT NOT NULL CHECK (tamano_bytes > 0),
  checksum_sha256 CHAR(64),
  subido_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Certificados
CREATE TABLE tipos_certificado (
  clave VARCHAR(40) PRIMARY KEY,
  nombre VARCHAR(120) NOT NULL,
  activo BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE plantillas_certificado (
  id VARCHAR(40) NOT NULL,
  version VARCHAR(20) NOT NULL,
  tipo_clave VARCHAR(40) NOT NULL REFERENCES tipos_certificado(clave) ON DELETE RESTRICT,
  nombre VARCHAR(180) NOT NULL,
  texto_institucional TEXT NOT NULL,
  configuracion JSONB NOT NULL DEFAULT '{}'::jsonb,
  activa BOOLEAN NOT NULL DEFAULT true,
  creado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id, version)
);

CREATE TABLE certificados (
  id VARCHAR(60) PRIMARY KEY,
  folio VARCHAR(40) NOT NULL UNIQUE,
  codigo_verificacion VARCHAR(64) NOT NULL UNIQUE,
  aspirante_id VARCHAR(30) REFERENCES aspirantes(id) ON DELETE SET NULL,
  -- Evidencia inmutable de los datos usados al emitir.
  aspirante_snapshot JSONB NOT NULL,
  tipo_clave VARCHAR(40) NOT NULL REFERENCES tipos_certificado(clave) ON DELETE RESTRICT,
  proceso_id VARCHAR(30) REFERENCES convocatorias(id) ON DELETE SET NULL,
  proceso_nombre VARCHAR(180) NOT NULL,
  plantilla_id VARCHAR(40) NOT NULL,
  plantilla_version VARCHAR(20) NOT NULL,
  estado estado_certificado NOT NULL DEFAULT 'en_proceso',
  resultado TEXT,
  periodo_participacion VARCHAR(100),
  tipo_generacion tipo_generacion NOT NULL DEFAULT 'automatica',
  justificacion_manual TEXT,
  autoridad_emisora VARCHAR(180),
  cargo_autoridad VARCHAR(180),
  observaciones_internas TEXT,
  emitido_en TIMESTAMPTZ,
  emitido_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  certificado_anterior_id VARCHAR(60) REFERENCES certificados(id) ON DELETE SET NULL,
  reemplazado_por_id VARCHAR(60) REFERENCES certificados(id) ON DELETE SET NULL,
  cancelado_en TIMESTAMPTZ,
  cancelado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  motivo_cancelacion TEXT,
  revocado_en TIMESTAMPTZ,
  revocado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  motivo_revocacion TEXT,
  enviado_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (plantilla_id, plantilla_version)
    REFERENCES plantillas_certificado(id, version) ON DELETE RESTRICT,
  CHECK (tipo_generacion <> 'manual' OR nullif(trim(justificacion_manual), '') IS NOT NULL)
);

CREATE INDEX certificados_aspirante_idx ON certificados (aspirante_id);
CREATE INDEX certificados_filtros_idx ON certificados (estado, tipo_clave, proceso_id, emitido_en DESC);
CREATE UNIQUE INDEX certificado_vigente_unico
  ON certificados (aspirante_id, tipo_clave, proceso_id)
  WHERE estado IN ('en_proceso', 'emitido', 'enviado', 'reenviado');

CREATE TABLE historial_certificados (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  certificado_id VARCHAR(60) NOT NULL REFERENCES certificados(id) ON DELETE CASCADE,
  accion VARCHAR(50) NOT NULL,
  estado_anterior estado_certificado,
  estado_nuevo estado_certificado,
  realizado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  realizado_por_email VARCHAR(254),
  descripcion TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX historial_certificado_idx
  ON historial_certificados (certificado_id, creado_en);

CREATE TABLE envios_certificado (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  certificado_id VARCHAR(60) NOT NULL REFERENCES certificados(id) ON DELETE CASCADE,
  destinatario_email VARCHAR(254) NOT NULL,
  enviado_por UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  estado estado_envio NOT NULL DEFAULT 'pendiente',
  numero_intento SMALLINT NOT NULL DEFAULT 1 CHECK (numero_intento > 0),
  proveedor_id VARCHAR(180),
  mensaje_error TEXT,
  enviado_en TIMESTAMPTZ,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX envios_certificado_idx
  ON envios_certificado (certificado_id, creado_en);

-- Auditoría transversal para operaciones administrativas.
CREATE TABLE auditoria (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  usuario_id UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  accion VARCHAR(100) NOT NULL,
  entidad VARCHAR(80) NOT NULL,
  entidad_id TEXT,
  datos_anteriores JSONB,
  datos_nuevos JSONB,
  ip INET,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX auditoria_entidad_idx ON auditoria (entidad, entidad_id, creado_en);
CREATE INDEX auditoria_usuario_idx ON auditoria (usuario_id, creado_en);

COMMIT;
