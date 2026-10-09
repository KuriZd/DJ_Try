"""Aprovisionamiento explícito, sin contraseñas compartidas ni argumentos secretos."""
import uuid
from getpass import getpass

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import EstadoUsuario, Rol, Usuario, UsuarioRol


class Command(BaseCommand):
    help = 'Crea una cuenta administradora con una contraseña elegida interactivamente.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--nombre', required=True)

    def handle(self, *args, **options):
        email = options['email'].strip()
        nombre = options['nombre'].strip()
        try:
            validate_email(email)
        except ValidationError as error:
            raise CommandError('; '.join(error.messages))
        if not nombre or len(nombre) > 180:
            raise CommandError('Indica un nombre de entre 1 y 180 caracteres.')
        if Usuario.objects.filter(email__iexact=email).exists():
            raise CommandError('Ya existe una cuenta con ese correo; no se modificó.')
        rol = Rol.objects.filter(clave='administrador').first()
        if rol is None:
            raise CommandError('Aplica las migraciones para crear el rol administrador.')
        password = getpass('Contraseña: ')
        if password != getpass('Confirma la contraseña: '):
            raise CommandError('Las contraseñas no coinciden.')
        usuario = Usuario(id=uuid.uuid4(), nombre_completo=nombre, email=email)
        try:
            validate_password(password, usuario)
        except ValidationError as error:
            raise CommandError('; '.join(error.messages))
        ahora = timezone.now()
        usuario.password_hash = make_password(password)
        usuario.estado = EstadoUsuario.ACTIVO
        usuario.creado_en = usuario.actualizado_en = ahora
        try:
            with transaction.atomic():
                usuario.save(force_insert=True)
                UsuarioRol.objects.create(usuario=usuario, rol=rol, asignado_en=ahora)
        except IntegrityError:
            raise CommandError('No se pudo crear la cuenta; verifica que el correo esté disponible.')
        self.stdout.write(self.style.SUCCESS('Cuenta administradora creada.'))
