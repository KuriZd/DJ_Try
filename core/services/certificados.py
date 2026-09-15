"""Emisión explícita; el expediente y el PDF quedan congelados al emitir."""
import json
import uuid
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.forms.models import model_to_dict
from django.utils import timezone
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from rest_framework.exceptions import APIException, ValidationError

from core.models import (
    Aspirante, Certificado, EnvioCertificado, HistorialCertificado,
    PerfilProfesional, PlantillaCertificado, Postulacion, TipoCertificado,
)
from .correo import _asunto_final, _destinatario_real


VIGENTES = ('en_proceso', 'emitido', 'enviado', 'reenviado')
# Los que ya son un documento: hay PDF, se puede enviar y la verificacion
# publica los da por buenos. 'en_proceso' todavia no lo es.
EMITIDOS = ('emitido', 'enviado', 'reenviado')


class ConflictoCertificado(APIException):
    status_code = 409
    default_detail = 'La operación no es válida para el estado actual del certificado.'


def registrar(certificado, usuario, accion, anterior=None, descripcion=''):
    return HistorialCertificado.objects.create(
        id=uuid.uuid4(), certificado=certificado, accion=accion,
        estado_anterior=anterior, estado_nuevo=certificado.estado,
        realizado_por=usuario, realizado_por_email=usuario.email,
        descripcion=descripcion, metadata={}, creado_en=timezone.now(),
    )


def snapshot_de(aspirante, postulacion, plantilla):
    perfil = PerfilProfesional.objects.filter(aspirante=aspirante).first()
    campos = model_to_dict(perfil, exclude=['aspirante', 'actualizado_en']) if perfil else {}
    grupos = {
        'academicos': ['nivel_educativo', 'institucion', 'carrera_especialidad',
                       'certificaciones', 'cursos_completados', 'validacion_academica'],
        'laborales': ['empresas', 'puestos_anteriores', 'experiencia_meses',
                      'area_profesional', 'validacion_laboral'],
        'competencias': ['habilidades_declaradas', 'habilidades_tecnicas',
                         'habilidades_blandas', 'resultado_evaluaciones', 'puntaje',
                         'compatibilidad_perfil', 'validacion_competencias'],
    }
    datos = {
        'version': 1,
        'generales': {nombre: getattr(aspirante, nombre) for nombre in (
            'id', 'matricula', 'nombre_completo', 'email', 'fecha_nacimiento',
            'telefono', 'cedula_profesional', 'ciudad', 'estado_region',
        )},
        **{grupo: {nombre: campos.get(nombre) for nombre in nombres}
           for grupo, nombres in grupos.items()},
        'postulacion': {'id': postulacion.pk, 'folio': postulacion.folio,
                        'vacante_id': postulacion.vacante_id,
                        'vacante_titulo': postulacion.vacante.titulo},
        'plantilla': {'id': plantilla.id, 'version': plantilla.version,
                     'nombre': plantilla.nombre, 'texto_institucional': plantilla.texto_institucional,
                     'tipo_nombre': plantilla.tipo.nombre},
    }
    return json.loads(json.dumps(datos, cls=DjangoJSONEncoder))


def generar_pdf(certificado):
    salida = BytesIO()
    estilos = getSampleStyleSheet()
    elementos = []

    def texto(valor, estilo='BodyText'):
        elementos.append(Paragraph(escape(str(valor)).replace('\n', '<br/>'), estilos[estilo]))
        elementos.append(Spacer(1, 7))

    snapshot = certificado.aspirante_snapshot
    texto('Certificado: ' + snapshot['plantilla']['tipo_nombre'], 'Title')
    texto(snapshot['generales']['nombre_completo'], 'Heading1')
    texto(snapshot['plantilla']['texto_institucional'])
    for nombre, valor in (
        ('Folio', certificado.folio), ('Código de verificación', certificado.codigo_verificacion),
        ('Fecha de emisión', certificado.emitido_en.isoformat()),
        ('Proceso', certificado.proceso_nombre), ('Resultado', certificado.resultado),
        ('Periodo de participación', certificado.periodo_participacion),
        ('Autoridad emisora', certificado.autoridad_emisora), ('Cargo', certificado.cargo_autoridad),
    ):
        texto(f'{nombre}: {valor or "Sin información"}')
    for grupo in ('postulacion', 'generales', 'academicos', 'laborales', 'competencias'):
        texto(grupo.replace('_', ' ').capitalize(), 'Heading2')
        for nombre, valor in snapshot[grupo].items():
            if valor is None or valor == '' or valor == []:
                valor = 'Sin información'
            elif isinstance(valor, bool):
                valor = 'Sí' if valor else 'No'
            elif isinstance(valor, (dict, list)):
                valor = json.dumps(valor, ensure_ascii=False)
            texto(f'{nombre.replace("_", " ").capitalize()}: {valor}')
    SimpleDocTemplate(salida).build(elementos)
    return salida.getvalue()


@transaction.atomic
def emitir(datos, usuario):
    datos = dict(datos)
    postulacion = Postulacion.objects.select_related('vacante').filter(pk=datos.pop('postulacion')).first()
    if not postulacion:
        raise ValidationError({'postulacion': 'La postulación ya no existe.'})
    # Un bloqueo por aspirante serializa también emisiones de distintas postulaciones.
    aspirante = Aspirante.objects.select_for_update().get(pk=postulacion.aspirante_id)
    if aspirante.eliminado_en:
        raise ValidationError({'postulacion': 'El aspirante está eliminado.'})
    tipo = TipoCertificado.objects.select_for_update().filter(pk=datos.pop('tipo'), activo=True).first()
    if not tipo:
        raise ValidationError({'tipo': 'El tipo no existe o está inactivo.'})
    plantilla = PlantillaCertificado.objects.select_for_update().filter(
        id=datos.pop('plantilla_id'), version=datos.pop('plantilla_version'), tipo=tipo, activa=True,
    ).first()
    if not plantilla:
        raise ValidationError({'plantilla_id': 'La plantilla no existe, está inactiva o no corresponde al tipo.'})
    proceso = postulacion.vacante.convocatoria
    existentes = Certificado.objects.filter(aspirante=aspirante, tipo=tipo, estado__in=VIGENTES)
    if existentes.filter(postulacion=postulacion).exists() or (
        proceso and existentes.filter(proceso=proceso).exists()
    ):
        raise ConflictoCertificado('Ya existe un certificado vigente para esta postulación o convocatoria y tipo.')
    ahora = timezone.now()
    certificado = Certificado(
        id='CERT-' + uuid.uuid4().hex, folio='CERT-' + uuid.uuid4().hex,
        codigo_verificacion=uuid.uuid4().hex + uuid.uuid4().hex,
        aspirante=aspirante, postulacion=postulacion, tipo=tipo,
        proceso=proceso, proceso_nombre=proceso.nombre if proceso else postulacion.vacante.titulo,
        plantilla_id=plantilla.id, plantilla_version=plantilla.version,
        aspirante_snapshot=snapshot_de(aspirante, postulacion, plantilla),
        estado='emitido', tipo_generacion='manual', emitido_en=ahora, emitido_por=usuario,
        creado_en=ahora, actualizado_en=ahora, **datos,
    )
    certificado.archivo_pdf = generar_pdf(certificado)
    certificado.save(force_insert=True)
    registrar(certificado, usuario, 'emitir', descripcion='Emisión manual justificada.')
    return certificado


def cambiar_estado(certificado, usuario, accion, motivo):
    objetivo = {'cancelar': 'cancelado', 'revocar': 'revocado'}[accion]
    if certificado.estado == objetivo:
        return certificado
    if certificado.estado not in VIGENTES or (accion == 'revocar' and certificado.estado == 'en_proceso'):
        raise ConflictoCertificado()
    anterior = certificado.estado
    certificado.estado = objetivo
    prefijo = 'cancelado' if accion == 'cancelar' else 'revocado'
    setattr(certificado, prefijo + '_en', timezone.now())
    setattr(certificado, prefijo + '_por', usuario)
    setattr(certificado, 'motivo_cancelacion' if accion == 'cancelar' else 'motivo_revocacion', motivo)
    certificado.actualizado_en = timezone.now()
    certificado.save()
    registrar(certificado, usuario, accion, anterior, motivo)
    return certificado


def enviar(certificado, usuario):
    if certificado.estado not in ('emitido', 'enviado', 'reenviado') or not certificado.archivo_pdf:
        raise ConflictoCertificado('El certificado no tiene un PDF vigente disponible.')
    destinatario = certificado.aspirante_snapshot.get('generales', {}).get('email')
    if not destinatario:
        raise ConflictoCertificado('El certificado no conserva un correo de destinatario.')
    envio = EnvioCertificado.objects.create(
        id=uuid.uuid4(), certificado=certificado, destinatario_email=destinatario,
        enviado_por=usuario, estado='pendiente', numero_intento=1, creado_en=timezone.now(),
    )
    try:
        mensaje = EmailMessage(
            subject=_asunto_final('Certificado ' + certificado.folio, destinatario),
            body='Adjuntamos su certificado ' + certificado.folio + '.',
            from_email=settings.DEFAULT_FROM_EMAIL, to=[_destinatario_real(destinatario)],
        )
        mensaje.attach(certificado.folio + '.pdf', bytes(certificado.archivo_pdf), 'application/pdf')
        if mensaje.send() != 1:
            raise RuntimeError('El proveedor no aceptó el correo.')
    except Exception:
        envio.estado = 'fallido'
        envio.mensaje_error = 'No se pudo enviar el correo. Consulta la configuración del proveedor.'
        registrar(certificado, usuario, 'envio_fallido', certificado.estado, envio.mensaje_error)
    else:
        envio.estado = 'enviado'
        envio.enviado_en = timezone.now()
        envio.proveedor_id = settings.EMAIL_BACKEND.rsplit('.', 2)[-2]
        anterior = certificado.estado
        certificado.estado = 'reenviado' if certificado.enviado_en else 'enviado'
        certificado.enviado_en = envio.enviado_en
        certificado.actualizado_en = timezone.now()
        certificado.save()
        registrar(certificado, usuario, 'enviar', anterior, 'Correo aceptado por el backend: ' + envio.proveedor_id)
    envio.save()
    return envio
