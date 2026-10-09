"""Las respuestas de tokens no exponen detalles de excepciones internas."""
from inspect import getclosurevars, unwrap
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from core.api.verificacion_views import confirmar_verificacion
from core.api.views import restablecer_password
from core.services.tokens import TokenInvalido


class TokenErrorResponseTests(SimpleTestCase):
    def test_los_dos_endpoints_ocultan_el_detalle_de_la_excepcion(self):
        request = SimpleNamespace(data={
            'token': 'token-de-prueba',
            'password_nueva': 'Amis2026Nueva!',
        })
        for view in (confirmar_verificacion, restablecer_password):
            handler = unwrap(getclosurevars(view.cls.post).nonlocals['func'])
            with self.subTest(endpoint=handler.__name__):
                with patch('core.services.tokens.canjear', side_effect=TokenInvalido(
                    'Detalle interno confidencial: ruta y token'
                )):
                    response = handler(request)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {
                    'token': ['El enlace no es valido, ya se uso o caduco.'],
                })
