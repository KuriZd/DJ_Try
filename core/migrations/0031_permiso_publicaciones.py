"""Moderacion del muro de `/actualiza`.

Publicar no pide permiso: basta una cuenta activa. Lo que si se reparte por
rol es borrar publicaciones ajenas, y por ahora solo lo trae el administrador.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('core', '0030_publicaciones')]

    operations = [migrations.RunSQL(
        """
        INSERT INTO permisos (clave, descripcion) VALUES
            ('publicaciones:administrar', 'Moderar el muro: eliminar publicaciones de cualquier cuenta')
        ON CONFLICT (clave) DO NOTHING;

        INSERT INTO roles_permisos (rol_id, permiso_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permisos p
        WHERE r.clave = 'administrador' AND p.clave = 'publicaciones:administrar'
        ON CONFLICT (rol_id, permiso_id) DO NOTHING;
        """,
        reverse_sql="""
        DELETE FROM roles_permisos
        WHERE permiso_id IN (SELECT id FROM permisos WHERE clave = 'publicaciones:administrar');
        DELETE FROM permisos WHERE clave = 'publicaciones:administrar';
        """,
    )]
