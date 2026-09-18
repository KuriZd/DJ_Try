"""Catalogo de cursos: slug, metadatos de ficha y modulos sobre las lecciones.

El frontend publica el curso en `/explora/curso/{slug}` y su ficha muestra
resumen, nivel y objetivos, que hasta ahora no existian. El temario deja de
ser una lista plana: las lecciones cuelgan de un modulo, que es la unidad que
el administrador da de alta y ordena.

Las lecciones que ya existen se agrupan en un modulo "Contenido" por curso,
conservando su orden. El orden unico pasa del curso al modulo: dos modulos del
mismo curso pueden tener cada uno su leccion numero 1.
"""

import django.db.models.deletion
import uuid
from django.db import migrations, models
from django.utils.text import slugify


def poblar_slugs(apps, schema_editor):
    Curso = apps.get_model('core', 'Curso')
    tomados = set()
    for curso in Curso.objects.order_by('fecha_creacion', 'id'):
        base = slugify(curso.titulo)[:200] or 'curso'
        slug, intento = base, 1
        while slug in tomados or Curso.objects.filter(slug=slug).exclude(pk=curso.pk).exists():
            intento += 1
            slug = f'{base}-{intento}'
        tomados.add(slug)
        curso.slug = slug
        curso.save(update_fields=['slug'])


def agrupar_lecciones(apps, schema_editor):
    Curso = apps.get_model('core', 'Curso')
    Modulo = apps.get_model('core', 'Modulo')
    Leccion = apps.get_model('core', 'Leccion')
    for curso in Curso.objects.filter(lecciones__isnull=False).distinct():
        modulo = Modulo.objects.create(id=uuid.uuid4(), curso=curso, titulo='Contenido', orden=1)
        Leccion.objects.filter(curso=curso).update(modulo=modulo)


class Migration(migrations.Migration):
    dependencies = [('core', '0028_certificados_api')]

    operations = [
        migrations.AddField(
            model_name='curso', name='slug',
            field=models.SlugField(blank=True, db_index=False, default='', max_length=220),
            preserve_default=False,
        ),
        migrations.RunPython(poblar_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='curso', name='slug',
            field=models.SlugField(blank=True, max_length=220, unique=True),
        ),
        migrations.AddField(
            model_name='curso', name='resumen',
            field=models.CharField(blank=True, default='', max_length=300),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='curso', name='objetivos',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='curso', name='nivel',
            field=models.CharField(blank=True, choices=[('basico', 'Basico'), ('intermedio', 'Intermedio'), ('avanzado', 'Avanzado')], default='', max_length=20),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='curso', name='categoria',
            field=models.CharField(blank=True, choices=[('tecnico', 'Tecnico'), ('normativo', 'Normativo'), ('comercial', 'Comercial'), ('desarrollo', 'Desarrollo humano')], max_length=120),
        ),
        migrations.CreateModel(
            name='Modulo',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('titulo', models.CharField(max_length=200)),
                ('resumen', models.TextField(blank=True)),
                ('orden', models.PositiveIntegerField()),
                ('curso', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='modulos', to='core.curso')),
            ],
            options={'ordering': ['orden', 'id']},
        ),
        migrations.AddConstraint(
            model_name='modulo',
            constraint=models.UniqueConstraint(fields=('curso', 'orden'), name='modulo_orden_unico'),
        ),
        migrations.AddField(
            model_name='leccion', name='modulo',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='lecciones', to='core.modulo'),
        ),
        migrations.RunPython(agrupar_lecciones, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='leccion', name='curso_orden_unico'),
        migrations.RemoveField(model_name='leccion', name='curso'),
        migrations.AlterField(
            model_name='leccion', name='modulo',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lecciones', to='core.modulo'),
        ),
        migrations.AddConstraint(
            model_name='leccion',
            constraint=models.UniqueConstraint(fields=('modulo', 'orden'), name='modulo_orden_leccion_unico'),
        ),
    ]
