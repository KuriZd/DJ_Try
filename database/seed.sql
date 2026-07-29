-- Catálogos y datos mínimos derivados de los mocks.
-- Requiere haber ejecutado database/schema.sql.

BEGIN;

INSERT INTO roles (clave, nombre, descripcion) VALUES
  ('administrador', 'Administrador', 'Acceso completo al sistema'),
  ('reclutador', 'Reclutador', 'Gestiona vacantes, aspirantes y postulaciones'),
  ('certificador', 'Certificador', 'Gestiona la emisión de certificados'),
  ('consulta', 'Solo consulta', 'Acceso de lectura');

INSERT INTO permisos (clave, descripcion) VALUES
  ('usuarios:administrar', 'Administrar usuarios y roles'),
  ('vacantes:consultar', 'Consultar vacantes'),
  ('vacantes:administrar', 'Crear y editar vacantes'),
  ('postulaciones:consultar', 'Consultar postulaciones'),
  ('postulaciones:administrar', 'Actualizar el proceso de selección'),
  ('certificados:consultar', 'Consultar certificados'),
  ('certificados:generar', 'Generar certificados'),
  ('certificados:generar-manual', 'Forzar una emisión manual'),
  ('certificados:descargar', 'Descargar certificados'),
  ('certificados:enviar', 'Enviar certificados'),
  ('certificados:cancelar', 'Cancelar certificados'),
  ('certificados:revocar', 'Revocar certificados'),
  ('certificados:historial', 'Consultar historial'),
  ('certificados:plantillas', 'Administrar plantillas');

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permisos p
WHERE r.clave = 'administrador';

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permisos p
WHERE r.clave = 'consulta'
  AND p.clave IN (
    'vacantes:consultar',
    'postulaciones:consultar',
    'certificados:consultar',
    'certificados:historial'
  );

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permisos p
WHERE r.clave = 'reclutador'
  AND (p.clave LIKE 'vacantes:%' OR p.clave LIKE 'postulaciones:%');

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permisos p
WHERE r.clave = 'certificador' AND p.clave LIKE 'certificados:%'
  AND p.clave NOT IN ('certificados:revocar', 'certificados:plantillas');

-- Usuario administrador de prueba.
-- Credenciales locales: kurizd@djtry.local / 0330
-- La contraseña se almacena como PBKDF2-SHA256, nunca en texto plano.
INSERT INTO usuarios (
  id, nombre_completo, email, password_hash, estado, email_verificado_en
) VALUES (
  '7a15fc4f-d972-48f7-b3d4-4d5f1b877dab',
  'kurizd',
  'kurizd@djtry.local',
  'pbkdf2_sha256$600000$ELwd6GWDUmPQobJklIVC0c$K+o8b+nDTCjhXq3jUFWm7Yf+0KZSC/SwcX9UCsbX15k=',
  'activo',
  now()
);

INSERT INTO usuarios_roles (usuario_id, rol_id)
SELECT '7a15fc4f-d972-48f7-b3d4-4d5f1b877dab', id
FROM roles
WHERE clave = 'administrador';

INSERT INTO catalogo_requisitos (clave, nombre) VALUES
  ('identificacion', 'Identificación oficial'),
  ('comprobante_estudios', 'Comprobante de estudios'),
  ('curriculum', 'Currículum vigente'),
  ('evaluacion', 'Evaluación aprobada'),
  ('asistencia', 'Asistencia acreditada');

INSERT INTO tipos_certificado (clave, nombre) VALUES
  ('participacion', 'Participación'),
  ('culminacion', 'Culminación'),
  ('evaluacion', 'Evaluación aprobada'),
  ('expediente', 'Validación de expediente');

INSERT INTO plantillas_certificado
  (id, version, tipo_clave, nombre, texto_institucional, activa)
VALUES
  (
    'TPL-PART-01', '1.0', 'participacion', 'Participación en convocatoria',
    'Se otorga el presente reconocimiento por su participación activa en la convocatoria institucional.',
    true
  ),
  (
    'TPL-CULM-01', '1.2', 'culminacion', 'Culminación de proceso',
    'Se hace constar que ha concluido satisfactoriamente el proceso institucional correspondiente.',
    true
  ),
  (
    'TPL-EVAL-01', '1.0', 'evaluacion', 'Aprobación de evaluación',
    'Se acredita la aprobación de la evaluación institucional con los resultados obtenidos.',
    true
  ),
  (
    'TPL-EXP-01', '1.1', 'expediente', 'Validación de expediente',
    'Se certifica que el expediente ha sido validado conforme a los requisitos institucionales.',
    true
  );

INSERT INTO convocatorias (id, nombre, estado) VALUES
  ('AMIS-2026-01', 'Convocatoria AMIS 2026-01', 'cerrada'),
  ('AMIS-2026-02', 'Convocatoria AMIS 2026-02', 'publicada'),
  ('AMIS-2026-03', 'Convocatoria AMIS 2026-03', 'borrador');

INSERT INTO aspirantes (
  id, matricula, nombre_completo, email, telefono, cedula_profesional,
  puesto_aspirado, folio_aplicacion, estado_expediente, convocatoria_id,
  registrado_en
) VALUES
  ('ASP-001', 'AM2026-0001', 'Ana Gómez Ríos', 'ana.gomez@amis.org',
   '+52 55 1234 5678', '12345678', 'Analista de riesgos', 'APP-2026-0001',
   'activo', 'AMIS-2026-01', '2026-01-12T09:30:00Z'),
  ('ASP-002', 'AM2026-0002', 'Carlos Restrepo Vega', 'carlos.restrepo@amis.org',
   '+52 55 2345 6789', '23456789', 'Supervisor de planta', 'APP-2026-0002',
   'activo', 'AMIS-2026-01', '2026-01-15T10:00:00Z'),
  ('ASP-003', 'AM2026-0003', 'María Fernanda Loaiza', 'mf.loaiza@amis.org',
   '+52 55 3456 7890', '34567890', 'Ingeniera de proyectos', 'APP-2026-0003',
   'activo', 'AMIS-2026-02', '2026-02-01T14:20:00Z'),
  ('ASP-004', 'AM2026-0004', 'Sofía Ramírez Nieto', 'sofia.ramirez@amis.org',
   '+52 55 4567 8901', '45678901', 'Desarrolladora de software', 'APP-2026-0004',
   'activo', 'AMIS-2026-02', '2026-02-05T11:45:00Z'),
  ('ASP-005', 'AM2026-0005', 'Luis Pérez Aguilar', 'luis.perez@amis.org',
   '+52 55 5678 9012', '56789012', 'Operador industrial', 'APP-2026-0005',
   'activo', 'AMIS-2026-02', '2026-02-10T16:00:00Z'),
  ('ASP-006', 'AM2026-0006', 'Julián Torres Molina', 'julian.torres@amis.org',
   '+52 55 6789 0123', '67890123', 'Técnico de mantenimiento', 'APP-2026-0006',
   'suspendido', 'AMIS-2026-01', '2026-01-20T08:15:00Z');

INSERT INTO aspirantes_requisitos (aspirante_id, requisito_clave, cumplido)
SELECT a.id, r.clave,
  CASE
    WHEN a.id = 'ASP-004' AND r.clave = 'asistencia' THEN false
    WHEN a.id = 'ASP-005' AND r.clave IN ('comprobante_estudios', 'evaluacion') THEN false
    ELSE true
  END
FROM aspirantes a
CROSS JOIN catalogo_requisitos r;

INSERT INTO perfiles_profesionales (
  aspirante_id, nivel_educativo, institucion, carrera_especialidad,
  certificaciones, cursos_completados, empresas, puestos_anteriores,
  experiencia_meses, area_profesional, habilidades_declaradas,
  habilidades_tecnicas, habilidades_blandas, resultado_evaluaciones,
  puntaje, compatibilidad_perfil, validacion_academica,
  validacion_laboral, validacion_competencias
) VALUES
  (
    'ASP-001', 'Licenciatura', 'UNAM', 'Actuaría',
    '["CFA Nivel I","AMIS Suscripción"]', '["Solvencia II","Riesgos operativos"]',
    '["GNP Seguros","MetLife México"]', '["Analista Jr.","Analista Sr."]',
    60, 'Sector asegurador', '["SQL","Python","Excel avanzado"]',
    '["Modelos actuariales","Análisis de riesgos"]',
    '["Comunicación","Trabajo en equipo"]',
    'Aprobado con distinción', 92, 87, true, true, true
  ),
  (
    'ASP-002', 'Ingeniería', 'IPN', 'Ingeniería Industrial',
    '["Six Sigma Green Belt"]', '["Lean Manufacturing","Auditoría de procesos"]',
    '["Cementos Mexicanos","Grupo Bimbo"]',
    '["Coordinador de línea","Jefe de turno"]',
    96, 'Operaciones industriales', '["Gestión de personal","Control de calidad"]',
    '["Planeación de producción","KPIs operativos"]',
    '["Liderazgo","Resolución de conflictos"]',
    'Aprobado', 88, 82, true, true, false
  ),
  (
    'ASP-003', 'Maestría', 'Tec de Monterrey', 'Gestión de Proyectos',
    '["PMP","Scrum Master"]', '["Agile avanzado","Gestión de portafolio"]',
    '["ICA Ingeniería","Bechtel"]', '["Coordinadora de obra","PMO analyst"]',
    84, 'Construcción e ingeniería', '["MS Project","Presupuestos","Contratos"]',
    '["Planeación de proyectos","Control de costos"]',
    '["Negociación","Gestión de stakeholders"]',
    'Aprobado', 90, 85, true, true, true
  );

-- El password_hash debe producirse en el backend con Argon2id o bcrypt.
-- Ejemplo de alta (reemplazar el hash):
-- INSERT INTO usuarios (nombre_completo, email, password_hash, estado)
-- VALUES ('Administrador', 'admin@amis.org', '$argon2id$...', 'activo');

COMMIT;
