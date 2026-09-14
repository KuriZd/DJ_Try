"""Datos de ejemplo locales. Invocar crear(email) desde manage.py shell."""
from django.db import transaction
from django.utils import timezone

from core.models import Aspirante, Certificado, PlantillaCertificado, Postulacion, Usuario, Vacante
from core.services.certificados import VIGENTES, emitir


@transaction.atomic
def crear(email):
    usuario = Usuario.objects.get(email__iexact=email, estado='activo')
    aspirante = Aspirante.objects.select_for_update().get(usuario=usuario, eliminado_en__isnull=True)
    emisor = Usuario.objects.filter(
        usuarios_roles__rol__clave='administrador', estado='activo',
    ).order_by('creado_en').first()
    if emisor is None:
        raise RuntimeError('No existe un administrador activo para registrar la carga de ejemplos.')
    ahora = timezone.now()
    titulo = 'DEMO certificados - ' + aspirante.id
    vacante = Vacante.objects.filter(titulo=titulo, estado='borrador').first()
    if vacante is None:
        vacante = Vacante.objects.create(
            titulo=titulo, descripcion='Vacante de prueba para visualizar certificados. Sin proceso real de contratación.',
            modalidad='remoto', estado='borrador', creado_por=emisor,
            creado_en=ahora, actualizado_en=ahora,
        )
    postulacion, _ = Postulacion.objects.get_or_create(
        aspirante=aspirante, vacante=vacante,
        defaults={'estado': 'nuevo', 'etapa': 'Datos de prueba para certificados',
                  'registrada_en': ahora, 'ultima_actividad_en': ahora},
    )
    resultados = []
    for tipo in ('participacion', 'culminacion', 'evaluacion', 'expediente'):
        plantilla, _ = PlantillaCertificado.objects.get_or_create(
            id='TPL-DEMO-' + tipo.upper(), version='1.0',
            defaults={
                'tipo_id': tipo, 'nombre': 'DEMO - ' + tipo,
                'texto_institucional': (
                    'DOCUMENTO DE PRUEBA SIN VALIDEZ. Ejemplo para comprobar la integración '
                    'del sistema. No acredita participación, culminación, aprobación de '
                    'evaluaciones ni validación de expediente reales.'
                ),
                'activa': True, 'configuracion': {}, 'creado_por': emisor, 'creado_en': ahora,
            },
        )
        certificado = Certificado.objects.filter(
            postulacion=postulacion, tipo_id=tipo, estado__in=VIGENTES,
        ).first()
        creado = certificado is None
        if creado:
            certificado = emitir({
                'postulacion': postulacion.pk, 'tipo': tipo,
                'plantilla_id': plantilla.id, 'plantilla_version': plantilla.version,
                'resultado': 'DEMO SIN VALIDEZ - Ejemplo de certificado de ' + tipo + '.',
                'justificacion_manual': 'Carga de ejemplos solicitada para la cuenta ' + usuario.email + '.',
                'autoridad_emisora': 'Entorno de pruebas - Sin autoridad certificadora',
                'cargo_autoridad': 'Demostración del sistema',
                'observaciones_internas': 'Datos de ejemplo para integración del frontend. No se envía correo.',
            }, emisor)
        resultados.append({'id': certificado.id, 'tipo': tipo, 'folio': certificado.folio, 'creado': creado})
    return {'email': usuario.email, 'postulacion': postulacion.pk, 'certificados': resultados}
