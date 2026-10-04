"""Enlace para compartir una publicacion del muro, con vista previa.

El frontend es una SPA: LinkedIn, WhatsApp y compania leen el HTML sin
ejecutar JavaScript, asi que `/actualiza/publicacion/{id}` les llega vacio y
el enlace sale sin titulo ni texto. Esta pagina la sirve el backend con las
etiquetas Open Graph ya escritas; quien la abre en un navegador sigue de
inmediato a la publicacion en el frontend.

Lo de una cuenta bloqueada o dada de baja no se previsualiza, igual que en el
muro: 404.
"""

import re

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from core.api.publicacion_views import cuenta_visible
from core.models import EstadoAdjunto, Publicacion, TipoAdjunto
from core.services import medios_publicacion

# Lo que muestran las tarjetas de vista previa antes de cortar.
LIMITE_DESCRIPCION = 200

# La imagen de la vista previa la guardan los rastreadores; se firma por el
# maximo que admite S3 (7 dias) para que no caduque antes de que la lean.
VIGENCIA_IMAGEN = 7 * 24 * 60 * 60


def imagen_de(publicacion):
    foto = publicacion.adjuntos.filter(
        estado=EstadoAdjunto.LISTO, tipo=TipoAdjunto.IMAGEN,
    ).order_by('orden').first()
    if foto is None:
        return None
    try:
        return medios_publicacion.url_de_lectura(foto.s3_key, foto.content_type, VIGENCIA_IMAGEN)
    except medios_publicacion.ERRORES_S3:
        return None


def resumen(texto, limite=LIMITE_DESCRIPCION):
    plano = re.sub(r'\s+', ' ', texto).strip()
    if len(plano) <= limite:
        return plano
    # Hasta limite inclusive: si justo ahi hay un espacio, la palabra cabe entera.
    corte = plano.rfind(' ', 0, limite + 1)
    return plano[: corte if corte > 0 else limite].rstrip() + '…'


@require_GET
def compartir_publicacion(request, pk):
    publicacion = get_object_or_404(
        Publicacion.objects.select_related('autor').filter(**cuenta_visible('autor')), pk=pk,
    )
    destino = f'{settings.FRONTEND_BASE_URL}/actualiza/publicacion/{publicacion.pk}'
    respuesta = render(request, 'compartir/publicacion.html', {
        'titulo': f'{publicacion.autor.nombre_completo} en Actualiza · AMIS',
        'descripcion': resumen(publicacion.cuerpo) or 'Publicación con fotos o video en Actualiza.',
        'imagen': imagen_de(publicacion),
        'destino': destino,
        'publicada_en': publicacion.fecha_publicacion,
    })
    # Los rastreadores vuelven poco; cinco minutos bastan para que una edicion
    # se vea pronto en la siguiente vista previa.
    respuesta['Cache-Control'] = 'public, max-age=300'
    return respuesta
