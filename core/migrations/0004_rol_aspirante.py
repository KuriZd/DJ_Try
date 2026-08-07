from django.db import migrations


# El rol se agregó a database/seed.sql para instalaciones nuevas; esta
# migración lo aplica a las bases que ya corrieron el seed original.
CREAR_ROL_SQL = """
INSERT INTO roles (clave, nombre, descripcion)
VALUES (
    'aspirante',
    'Aspirante',
    'Consulta su expediente, vacantes y certificados'
)
ON CONFLICT (clave) DO NOTHING;

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permisos p
WHERE r.clave = 'aspirante'
  AND p.clave IN (
    'vacantes:consultar',
    'postulaciones:consultar',
    'certificados:consultar',
    'certificados:descargar'
  )
ON CONFLICT (rol_id, permiso_id) DO NOTHING;
"""


BORRAR_ROL_SQL = """
DELETE FROM usuarios_roles
WHERE rol_id = (SELECT id FROM roles WHERE clave = 'aspirante');

DELETE FROM roles_permisos
WHERE rol_id = (SELECT id FROM roles WHERE clave = 'aspirante');

DELETE FROM roles WHERE clave = 'aspirante';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_link_kurizd_to_asp001"),
    ]

    operations = [
        migrations.RunSQL(CREAR_ROL_SQL, reverse_sql=BORRAR_ROL_SQL),
    ]
