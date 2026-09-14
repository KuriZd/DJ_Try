from decimal import Decimal, ROUND_DOWN
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from core.models import CertificadoCurso, Inscripcion, ProgresoLeccion


def generar_pdf(certificado):
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), topMargin=60, bottomMargin=40)
    title = ParagraphStyle('titulo', fontName='Helvetica-Bold', fontSize=26, leading=32,
                           alignment=TA_CENTER, textColor=colors.HexColor('#17365d'))
    body = ParagraphStyle('cuerpo', fontName='Helvetica', fontSize=16, leading=23, alignment=TA_CENTER)
    small = ParagraphStyle('folio', parent=body, fontSize=10, leading=14)
    doc.build([
        Paragraph('Certificado de finalización', title), Spacer(1, 30),
        Paragraph('Se otorga a', body),
        Paragraph(escape(certificado.usuario.nombre_completo), title), Spacer(1, 22),
        Paragraph('Por completar el curso', body),
        Paragraph(escape(certificado.curso.titulo), body), Spacer(1, 22),
        Paragraph('Instructor: ' + escape(certificado.curso.instructor.nombre_completo), body),
        Spacer(1, 25),
        Paragraph('Fecha de emisión: ' + timezone.localdate().isoformat(), small),
        Paragraph('Código: ' + str(certificado.codigo_certificado), small),
    ])
    return output.getvalue()


def recalcular_inscripcion(inscripcion):
    """Llamar dentro de atomic con el curso bloqueado antes de cualquier escritura.

    Serializar por curso evita carreras entre progreso y cambios del temario.
    Un certificado acredita la finalizacion en su fecha y se conserva si luego
    se agregan lecciones o el alumno vuelve a marcar una leccion como pendiente.
    """
    total = inscripcion.curso.lecciones.filter(activo=True).count()
    vistos = ProgresoLeccion.objects.filter(
        usuario_id=inscripcion.usuario_id, leccion__curso_id=inscripcion.curso_id,
        leccion__activo=True, visto=True,
    ).count()
    inscripcion.completado = total > 0 and vistos == total
    inscripcion.porcentaje_avance = (
        (Decimal(vistos) * 100 / Decimal(total)).quantize(Decimal('.01'), rounding=ROUND_DOWN)
        if total else Decimal('0.00')
    )
    inscripcion.save(update_fields=['completado', 'porcentaje_avance'])
    if inscripcion.completado and not CertificadoCurso.objects.filter(
        usuario_id=inscripcion.usuario_id, curso_id=inscripcion.curso_id,
    ).exists():
        certificado = CertificadoCurso(usuario=inscripcion.usuario, curso=inscripcion.curso)
        certificado.archivo_pdf = generar_pdf(certificado)
        certificado.save()
    return inscripcion


def recalcular_curso(curso):
    for inscripcion in Inscripcion.objects.filter(curso=curso).select_related('usuario', 'curso__instructor'):
        recalcular_inscripcion(inscripcion)
