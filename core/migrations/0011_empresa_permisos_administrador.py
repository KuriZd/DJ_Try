from django.db import migrations


IGUALAR_PERMISOS_SQL = """
INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT empresa.id, permiso.id
FROM roles empresa
CROSS JOIN permisos permiso
WHERE empresa.clave = 'empresa'
ON CONFLICT (rol_id, permiso_id) DO NOTHING;
"""


RESTAURAR_PERMISOS_SQL = """
DELETE FROM roles_permisos
WHERE rol_id = (SELECT id FROM roles WHERE clave = 'empresa');

INSERT INTO roles_permisos (rol_id, permiso_id)
SELECT empresa.id, permiso.id
FROM roles empresa
CROSS JOIN permisos permiso
WHERE empresa.clave = 'empresa'
  AND (
    (
      permiso.clave LIKE 'certificados:%'
      AND permiso.clave NOT IN (
        'certificados:revocar',
        'certificados:plantillas'
      )
    )
    OR permiso.clave = 'aspirantes:consultar'
  )
ON CONFLICT (rol_id, permiso_id) DO NOTHING;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_usuarios_demo_por_rol"),
    ]

    operations = [
        migrations.RunSQL(
            IGUALAR_PERMISOS_SQL,
            reverse_sql=RESTAURAR_PERMISOS_SQL,
        ),
    ]
