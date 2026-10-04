"""Fotos y videos del muro en S3, bajo `publicaciones/`.

Mismo mecanismo que los videos de cursos (`videos.py`): el backend firma, el
navegador sube y descarga directo contra S3, y el bucket sigue privado. Lo que
cambia es que el muro es publico: cada lectura firma una URL temporal, de
modo que nadie necesita sesion para ver una foto, pero ningun archivo queda
expuesto de forma permanente.

Al confirmar una carga no basta con creer el Content-Type: S3 guarda el que
declaro el navegador. Se leen los primeros bytes y se exige la firma del
formato. Asi una pagina HTML renombrada a .png no entra al muro.
"""

import logging

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core.models import TipoAdjunto
from core.services.videos import s3_video_storage

logger = logging.getLogger(__name__)

PREFIJO = 'publicaciones'

# Bytes que se leen del inicio del archivo para reconocer su formato.
BYTES_FIRMA = 16


def _es_jpeg(cabeza):
    return cabeza.startswith(b'\xff\xd8\xff')


def _es_png(cabeza):
    return cabeza.startswith(b'\x89PNG\r\n\x1a\n')


def _es_webp(cabeza):
    return cabeza[:4] == b'RIFF' and cabeza[8:12] == b'WEBP'


def _es_mp4(cabeza):
    # La caja `ftyp` va siempre en el byte 4 de un MP4.
    return cabeza[4:8] == b'ftyp'


# content_type -> (tipo, extension, comprobacion de la firma)
FORMATOS = {
    'image/jpeg': (TipoAdjunto.IMAGEN, 'jpg', _es_jpeg),
    'image/png': (TipoAdjunto.IMAGEN, 'png', _es_png),
    'image/webp': (TipoAdjunto.IMAGEN, 'webp', _es_webp),
    'video/mp4': (TipoAdjunto.VIDEO, 'mp4', _es_mp4),
}

# Lo que puede fallar al hablar con S3 o porque falta configurarlo.
ERRORES_S3 = (BotoCoreError, ClientError, ImproperlyConfigured)


def tipo_de(content_type):
    return FORMATOS[content_type][0]


def tamano_maximo(tipo):
    if tipo == TipoAdjunto.VIDEO:
        return settings.PUBLICACION_VIDEO_MAX_BYTES
    return settings.PUBLICACION_IMAGEN_MAX_BYTES


def clave_para(adjunto):
    extension = FORMATOS[adjunto.content_type][1]
    return f'{PREFIJO}/{adjunto.autor_id}/{adjunto.id}.{extension}'


def url_de_subida(clave, content_type):
    """URL de PUT firmada. Firma el Content-Type: el navegador no puede subir
    otro, y `If-None-Match` impide pisar un archivo ya subido."""
    cliente, bucket = s3_video_storage()
    headers = {'Content-Type': content_type, 'If-None-Match': '*'}
    url = cliente.generate_presigned_url(
        'put_object',
        Params={'Bucket': bucket, 'Key': clave, 'ContentType': content_type, 'IfNoneMatch': '*'},
        ExpiresIn=settings.VIDEO_UPLOAD_URL_TTL,
        HttpMethod='PUT',
    )
    return url, headers


def url_de_lectura(clave, content_type, vigencia=None):
    """URL de GET firmada. Fuerza el Content-Type al leer, para que el
    navegador trate el archivo como lo que se comprobo que es."""
    cliente, bucket = s3_video_storage()
    return cliente.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': bucket, 'Key': clave,
            'ResponseContentType': content_type,
            'ResponseContentDisposition': 'inline',
        },
        ExpiresIn=vigencia or settings.PUBLICACION_MEDIA_URL_TTL,
        HttpMethod='GET',
    )


def inspeccionar(clave):
    """(tamano, content_type guardado, primeros bytes) del archivo en S3."""
    cliente, bucket = s3_video_storage()
    cabecera = cliente.head_object(Bucket=bucket, Key=clave)
    inicio = cliente.get_object(
        Bucket=bucket, Key=clave, Range=f'bytes=0-{BYTES_FIRMA - 1}',
    )['Body'].read()
    return cabecera.get('ContentLength', 0), cabecera.get('ContentType'), inicio


def aun_no_existe(error):
    """Si un ClientError de HeadObject significa "el archivo no está".

    S3 responde 404 solo a quien puede listar el bucket. La cuenta de la app no
    tiene `s3:ListBucket` a propósito, así que a un archivo que no existe le
    responde 403. Como sí tiene `s3:GetObject` sobre `publicaciones/*`, un 403
    ahí significa que la carga todavía no llega.
    """
    return error.response.get('Error', {}).get('Code') in ('403', '404', 'NoSuchKey', 'NotFound')


def es_valido(adjunto, tamano, content_type, inicio):
    _, _, firma_ok = FORMATOS[adjunto.content_type]
    return (
        content_type == adjunto.content_type
        and 0 < tamano <= tamano_maximo(adjunto.tipo)
        and firma_ok(inicio)
    )


def borrar(claves):
    """Borra archivos de S3 sin lanzar: se llama al confirmar un borrado ya
    hecho en la base, y un fallo aqui deja un huerfano, no un error."""
    if not claves:
        return
    try:
        cliente, bucket = s3_video_storage()
    except ImproperlyConfigured:
        logger.warning('S3 sin configurar: quedan %s archivos sin borrar.', len(claves))
        return
    for clave in claves:
        try:
            cliente.delete_object(Bucket=bucket, Key=clave)
        except (BotoCoreError, ClientError):
            logger.warning('No se pudo borrar %s de S3.', clave, exc_info=True)
