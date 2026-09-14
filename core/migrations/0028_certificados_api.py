import core.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0027_crud_videos')]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                """
                ALTER TABLE postulaciones ADD COLUMN folio varchar(40);
                UPDATE postulaciones SET folio = 'POST-' || replace(gen_random_uuid()::text, '-', '');
                ALTER TABLE postulaciones ALTER COLUMN folio SET NOT NULL;
                ALTER TABLE postulaciones ALTER COLUMN folio SET DEFAULT
                    ('POST-' || replace(gen_random_uuid()::text, '-', ''));
                ALTER TABLE postulaciones ADD CONSTRAINT postulaciones_folio_unique UNIQUE (folio);
                ALTER TABLE certificados ADD COLUMN archivo_pdf bytea;
                ALTER TABLE certificados ADD COLUMN postulacion_id bigint REFERENCES postulaciones(id) ON DELETE RESTRICT;
                CREATE UNIQUE INDEX certificado_postulacion_vigente_unico
                    ON certificados (postulacion_id, tipo_clave)
                    WHERE estado IN ('en_proceso', 'emitido', 'enviado', 'reenviado');
                """,
                """
                DROP INDEX certificado_postulacion_vigente_unico;
                ALTER TABLE certificados DROP COLUMN postulacion_id, DROP COLUMN archivo_pdf;
                ALTER TABLE postulaciones DROP COLUMN folio;
                """,
            )],
            state_operations=[
                migrations.AddField('postulacion', 'folio', models.CharField(
                    max_length=40, unique=True, default=core.models.nuevo_folio_postulacion, editable=False)),
                migrations.AddField('certificado', 'archivo_pdf', models.BinaryField(null=True, editable=False)),
                migrations.AddField('certificado', 'postulacion', models.ForeignKey(
                    to='core.postulacion', on_delete=django.db.models.deletion.PROTECT,
                    null=True, blank=True, related_name='certificados')),
            ],
        ),
    ]
