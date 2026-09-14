"""Recuperacion de contrasena (fase 4).

Dos endpoints publicos. Lo que se prueba aqui, por orden de importancia:

1. **No se filtra que cuentas existen.** La respuesta es identica exista o no.
2. **El token es lo unico que autoriza el cambio**, y se gasta al usarse.
3. **Cambiar la contrasena tira todas las sesiones**, incluida la de quien
   pudiera haber entrado sin permiso.
"""

import uuid

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    PropositoToken,
    Sesion,
    TokenRecuperacion,
    Usuario,
)
from core.services import tokens


class SinThrottleMixin:
    """Limpia el contador de frecuencia antes de cada prueba.

    DRF lo guarda en la cache del proceso, que sobrevive de una prueba a la
    siguiente: sin esto, a partir de la sexta llamada el endpoint responde 429
    y fallan casos que no tienen nada que ver con el limite.
    """

    def setUp(self):
        cache.clear()
        super().setUp()


def url_recuperar():
    return reverse("api:recuperar-password")


def url_restablecer():
    return reverse("api:restablecer-password")


def enlace_del_correo():
    """El token que viaja en el ultimo correo enviado."""
    cuerpo = mail.outbox[-1].body
    for palabra in cuerpo.split():
        if "token=" in palabra:
            return palabra.split("token=", 1)[1].strip()
    raise AssertionError("El correo no traia enlace con token.")


class PedirEnlaceTest(SinThrottleMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.cliente = APIClient()

    def test_manda_el_correo_a_una_cuenta_existente(self):
        respuesta = self.cliente.post(
            url_recuperar(), {"email": self.usuario.email}, format="json"
        )

        self.assertEqual(respuesta.status_code, 204)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.usuario.email])
        self.assertIn("Recupera el acceso", mail.outbox[0].subject)

    def test_responde_igual_con_una_cuenta_que_no_existe(self):
        # Cualquier diferencia convertiria esto en un comprobador de que
        # direcciones estan registradas.
        respuesta = self.cliente.post(
            url_recuperar(), {"email": "nadie@ejemplo.test"}, format="json"
        )

        self.assertEqual(respuesta.status_code, 204)
        self.assertEqual(respuesta.content, b"")
        self.assertEqual(len(mail.outbox), 0)

    def test_no_distingue_por_mayusculas_del_correo(self):
        self.cliente.post(
            url_recuperar(), {"email": "ASPIRANTE@AMIS.ORG"}, format="json"
        )

        self.assertEqual(len(mail.outbox), 1)

    def test_rechaza_algo_que_no_es_un_correo(self):
        respuesta = self.cliente.post(
            url_recuperar(), {"email": "no-es-un-correo"}, format="json"
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_una_cuenta_eliminada_no_recibe_nada(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(
            eliminado_en=timezone.now()
        )

        respuesta = self.cliente.post(
            url_recuperar(), {"email": self.usuario.email}, format="json"
        )

        self.assertEqual(respuesta.status_code, 204)
        self.assertEqual(len(mail.outbox), 0)

    def test_pedirlo_otra_vez_invalida_el_enlace_anterior(self):
        self.cliente.post(
            url_recuperar(), {"email": self.usuario.email}, format="json"
        )
        viejo = enlace_del_correo()

        self.cliente.post(
            url_recuperar(), {"email": self.usuario.email}, format="json"
        )
        nuevo = enlace_del_correo()

        self.assertNotEqual(viejo, nuevo)
        with self.assertRaises(tokens.TokenInvalido):
            tokens.canjear(viejo, PropositoToken.RECUPERACION)

    def test_un_correo_que_no_sale_no_rompe_la_peticion(self):
        from unittest.mock import patch

        from core.services import correo

        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("caido")
        ):
            respuesta = self.cliente.post(
                url_recuperar(), {"email": self.usuario.email}, format="json"
            )

        self.assertEqual(respuesta.status_code, 204)


class RestablecerTest(SinThrottleMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.cliente = APIClient()
        self.cliente.post(
            url_recuperar(), {"email": self.usuario.email}, format="json"
        )
        self.token = enlace_del_correo()

    def test_cambia_la_contrasena(self):
        anterior = self.usuario.password_hash

        respuesta = self.cliente.post(
            url_restablecer(),
            {"token": self.token, "password_nueva": "Amis2026Nueva!"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 204)
        self.usuario.refresh_from_db()
        self.assertNotEqual(self.usuario.password_hash, anterior)

    def test_la_contrasena_nueva_sirve_para_entrar(self):
        self.cliente.post(
            url_restablecer(),
            {"token": self.token, "password_nueva": "Amis2026Nueva!"},
            format="json",
        )

        acceso = APIClient().post(
            reverse("api:login"),
            {"email": self.usuario.email, "password": "Amis2026Nueva!"},
            format="json",
        )

        self.assertEqual(acceso.status_code, 200)

    def test_el_token_se_gasta_al_usarse(self):
        datos = {"token": self.token, "password_nueva": "Amis2026Nueva!"}
        self.cliente.post(url_restablecer(), datos, format="json")

        segundo = self.cliente.post(url_restablecer(), datos, format="json")

        self.assertEqual(segundo.status_code, 400)

    def test_un_token_caducado_no_sirve(self):
        TokenRecuperacion.objects.filter(usuario=self.usuario).update(
            expira_en=timezone.now() - timezone.timedelta(minutes=1)
        )

        respuesta = self.cliente.post(
            url_restablecer(),
            {"token": self.token, "password_nueva": "Amis2026Nueva!"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_un_token_inventado_no_sirve(self):
        respuesta = self.cliente.post(
            url_restablecer(),
            {"token": "inventado", "password_nueva": "Amis2026Nueva!"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_un_token_de_verificacion_no_sirve_para_esto(self):
        # Un enlace de "confirma tu correo" no puede convertirse en uno de
        # cambio de contrasena.
        otro = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)

        respuesta = self.cliente.post(
            url_restablecer(),
            {"token": otro, "password_nueva": "Amis2026Nueva!"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_rechaza_una_contrasena_debil(self):
        respuesta = self.cliente.post(
            url_restablecer(),
            {"token": self.token, "password_nueva": "1234"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("password_nueva", respuesta.data)

    def test_una_contrasena_rechazada_no_gasta_el_token(self):
        # Equivocarse con la contrasena no puede obligar a pedir otro enlace.
        self.cliente.post(
            url_restablecer(),
            {"token": self.token, "password_nueva": "1234"},
            format="json",
        )

        segundo = self.cliente.post(
            url_restablecer(),
            {"token": self.token, "password_nueva": "Amis2026Nueva!"},
            format="json",
        )

        self.assertEqual(segundo.status_code, 204)


class SesionesTrasRestablecerTest(SinThrottleMixin, TestCase):
    """Recuperar el acceso tiene que expulsar a quien ya estuviera dentro."""

    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.cliente = APIClient()

    def _sesion_abierta(self):
        ahora = timezone.now()
        return Sesion.objects.create(
            id=uuid.uuid4(),
            usuario=self.usuario,
            refresh_token_hash=uuid.uuid4().hex,
            expira_en=ahora + timezone.timedelta(days=7),
            creado_en=ahora,
        )

    def test_se_revocan_todas_las_sesiones_abiertas(self):
        # A diferencia de `cambiar_password`, aqui no se conserva ninguna: el
        # motivo tipico de recuperar es haber perdido el control de la cuenta.
        intrusa = self._sesion_abierta()
        propia = self._sesion_abierta()

        self.cliente.post(
            url_recuperar(), {"email": self.usuario.email}, format="json"
        )
        self.cliente.post(
            url_restablecer(),
            {"token": enlace_del_correo(), "password_nueva": "Amis2026Nueva!"},
            format="json",
        )

        intrusa.refresh_from_db()
        propia.refresh_from_db()
        self.assertIsNotNone(intrusa.revocada_en)
        self.assertIsNotNone(propia.revocada_en)


@override_settings(EMAIL_REDIRIGIR_A="pruebas@ejemplo.test")
class RecuperacionEnLaJaulaTest(SinThrottleMixin, TestCase):
    def test_el_enlace_no_alcanza_al_destinatario_real(self):
        usuario = Usuario.objects.get(email__iexact="admin@amis.org")

        APIClient().post(
            url_recuperar(), {"email": usuario.email}, format="json"
        )

        self.assertEqual(mail.outbox[0].to, ["pruebas@ejemplo.test"])
