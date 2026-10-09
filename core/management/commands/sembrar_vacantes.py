"""Vacantes de ejemplo para probar la bolsa de trabajo, sólo en desarrollo.

    python manage.py sembrar_vacantes              crea las que falten
    python manage.py sembrar_vacantes --reiniciar  las borra y las vuelve a crear
    python manage.py sembrar_vacantes --limpiar    sólo las borra

Cada vacante cubre un caso de la tarjeta de Aplica hoy: duración cerrada,
abierta por arriba o por abajo, indefinida; etiquetas de sobra para el "+N";
sin correo ni requisitos (sin "Ver detalle"); título largo; convocatoria que
cierra pronto; y, fuera del listado público, borrador, pausada, cerrada y
publicada con fecha vencida para el panel de administración.

Se reconocen por la empresa ficticia más el título, así que borrarlas no toca
vacantes reales. Una vacante con postulaciones no se borra —la llave es
PROTECT y llevarse las postulaciones de alguien sería peor—: se informa y se
deja.

Se niega a correr con DEBUG apagado: la bolsa de trabajo es pública.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import EstadoVacante, JornadaVacante, ModalidadVacante, Vacante

DOMINIO = '@ejemplo-vacantes.test'

PUBLICADA = EstadoVacante.PUBLICADA
REMOTO, HIBRIDO, PRESENCIAL = (
    ModalidadVacante.REMOTO, ModalidadVacante.HIBRIDO, ModalidadVacante.PRESENCIAL,
)

# `publicada_hace` y `cierra_en` en días respecto a hoy; `cierra_en` negativo
# es una convocatoria ya vencida. Empresas y correos son ficticios.
VACANTES = [
    {
        'titulo': 'Ingeniero(a) de Datos',
        'empresa': 'Grupo Cobalto', 'departamento': 'Analítica',
        'descripcion': 'Diseña y mantiene los pipelines que alimentan los tableros '
                       'de operación y los modelos de pronóstico de demanda.',
        'modalidad': REMOTO, 'contratacion': 'Tiempo completo',
        'duracion': (12, 24), 'correo': 'talento',
        'etiquetas': ['Python', 'SQL', 'Airflow', 'dbt', 'PostgreSQL', 'AWS'],
        'requisitos': [
            '3 años construyendo pipelines de datos en producción.',
            'SQL avanzado: ventanas, CTE y optimización de consultas.',
            'Experiencia con orquestadores (Airflow, Dagster o similar).',
            'Inglés técnico para leer documentación.',
        ],
        'publicada_hace': 1,
    },
    {
        'titulo': 'Desarrollador(a) Frontend React',
        'empresa': 'Nodo Logística', 'departamento': 'Producto digital',
        'descripcion': 'Construye la consola con la que los transportistas siguen '
                       'sus entregas en tiempo real.',
        'modalidad': HIBRIDO, 'contratacion': 'Por proyecto',
        'duracion': (8, 12), 'correo': 'reclutamiento',
        'etiquetas': ['React', 'TypeScript', 'Vite'],
        'requisitos': [
            'Dos proyectos en producción con React.',
            'Pruebas con Testing Library o similar.',
            'Criterio de accesibilidad (WCAG AA).',
        ],
        'publicada_hace': 2,
    },
    {
        'titulo': 'Analista de Riesgos Operativos',
        'empresa': 'Aseguradora Brisa', 'departamento': 'Riesgos',
        'descripcion': 'Identifica, mide y da seguimiento a los riesgos operativos '
                       'de las áreas de siniestros y emisión.',
        'modalidad': PRESENCIAL, 'contratacion': 'Nómina, plaza indefinida',
        'duracion': (None, None), 'correo': 'rh',
        'etiquetas': ['Riesgos', 'Excel', 'Power BI'],
        'requisitos': ['Licenciatura en Actuaría, Economía o afín.'],
        'publicada_hace': 3,
    },
    {
        'titulo': 'Consultor(a) SAP FI/CO',
        'empresa': 'Grupo Cobalto', 'departamento': 'Consultoría SAP',
        'descripcion': 'Acompaña el cierre contable y la configuración de centros '
                       'de costo en una implementación S/4HANA.',
        'modalidad': REMOTO, 'contratacion': 'Freelance',
        'duracion': (6, None), 'correo': 'talento',
        'etiquetas': ['SAP FI', 'SAP CO', 'S/4HANA'],
        'requisitos': [
            'Al menos un ciclo completo de implementación FI/CO.',
            'Disponibilidad para juntas en horario de CDMX.',
        ],
        'publicada_hace': 4,
    },
    {
        'titulo': 'Ingeniero(a) QA Automatizado',
        'empresa': 'Estudio Ámbar', 'departamento': 'Calidad',
        'descripcion': 'Automatiza las pruebas de regresión de una app móvil de '
                       'pagos antes de su lanzamiento.',
        'modalidad': REMOTO, 'contratacion': 'Por proyecto',
        'duracion': (None, 10), 'correo': 'equipo',
        'etiquetas': ['Playwright', 'Cypress', 'CI/CD'],
        'requisitos': ['Experiencia automatizando pruebas end-to-end.'],
        'publicada_hace': 5,
    },
    {
        # Sin correo, sin requisitos y con una sola etiqueta: la tarjeta no
        # ofrece "Ver detalle" y explica que no hay correo de contacto.
        'titulo': 'Soporte Técnico Nivel 1',
        'empresa': 'Clínica Faro', 'departamento': 'Sistemas',
        'descripcion': 'Atiende los reportes del personal de la clínica sobre '
                       'equipos e impresoras.',
        'modalidad': PRESENCIAL, 'jornada': JornadaVacante.MEDIO_TIEMPO,
        'contratacion': 'Medio tiempo',
        'duracion': (None, None), 'correo': None,
        'etiquetas': ['Soporte'], 'requisitos': [],
        'publicada_hace': 6,
    },
    {
        'titulo': 'Arquitecto(a) de Soluciones en la Nube para Plataformas de '
                  'Seguros Multicanal y Pagos Digitales',
        'empresa': 'Aseguradora Brisa', 'departamento': 'Arquitectura empresarial',
        'descripcion': 'Define la arquitectura objetivo para migrar la emisión de '
                       'pólizas y los cobros recurrentes a la nube, con alta '
                       'disponibilidad y cumplimiento regulatorio.',
        'modalidad': HIBRIDO, 'contratacion': 'Prestación de servicios',
        'duracion': (16, 20), 'correo': 'arquitectura',
        'etiquetas': [
            'Azure', 'AWS', 'Kubernetes', 'Terraform', 'Event-driven',
            'Microservicios', 'PCI DSS', 'Observabilidad',
        ],
        'requisitos': [
            'Cinco años diseñando arquitecturas en la nube.',
            'Certificación de arquitecto en Azure o AWS.',
            'Experiencia con cumplimiento PCI DSS.',
            'Haber liderado una migración de un sistema crítico.',
            'Comunicación clara con áreas de negocio.',
        ],
        'publicada_hace': 8,
    },
    {
        'titulo': 'Diseñador(a) UX/UI',
        'empresa': 'Estudio Ámbar', 'departamento': 'Diseño',
        'descripcion': 'Rediseña el flujo de alta de clientes de una aseguradora '
                       'digital, de la investigación al prototipo.',
        'modalidad': REMOTO, 'contratacion': 'Por proyecto',
        'duracion': (4, 6), 'correo': 'equipo',
        'etiquetas': ['Figma', 'Investigación', 'Prototipado'],
        'requisitos': ['Portafolio con casos de producto digital.'],
        'publicada_hace': 10, 'cierra_en': 3,
    },
    {
        'titulo': 'Project Manager de Implementaciones',
        'empresa': 'Nodo Logística', 'departamento': 'PMO',
        'descripcion': 'Coordina la puesta en marcha del sistema de almacenes en '
                       'cuatro centros de distribución.',
        'modalidad': HIBRIDO, 'contratacion': 'Tiempo completo',
        'duracion': (24, 52), 'correo': 'reclutamiento',
        'etiquetas': ['Scrum', 'Jira', 'WMS'],
        'requisitos': [
            'Dos implementaciones de sistemas logísticos o ERP.',
            'Certificación PMP o Scrum Master deseable.',
        ],
        'publicada_hace': 12,
    },
    {
        'titulo': 'Becario(a) de Desarrollo Backend',
        'empresa': 'Clínica Faro', 'departamento': 'Sistemas',
        'descripcion': 'Apoya en el desarrollo de la API de citas médicas con '
                       'Django y PostgreSQL.',
        'modalidad': PRESENCIAL, 'jornada': JornadaVacante.ESTACIONAL,
        'contratacion': 'Beca',
        'duracion': (12, 12), 'correo': 'sistemas',
        'etiquetas': ['Python', 'Django'],
        'requisitos': ['Estudiante de los últimos semestres de ingeniería.'],
        'publicada_hace': 14,
    },
    # --- Fuera del listado público: sólo las ve el panel de administración.
    {
        'titulo': 'Ingeniero(a) DevOps',
        'empresa': 'Grupo Cobalto', 'departamento': 'Plataforma',
        'descripcion': 'Borrador: falta definir el rango salarial.',
        'modalidad': REMOTO, 'contratacion': 'Tiempo completo',
        'duracion': (None, None), 'correo': 'talento',
        'etiquetas': ['Docker', 'GitHub Actions'], 'requisitos': [],
        'estado': EstadoVacante.BORRADOR,
    },
    {
        'titulo': 'Analista de Inteligencia de Negocios',
        'empresa': 'Aseguradora Brisa', 'departamento': 'Analítica',
        'descripcion': 'En pausa mientras se aprueba el presupuesto del área.',
        'modalidad': HIBRIDO, 'contratacion': 'Nómina',
        'duracion': (None, None), 'correo': 'rh',
        'etiquetas': ['Power BI', 'SQL'], 'requisitos': [],
        'estado': EstadoVacante.PAUSADA, 'publicada_hace': 20,
    },
    {
        'titulo': 'Técnico(a) de Campo',
        'empresa': 'Nodo Logística', 'departamento': 'Operaciones',
        'descripcion': 'Convocatoria cerrada: ya se cubrió la plaza.',
        'modalidad': PRESENCIAL, 'contratacion': 'Por proyecto',
        'duracion': (8, 8), 'correo': 'reclutamiento',
        'etiquetas': ['Redes', 'Cableado'], 'requisitos': [],
        'estado': EstadoVacante.CERRADA, 'publicada_hace': 40,
    },
    {
        # Publicada pero con la fecha de cierre vencida: el listado público ya
        # no la ofrece aunque su estado diga "publicada".
        'titulo': 'Consultor(a) de Ciberseguridad',
        'empresa': 'Grupo Cobalto', 'departamento': 'Seguridad',
        'descripcion': 'Evaluación de controles ISO 27001 en tres subsidiarias.',
        'modalidad': REMOTO, 'contratacion': 'Freelance',
        'duracion': (6, 10), 'correo': 'talento',
        'etiquetas': ['ISO 27001', 'Pentesting'], 'requisitos': [],
        'publicada_hace': 30, 'cierra_en': -2,
    },
]


def _filtro_de_ejemplo():
    """Las vacantes de esta lista, por empresa + título."""
    filtro = Q(pk__in=[])
    for datos in VACANTES:
        filtro |= Q(titulo=datos['titulo'], empresa=datos['empresa'])
    return filtro


def vacantes_de_ejemplo():
    return Vacante.objects.filter(_filtro_de_ejemplo())


def limpiar():
    """Borra las de ejemplo sin postulaciones. Devuelve (borradas, conservadas)."""
    ejemplo = vacantes_de_ejemplo()
    conservadas = list(
        ejemplo.filter(postulaciones__isnull=False)
        .distinct()
        .values_list('titulo', flat=True)
    )
    _, por_modelo = ejemplo.filter(postulaciones__isnull=True).delete()
    return por_modelo.get('core.Vacante', 0), conservadas


def _nueva_vacante(datos, ahora):
    estado = datos.get('estado', PUBLICADA)
    hace = datos.get('publicada_hace')
    publicada_en = (
        ahora - timedelta(days=hace)
        if hace is not None and estado != EstadoVacante.BORRADOR
        else None
    )
    cierra = datos.get('cierra_en')
    duracion_min, duracion_max = datos['duracion']
    correo = datos['correo']

    return Vacante(
        titulo=datos['titulo'],
        empresa=datos['empresa'],
        departamento=datos['departamento'],
        descripcion=datos['descripcion'],
        modalidad=datos['modalidad'],
        jornada=datos.get('jornada', JornadaVacante.TIEMPO_COMPLETO),
        estado=estado,
        publicada_en=publicada_en,
        cierra_en=ahora + timedelta(days=cierra) if cierra is not None else None,
        contratacion=datos['contratacion'],
        duracion_min_semanas=duracion_min,
        duracion_max_semanas=duracion_max,
        email_contacto=f'{correo}{DOMINIO}' if correo else None,
        etiquetas=datos['etiquetas'],
        requisitos=datos['requisitos'],
        creado_en=publicada_en or ahora,
        actualizado_en=ahora,
    )


def sembrar():
    """Crea las que falten; las que ya existen se dejan como están."""
    ahora = timezone.now()
    existentes = set(vacantes_de_ejemplo().values_list('titulo', 'empresa'))
    nuevas = [
        _nueva_vacante(datos, ahora)
        for datos in VACANTES
        if (datos['titulo'], datos['empresa']) not in existentes
    ]
    # Una por una y no con bulk_create: en Django 6 ése arma un INSERT con
    # UNNEST de varchar que Postgres no convierte a los enums de la tabla
    # (`modalidad_vacante`, `estado_vacante`). Son unas cuantas filas.
    for vacante in nuevas:
        vacante.save(force_insert=True)
    return len(nuevas)


class Command(BaseCommand):
    help = 'Crea (o borra) vacantes de ejemplo para la bolsa de trabajo. Sólo en desarrollo.'

    def add_arguments(self, parser):
        accion = parser.add_mutually_exclusive_group()
        accion.add_argument('--reiniciar', action='store_true',
                            help='Borra las vacantes de ejemplo y las vuelve a crear.')
        accion.add_argument('--limpiar', action='store_true',
                            help='Sólo borra las vacantes de ejemplo.')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'sembrar_vacantes sólo corre con DEBUG activo: la bolsa de trabajo es pública.')

        with transaction.atomic():
            conservadas = []
            if options['limpiar'] or options['reiniciar']:
                borradas, conservadas = limpiar()
                self.stdout.write(f'Vacantes de ejemplo borradas: {borradas}.')
            creadas = 0 if options['limpiar'] else sembrar()

        if conservadas:
            self.stdout.write(self.style.WARNING(
                'Se conservaron porque tienen postulaciones: ' + ', '.join(conservadas)))
        if not options['limpiar']:
            self.stdout.write(self.style.SUCCESS(
                f'Vacantes de ejemplo creadas: {creadas} '
                f'(de {len(VACANTES)}; las que ya existían se dejaron).'))
