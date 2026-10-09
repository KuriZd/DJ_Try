"""Imágenes reales pequeñas para verificar decodificación, sin acceso a S3."""
import io

from PIL import Image


def imagen(formato):
    salida = io.BytesIO()
    Image.new('RGB', (2, 2), (40, 100, 180)).save(salida, format=formato)
    return salida.getvalue()


JPEG = imagen('JPEG')
PNG = imagen('PNG')
WEBP = imagen('WEBP')
