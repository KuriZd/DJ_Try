-- Ejecutar conectado a la base administrativa "postgres".
-- CREATE DATABASE no puede ejecutarse dentro de una transacción.

CREATE DATABASE "DJ_try"
  WITH
  OWNER = CURRENT_USER
  ENCODING = 'UTF8'
  TEMPLATE = template0;

-- Después conéctate a "DJ_try" y ejecuta las migraciones de Django:
--   python Prueba/manage.py migrate
