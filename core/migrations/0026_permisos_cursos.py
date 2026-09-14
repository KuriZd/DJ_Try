from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('core', '0025_cursos')]

    operations = [migrations.RunSQL(
        """
        INSERT INTO roles (clave, nombre, descripcion)
        VALUES ('instructor', 'Instructor', 'Crea y administra sus propios cursos')
        ON CONFLICT (clave) DO NOTHING;

        INSERT INTO permisos (clave, descripcion) VALUES
            ('cursos:crear', 'Crear cursos y administrar los propios'),
            ('cursos:administrar', 'Administrar todos los cursos y sus lecciones')
        ON CONFLICT (clave) DO NOTHING;

        INSERT INTO roles_permisos (rol_id, permiso_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permisos p
        WHERE (r.clave IN ('administrador', 'empresa')
               AND p.clave IN ('cursos:crear', 'cursos:administrar'))
           OR (r.clave = 'instructor' AND p.clave = 'cursos:crear')
        ON CONFLICT (rol_id, permiso_id) DO NOTHING;
        """,
        reverse_sql="""
        DELETE FROM roles_permisos
        WHERE permiso_id IN (SELECT id FROM permisos WHERE clave IN ('cursos:crear', 'cursos:administrar'));
        DELETE FROM permisos WHERE clave IN ('cursos:crear', 'cursos:administrar');
        DELETE FROM roles WHERE clave = 'instructor'
            AND NOT EXISTS (SELECT 1 FROM usuarios_roles WHERE rol_id = roles.id)
            AND NOT EXISTS (SELECT 1 FROM roles_permisos WHERE rol_id = roles.id);
        """,
    )]
