from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from core.models import TipoAdjunto
from core.services import medios_publicacion as medios
from tests.media_fixtures import JPEG, PNG, WEBP


class ImageValidationTests(TestCase):
    def test_real_images_decode(self):
        for tipo, datos in [('image/jpeg', JPEG), ('image/png', PNG), ('image/webp', WEBP)]:
            with self.subTest(tipo=tipo):
                self.assertTrue(medios.imagen_valida(datos, tipo))

    def test_truncated_wrong_format_and_excessive_pixels_rejected(self):
        self.assertFalse(medios.imagen_valida(JPEG[:16], 'image/jpeg'))
        self.assertFalse(medios.imagen_valida(PNG[:16], 'image/png'))
        self.assertFalse(medios.imagen_valida(WEBP[:16], 'image/webp'))
        self.assertFalse(medios.imagen_valida(JPEG, 'image/png'))
        with patch.object(medios.settings, 'IMAGEN_MAX_PIXELES', 3):
            self.assertFalse(medios.imagen_valida(PNG, 'image/png'))

    def test_video_keeps_bounded_signature_validation(self):
        adjunto = SimpleNamespace(content_type='video/mp4', tipo=TipoAdjunto.VIDEO)
        self.assertTrue(medios.es_valido(adjunto, 100, 'video/mp4', b'\x00\x00\x00\x18ftypmp42'))
