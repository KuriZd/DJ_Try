from django.db import migrations


# Separa "ver cualquier expediente" de "ver el mío". Sin este permiso, la API
# devolvía todos los aspirantes a cualquier usuario autenticado.
CREAR_PERMISO_SQL = """
INSERT INTO permisos (clave, descripcion)
VALUES (
    'aspirantes:consultar',
    'Consultar el expediente de cualquier aspirante'
)
ON CONFLICT (clave) DO NOTHING;

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permisos p
WHERE p.clave = 'aspirantes:consultar'
  AND r.clave IN ('administrador', 'reclutador', 'certificador', 'consulta')
ON CONFLICT (rol_id, permiso_id) DO NOTHING;
"""


BORRAR_PERMISO_SQL = """
DELETE FROM roles_permisos
WHERE permiso_id = (
    SELECT id FROM permisos WHERE clave = 'aspirantes:consultar'
);

DELETE FROM permisos WHERE clave = 'aspirantes:consultar';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_rol_aspirante"),
    ]

    operations = [
        migrations.RunSQL(CREAR_PERMISO_SQL, reverse_sql=BORRAR_PERMISO_SQL),
    ]
