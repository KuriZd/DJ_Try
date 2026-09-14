from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from core.models import Video, VideoRendition


class Command(BaseCommand):
    help = 'Verifica migracion, columnas, PK, FK y unicidad de videos (solo lectura).'

    def handle(self, *args, **options):
        if not MigrationRecorder(connection).migration_qs.filter(
            app='core', name='0023_videos_s3'
        ).exists():
            raise CommandError('0023_videos_s3 no esta aplicada en esta base.')
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            for model in (Video, VideoRendition):
                table = model._meta.db_table
                if table not in tables:
                    raise CommandError(f'Falta la tabla {table}.')
                columns = {c.name for c in connection.introspection.get_table_description(cursor, table)}
                missing = {f.column for f in model._meta.local_fields} - columns
                if missing:
                    raise CommandError(f'Faltan columnas en {table}: {sorted(missing)}')
                constraints = connection.introspection.get_constraints(cursor, table)
                if not any(c['primary_key'] and c['columns'] == ['id'] for c in constraints.values()):
                    raise CommandError(f'Falta PK id en {table}.')
                for field in model._meta.local_fields:
                    if field.many_to_one:
                        target = (field.related_model._meta.db_table, field.target_field.column)
                        if not any(c['columns'] == [field.column] and c['foreign_key'] == target
                                   for c in constraints.values()):
                            raise CommandError(f'Falta FK {table}.{field.column}.')
                if model is VideoRendition:
                    for names in (['s3_key'], ['video_id', 'profile']):
                        if not any(c['unique'] and c['columns'] == names for c in constraints.values()):
                            raise CommandError(f'Falta unicidad {table}: {names}.')
                self.stdout.write(self.style.SUCCESS(f'OK: {table}, columnas y restricciones.'))
