"""Contenido de ejemplo para el muro de Actualiza, sólo en desarrollo.

    python manage.py sembrar_muro              crea el contenido
    python manage.py sembrar_muro --reiniciar  lo borra y lo vuelve a crear
    python manage.py sembrar_muro --limpiar    sólo lo borra

Todo cuelga de cuentas con correo @ejemplo-muro.test: así se reconoce y se
borra sin tocar lo que hayan escrito cuentas reales. Esas cuentas tienen el
correo verificado y una contraseña inutilizable; nadie entra con ellas.

Se niega a correr con DEBUG apagado: el muro es público, y un contenido de
ejemplo en producción se leería como si fuera de la comunidad.
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import (
    Comentario, EstadoUsuario, MotivoReporte, Publicacion, Reaccion, Reporte, Rol, Usuario,
    UsuarioRol,
)

DOMINIO = '@ejemplo-muro.test'

# (clave del correo, nombre). Personas ficticias.
CUENTAS = [
    ('jorge', 'Jorge Ibarra Salinas'),
    ('lucia', 'Lucía Fernández Ochoa'),
    ('mariana', 'Mariana Ortega Ruiz'),
    ('ricardo', 'Ricardo Salas Medina'),
    ('andrea', 'Andrea Cortés Villalobos'),
    ('tomas', 'Tomás Herrera Luna'),
]

# Cada publicación: autor, hace cuántos minutos, si se editó, texto,
# quiénes reaccionan y sus comentarios (autor, minutos después, texto).
PUBLICACIONES = [
    {
        'autor': 'jorge', 'hace': 50, 'editada': False,
        'cuerpo': (
            'Hoy cerramos el taller de suscripción con colegas de varias aseguradoras. '
            'Tres ideas que me llevo:\n\n'
            '1. La suscripción de riesgos climáticos ya no puede depender solo de series '
            'históricas; los modelos tienen que incorporar escenarios.\n'
            '2. La experiencia del asegurado empieza en la cotización, no en el siniestro.\n'
            '3. Formar a los agentes en lectura de pólizas reduce reclamaciones improcedentes.\n\n'
            'Gracias a quienes compartieron sus casos con tanta apertura.'
        ),
        'reacciones': ['lucia', 'mariana', 'ricardo', 'andrea'],
        'comentarios': [
            ('lucia', 5, 'Muy buen resumen, gracias por compartirlo.'),
            ('mariana', 20, 'El punto dos es clave. En mi equipo lo empezamos a medir desde la cotización.'),
        ],
    },
    {
        'autor': 'lucia', 'hace': 60 * 3, 'editada': False,
        'cuerpo': '¿Alguien ha tomado el curso de marco normativo en Explora? Busco opiniones antes de inscribirme.',
        'reacciones': ['jorge'],
        'comentarios': [
            ('ricardo', 15, 'Yo lo tomé: la parte de solvencia está muy bien explicada.'),
            ('andrea', 40, 'Recomiendo hacerlo con calma, los módulos de requerimientos de capital son densos.'),
            ('lucia', 55, '¡Gracias a los dos! Me inscribo esta semana.'),
        ],
    },
    {
        'autor': 'mariana', 'hace': 60 * 26, 'editada': True,
        'cuerpo': (
            'Terminé mi certificación en suscripción de daños. Fue un proceso exigente '
            'y muy útil; lo más valioso fue practicar con casos reales de mi cartera.'
        ),
        'reacciones': ['jorge', 'lucia', 'tomas'],
        'comentarios': [('tomas', 30, '¡Felicidades, Mariana!')],
    },
    {
        'autor': 'ricardo', 'hace': 60 * 30, 'editada': False,
        'cuerpo': (
            'Recordatorio para quienes llevan cartera de autos: revisen con sus asegurados '
            'las coberturas de responsabilidad civil antes de las vacaciones. Es la duda '
            'más frecuente que recibimos en temporada alta.'
        ),
        'reacciones': ['andrea'],
        'comentarios': [],
    },
    {
        'autor': 'andrea', 'hace': 60 * 48, 'editada': False,
        'cuerpo': (
            'Estamos buscando analista actuarial junior para el área de vida. Si conocen a '
            'alguien recién egresado con ganas de aprender, la vacante está en Aplica hoy.'
        ),
        'reacciones': ['jorge', 'mariana'],
        'comentarios': [('jorge', 90, 'La comparto con mi generación de la facultad.')],
    },
    {
        'autor': 'tomas', 'hace': 60 * 24 * 4, 'editada': False,
        'cuerpo': (
            'Comparto una lectura recomendada sobre prevención de fraude en siniestros: '
            'https://ejemplo.org/prevencion-fraude. Me ayudó a ordenar los indicadores '
            'que revisamos antes de pagar.'
        ),
        'reacciones': [],
        'comentarios': [],
    },
    {
        'autor': 'lucia', 'hace': 60 * 24 * 6, 'editada': False,
        'cuerpo': 'Gana dinero rápido desde casa, escríbeme por privado y te explico.',
        'reacciones': [],
        'comentarios': [],
        # Para que la cola de moderación tenga un caso que mostrar.
        'reportes': [
            ('mariana', MotivoReporte.SPAM, 'Parece publicidad engañosa.'),
            ('jorge', MotivoReporte.INAPROPIADO, ''),
        ],
    },
]


def cuentas_de_ejemplo():
    return Usuario.objects.filter(email__endswith=DOMINIO)


def limpiar():
    """Borra todo lo de las cuentas de ejemplo. Devuelve cuántas publicaciones."""
    ids = list(cuentas_de_ejemplo().values_list('pk', flat=True))
    Reporte.objects.filter(reportado_por__in=ids).delete()
    Reporte.objects.filter(autor_reportado__in=ids).delete()
    Reaccion.objects.filter(usuario__in=ids).delete()
    Comentario.objects.filter(autor__in=ids).delete()
    publicaciones, _ = Publicacion.objects.filter(autor__in=ids).delete()
    UsuarioRol.objects.filter(usuario__in=ids).delete()
    cuentas_de_ejemplo().delete()
    return publicaciones


def sembrar():
    ahora = timezone.now()
    rol = Rol.objects.filter(clave='aspirante').first()
    if rol is None:
        raise CommandError("Falta el rol 'aspirante'. Aplica las migraciones.")

    cuentas = {}
    for clave, nombre in CUENTAS:
        cuentas[clave] = Usuario.objects.create(
            id=uuid.uuid4(), nombre_completo=nombre, email=f'{clave}{DOMINIO}',
            password_hash=make_password(None), estado=EstadoUsuario.ACTIVO,
            email_verificado_en=ahora, creado_en=ahora, actualizado_en=ahora,
        )
        UsuarioRol.objects.create(usuario=cuentas[clave], rol=rol, asignado_en=ahora)

    for datos in PUBLICACIONES:
        publicada = ahora - timedelta(minutes=datos['hace'])
        publicacion = Publicacion.objects.create(autor=cuentas[datos['autor']], cuerpo=datos['cuerpo'])
        # La fecha es auto_now_add: se fija después para repartir el muro en el tiempo.
        Publicacion.objects.filter(pk=publicacion.pk).update(
            fecha_publicacion=publicada,
            fecha_edicion=publicada + timedelta(hours=2) if datos['editada'] else None,
        )
        Reaccion.objects.bulk_create(
            Reaccion(publicacion=publicacion, usuario=cuentas[clave]) for clave in datos['reacciones']
        )
        for autor, despues, cuerpo in datos['comentarios']:
            comentario = Comentario.objects.create(
                publicacion=publicacion, autor=cuentas[autor], cuerpo=cuerpo,
            )
            Comentario.objects.filter(pk=comentario.pk).update(
                fecha_publicacion=min(publicada + timedelta(minutes=despues), ahora),
            )
        for quien, motivo, detalle in datos.get('reportes', []):
            Reporte.objects.create(
                publicacion=publicacion, reportado_por=cuentas[quien], motivo=motivo,
                detalle=detalle, cuerpo_reportado=publicacion.cuerpo,
                autor_reportado=publicacion.autor,
            )
    return len(PUBLICACIONES)


class Command(BaseCommand):
    help = 'Crea (o borra) contenido de ejemplo en el muro de Actualiza. Sólo en desarrollo.'

    def add_arguments(self, parser):
        accion = parser.add_mutually_exclusive_group()
        accion.add_argument('--reiniciar', action='store_true',
                            help='Borra el contenido de ejemplo y lo vuelve a crear.')
        accion.add_argument('--limpiar', action='store_true',
                            help='Sólo borra el contenido de ejemplo.')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('sembrar_muro sólo corre con DEBUG activo: el muro es público.')

        with transaction.atomic():
            if options['limpiar']:
                borradas = limpiar()
                self.stdout.write(self.style.SUCCESS(
                    f'Contenido de ejemplo borrado ({borradas} publicaciones).'))
                return
            if cuentas_de_ejemplo().exists():
                if not options['reiniciar']:
                    raise CommandError(
                        'El muro ya tiene contenido de ejemplo. Usa --reiniciar para '
                        'recrearlo o --limpiar para borrarlo.')
                limpiar()
            creadas = sembrar()

        self.stdout.write(self.style.SUCCESS(
            f'Muro sembrado: {creadas} publicaciones de {len(CUENTAS)} cuentas de ejemplo '
            f'({DOMINIO}).'))
