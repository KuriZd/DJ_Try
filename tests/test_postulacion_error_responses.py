"""Los errores de postulaciones no exponen las excepciones del servicio."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from core.api.views import PostulacionViewSet
from core.services.postulaciones import PostulacionNoRetirable, PostulacionRetirada


class PostulacionErrorResponseTests(SimpleTestCase):
    def test_las_acciones_ocultan_el_detalle_interno(self):
        request = SimpleNamespace(data={'estado': 'revision'}, user=SimpleNamespace(pk=1))
        casos = (
            ('partial_update', 'avanzar_postulacion', PostulacionRetirada,
             'No se puede avanzar una postulación retirada.'),
            ('retirar', 'retirar_postulacion', PostulacionNoRetirable,
             'Esta postulación ya no se puede retirar: el proceso terminó.'),
        )
        for accion, servicio, excepcion, mensaje in casos:
            with self.subTest(accion=accion):
                view = PostulacionViewSet()
                view.get_object = Mock(return_value=SimpleNamespace(
                    aspirante=SimpleNamespace(usuario_id=1),
                ))
                with patch('core.api.views.' + servicio, side_effect=excepcion(
                    'Detalle interno confidencial: ruta de servidor'
                )):
                    with self.assertRaises(ValidationError) as capturada:
                        getattr(view, accion)(request)
                self.assertEqual(capturada.exception.status_code, 400)
                self.assertEqual(capturada.exception.detail, {'estado': mensaje})
