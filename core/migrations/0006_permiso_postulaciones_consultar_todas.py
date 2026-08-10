from django.db import migrations


# Separa "ver cualquier postulación" de "ver la mía". `postulaciones:consultar`
# no servía para decidir el alcance porque el rol aspirante también lo tiene,
# justamente para consultar las suyas; con un solo permiso, el rol de sólo
# consulta se quedaba sin ver nada o el aspirante veía todo.
#
# No se otorga a `aspirante` (ve lo suyo por pertenencia) ni a `certificador`,
# que no participa en el proceso de selección.
CREAR_PERMISO_SQL = """
INSERT INTO permisos (clave, descripcion)
VALUES (
    'postulaciones:consultar-todas',
    'Consultar las postulaciones de cualquier aspirante'
)
ON CONFLICT (clave) DO NOTHING;

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permisos p
WHERE p.clave = 'postulaciones:consultar-todas'
  AND r.clave IN ('administrador', 'reclutador', 'consulta')
ON CONFLICT (rol_id, permiso_id) DO NOTHING;
"""


BORRAR_PERMISO_SQL = """
DELETE FROM roles_permisos
WHERE permiso_id = (
    SELECT id FROM permisos WHERE clave = 'postulaciones:consultar-todas'
);

DELETE FROM permisos WHERE clave = 'postulaciones:consultar-todas';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_permiso_aspirantes_consultar"),
    ]

    operations = [
        migrations.RunSQL(CREAR_PERMISO_SQL, reverse_sql=BORRAR_PERMISO_SQL),
    ]
