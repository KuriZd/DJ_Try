"""Borra fotos y videos del muro que nunca llegaron a publicarse.

    python manage.py limpiar_adjuntos            mas viejos de 24 horas
    python manage.py limpiar_adjuntos --horas 6

Un adjunto nace al elegir el archivo, antes de publicar. Si la persona cierra
la pagina, cancela o la carga falla, queda sin publicacion: ocupa espacio en
S3 y no lo ve nadie. Pensado para correr una vez al dia (cron o tarea
programada). Nunca toca lo que ya forma parte de una publicacion.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import AdjuntoPublicacion
from core.services import medios_publicacion


class Command(BaseCommand):
    help = 'Borra de S3 y de la base los adjuntos del muro que no se publicaron.'

    def add_arguments(self, parser):
        parser.add_argument('--horas', type=int, default=24,
                            help='Antigüedad minima, en horas (24 por omision).')

    def handle(self, *args, **options):
        limite = timezone.now() - timedelta(hours=max(options['horas'], 1))
        with transaction.atomic():
            huerfanos = AdjuntoPublicacion.objects.select_for_update().filter(
                publicacion__isnull=True, creado_en__lt=limite,
            )
            claves = list(huerfanos.values_list('s3_key', flat=True))
            huerfanos.delete()
            transaction.on_commit(lambda: medios_publicacion.borrar(claves))
        self.stdout.write(self.style.SUCCESS(f'Adjuntos sin publicar borrados: {len(claves)}.'))
