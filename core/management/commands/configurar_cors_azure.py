"""Add browser upload origins without replacing existing Blob CORS rules."""
from urllib.parse import urlsplit

from azure.core.exceptions import AzureError
from azure.storage.blob import CorsRule
from django.core.management.base import BaseCommand, CommandError

from core.services.azure_media import AzureMediaClient


class Command(BaseCommand):
    help = 'Agregar CORS al servicio Blob para los origenes exactos del frontend.'

    def add_arguments(self, parser):
        parser.add_argument('--origin', action='append', required=True)

    def handle(self, *args, **options):
        origins = sorted(set(options['origin']))
        for origin in origins:
            url = urlsplit(origin)
            if (url.scheme not in ('http', 'https') or not url.hostname or url.username
                    or url.password or url.path or url.query or url.fragment or '*' in origin):
                raise CommandError('Usar origenes exactos, sin ruta ni comodines.')
        try:
            azure = AzureMediaClient().service
            rules = list(azure.get_service_properties()['cors'])
            rule = CorsRule(
                allowed_origins=origins, allowed_methods=['GET', 'HEAD', 'PUT', 'OPTIONS'],
                allowed_headers=['content-type', 'if-none-match', 'x-ms-blob-type', 'range', 'x-ms-version'],
                exposed_headers=['Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag'],
                max_age_in_seconds=600,
            )
            if not any(sorted(r.allowed_origins) == origins and r.allowed_headers == rule.allowed_headers
                       and r.allowed_methods == rule.allowed_methods for r in rules):
                if len(rules) >= 5:
                    raise CommandError('Ya existen 5 reglas CORS; revisar antes de agregar otra.')
                azure.set_service_properties(cors=rules + [rule])
        except AzureError:
            raise CommandError('Azure no permitio configurar CORS; revisar credenciales, permisos y red.') from None
        self.stdout.write('CORS configurado para los origenes solicitados.')
