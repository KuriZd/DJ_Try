"""Verificacion del correo: pedir el enlace y canjearlo."""

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import PropositoToken, TokenRecuperacion
from core.services import tokens
from tests.test_publicaciones import crear_usuario
from tests.test_recuperar_password import enlace_del_correo


class VerificacionCorreoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.usuario = crear_usuario('aspirante', verificado=False)
        self.url_solicitar = reverse('api:solicitar-verificacion')
        self.url_confirmar = reverse('api:confirmar-verificacion')

    def solicitar(self):
        self.client.force_authenticate(self.usuario)
        return self.client.post(self.url_solicitar)

    def confirmar(self, token):
        self.client.force_authenticate(None)
        return self.client.post(self.url_confirmar, {'token': token}, format='json')

    def test_pedir_el_enlace_pide_sesion(self):
        self.assertEqual(self.client.post(self.url_solicitar).status_code, 401)

    def test_manda_un_enlace_al_frontend(self):
        self.assertEqual(self.solicitar().status_code, 204)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Confirma tu correo', mail.outbox[0].subject)
        self.assertIn('/verificar-correo?token=', mail.outbox[0].body)

    def test_el_enlace_verifica_sin_sesion_y_se_gasta(self):
        self.solicitar()
        token = enlace_del_correo()

        self.assertEqual(self.confirmar(token).status_code, 204)
        self.usuario.refresh_from_db()
        self.assertIsNotNone(self.usuario.email_verificado_en)

        segundo = self.confirmar(token)
        self.assertEqual(segundo.status_code, 400)
        self.assertIn('token', segundo.data)

    def test_un_token_inventado_o_de_recuperacion_no_verifica(self):
        recuperacion = tokens.emitir(self.usuario, PropositoToken.RECUPERACION)
        self.assertEqual(self.confirmar('inventado').status_code, 400)
        self.assertEqual(self.confirmar(recuperacion).status_code, 400)
        self.usuario.refresh_from_db()
        self.assertIsNone(self.usuario.email_verificado_en)

    def test_reenviar_apaga_el_enlace_anterior(self):
        self.solicitar()
        viejo = enlace_del_correo()
        self.solicitar()
        nuevo = enlace_del_correo()

        self.assertEqual(self.confirmar(viejo).status_code, 400)
        self.assertEqual(self.confirmar(nuevo).status_code, 204)

    def test_con_el_correo_ya_verificado_no_manda_nada(self):
        verificado = crear_usuario('aspirante')
        self.client.force_authenticate(verificado)
        self.assertEqual(self.client.post(self.url_solicitar).status_code, 204)
        self.assertEqual(len(mail.outbox), 0)

    def test_cambiar_el_correo_invalida_el_enlace_mandado_al_anterior(self):
        self.solicitar()
        token = enlace_del_correo()

        self.client.force_authenticate(self.usuario)
        respuesta = self.client.patch(
            reverse('api:usuario-actual'), {'email': 'nuevo@example.test'}, format='json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.data)

        self.assertEqual(self.confirmar(token).status_code, 400)
        self.assertFalse(TokenRecuperacion.objects.filter(
            usuario=self.usuario, proposito=PropositoToken.VERIFICACION, usado_en__isnull=True,
        ).exists())

    def test_limite_de_envios_por_cuenta(self):
        codigos = [self.solicitar().status_code for _ in range(4)]
        self.assertEqual(codigos, [204, 204, 204, 429])
