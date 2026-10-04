"""Caratula de un curso en S3, bajo `cursos/{curso_id}/`.

Mismo mecanismo que las fotos del muro (`medios_publicacion.py`) y con sus
mismas piezas: el backend firma, el navegador sube directo a S3 y el bucket
sigue privado. El catalogo es publico, asi que cada lectura firma una URL
temporal; nadie necesita sesion para ver la caratula, pero el archivo nunca
queda expuesto de forma permanente.

A diferencia de un adjunto, la caratula no tiene fila propia: se pide la
subida, se sube, y al confirmar se fija en el curso. La clave que confirma el
navegador se valida contra un patron que solo admite la carpeta de ese curso,
de modo que nadie puede colgarle a su curso un archivo ajeno.
"""

import logging
import re
import uuid

from django.conf import settings

from core.services import medios_publicacion

logger = logging.getLogger(__name__)

# content_type -> (extension, comprobacion de la firma). Solo fotos: el
# formato que no es imagen no pasa ni a pedir la subida.
FORMATOS = {
    content_type: (extension, firma)
    for content_type, (tipo, extension, firma) in medios_publicacion.FORMATOS.items()
    if content_type.startswith('image/')
}
POR_EXTENSION = {extension: content_type for content_type, (extension, _) in FORMATOS.items()}

ERRORES_S3 = medios_publicacion.ERRORES_S3


def clave_nueva(curso, content_type):
    extension = FORMATOS[content_type][0]
    return f'cursos/{curso.pk}/portada-{uuid.uuid4()}.{extension}'


def tipo_de_clave(curso, clave):
    """El content_type que corresponde a la clave, o None si la clave no es
    una caratula de este curso."""
    extensiones = '|'.join(re.escape(extension) for extension in POR_EXTENSION)
    patron = rf'cursos/{re.escape(str(curso.pk))}/portada-[0-9a-f-]{{36}}\.({extensiones})'
    coincidencia = re.fullmatch(patron, clave or '')
    return POR_EXTENSION[coincidencia.group(1)] if coincidencia else None


def url_de_subida(clave, content_type):
    return medios_publicacion.url_de_subida(clave, content_type)


def inspeccionar(clave):
    return medios_publicacion.inspeccionar(clave)


def es_valida(content_type, tamano, content_type_guardado, inicio):
    _, firma_ok = FORMATOS[content_type]
    return (
        content_type_guardado == content_type
        and 0 < tamano <= settings.CURSO_PORTADA_MAX_BYTES
        and firma_ok(inicio)
    )


def url_de_lectura(curso):
    """La caratula firmada; si no hay, la URL heredada; si S3 falla, None.

    Que S3 no responda no puede tumbar el catalogo: el curso se sirve sin
    imagen y el frontend dibuja su portada tipografica.
    """
    if not curso.imagen_clave:
        return curso.imagen or None
    try:
        return medios_publicacion.url_de_lectura(curso.imagen_clave, curso.imagen_tipo)
    except ERRORES_S3:
        logger.warning('No se pudo firmar la caratula del curso %s.', curso.pk, exc_info=True)
        return None


def borrar(claves):
    medios_publicacion.borrar([clave for clave in claves if clave])
