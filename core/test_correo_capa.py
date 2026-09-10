"""Capa de correo y de tokens (fase 1).

No hay pantalla ni endpoint todavia: lo que se prueba aqui son las dos
garantias sobre las que se apoyan las fases siguientes.

1. **Un fallo de correo no tumba nada.** `enviar` no propaga excepciones.
2. **La jaula de pruebas funciona.** Con `EMAIL_REDIRIGIR_A` puesto, ningun
   mensaje alcanza al destinatario original.
"""

from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    EnvioCorreo,
    EstadoEnvio,
    PropositoToken,
    TokenRecuperacion,
    Usuario,
)
from core.services import correo, tokens


VERIFICACION = correo.PlantillaCorreo(
    clave="verificacion",
    asunto="Confirma tu correo",
    entidad="usuario",
)


def contexto(enlace="https://amis.test/verificar-correo?token=abc"):
    return {"nombre": "Ada", "enlace": enlace, "horas": 48}


class EnvioDeCorreoTest(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.get(email__iexact="aspirante@amis.org")

    def test_envia_y_deja_constancia(self):
        registro = correo.enviar(
            VERIFICACION, self.usuario.email, contexto(),
            entidad_id=self.usuario.id,
        )

        self.assertEqual(registro.estado, EstadoEnvio.ENVIADO)
        self.assertIsNotNone(registro.enviado_en)
        self.assertEqual(registro.numero_intento, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.usuario.email])

    def test_deja_dicho_por_donde_salio(self):
        # `enviado` por si solo miente: el backend de consola acepta cualquier
        # mensaje y devuelve exito sin contactar a nadie. Sin esta marca, una
        # fila en `enviado` se lee como "llego".
        registro = correo.enviar(VERIFICACION, self.usuario.email, contexto())

        self.assertEqual(registro.proveedor_id, "locmem")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"
    )
    def test_la_consola_queda_marcada_como_tal(self):
        registro = correo.enviar(VERIFICACION, self.usuario.email, contexto())

        self.assertEqual(registro.estado, EstadoEnvio.ENVIADO)
        self.assertEqual(registro.proveedor_id, "console")

    def test_manda_texto_y_html(self):
        correo.enviar(VERIFICACION, self.usuario.email, contexto())

        mensaje = mail.outbox[0]
        self.assertIn("Confirma que esta dirección es tuya", mensaje.body)
        tipos = [tipo for _, tipo in mensaje.alternatives]
        self.assertIn("text/html", tipos)

    def test_el_enlace_viaja_en_las_dos_versiones(self):
        enlace = "https://amis.test/verificar-correo?token=xyz"
        correo.enviar(VERIFICACION, self.usuario.email, contexto(enlace))

        mensaje = mail.outbox[0]
        self.assertIn(enlace, mensaje.body)
        self.assertIn(enlace, mensaje.alternatives[0][0])

    def test_un_smtp_caido_no_propaga_la_excepcion(self):
        # La garantia que sostiene todo: el alta o el cobro que dispararon el
        # correo tienen que completarse aunque el correo no salga.
        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("timeout")
        ):
            registro = correo.enviar(
                VERIFICACION, self.usuario.email, contexto()
            )

        self.assertEqual(registro.estado, EstadoEnvio.FALLIDO)
        self.assertIn("timeout", registro.mensaje_error)
        self.assertIsNone(registro.enviado_en)
        self.assertEqual(len(mail.outbox), 0)

    def test_guarda_el_tipo_del_error_y_no_solo_su_texto(self):
        # Un timeout de socket llega con mensaje vacio: sin el tipo, la fila
        # no diria nada de lo que paso.
        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=TimeoutError()
        ):
            registro = correo.enviar(
                VERIFICACION, self.usuario.email, contexto()
            )

        self.assertIn("TimeoutError", registro.mensaje_error)

    def test_una_plantilla_sin_html_se_manda_solo_con_texto(self):
        solo_texto = correo.PlantillaCorreo(
            clave="_firma", asunto="Prueba sin HTML"
        )

        registro = correo.enviar(solo_texto, self.usuario.email, {})

        self.assertEqual(registro.estado, EstadoEnvio.ENVIADO)
        self.assertEqual(mail.outbox[0].alternatives, [])

    def test_una_plantilla_inexistente_queda_fallida_sin_reventar(self):
        rota = correo.PlantillaCorreo(clave="no-existe", asunto="Rota")

        registro = correo.enviar(rota, self.usuario.email, {})

        self.assertEqual(registro.estado, EstadoEnvio.FALLIDO)
        self.assertEqual(len(mail.outbox), 0)

    def test_reintentar_suma_al_contador(self):
        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("caido")
        ):
            registro = correo.enviar(
                VERIFICACION, self.usuario.email, contexto()
            )

        resultado = correo.reintentar(
            registro, VERIFICACION, self.usuario.email, contexto()
        )

        self.assertEqual(resultado.estado, EstadoEnvio.ENVIADO)
        self.assertEqual(resultado.numero_intento, 2)
        self.assertIsNone(resultado.mensaje_error)

    def test_ya_se_envio_solo_cuenta_los_entregados(self):
        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("caido")
        ):
            correo.enviar(
                VERIFICACION, self.usuario.email, contexto(), entidad_id="X-1"
            )

        self.assertFalse(correo.ya_se_envio("usuario", "X-1"))

        correo.enviar(
            VERIFICACION, self.usuario.email, contexto(), entidad_id="X-1"
        )
        self.assertTrue(correo.ya_se_envio("usuario", "X-1"))


@override_settings(EMAIL_REDIRIGIR_A="pruebas@ejemplo.test")
class JaulaDePruebasTest(TestCase):
    """`amis.org` es un dominio real y el seed esta lleno de sus direcciones."""

    def test_ningun_mensaje_alcanza_al_destinatario_original(self):
        correo.enviar(VERIFICACION, "ana.gomez@amis.org", contexto())

        self.assertEqual(mail.outbox[0].to, ["pruebas@ejemplo.test"])
        self.assertNotIn("ana.gomez@amis.org", mail.outbox[0].to)

    def test_el_asunto_dice_a_quien_iba(self):
        # Veinte correos de prueba en la misma bandeja son indistinguibles sin
        # esto.
        correo.enviar(VERIFICACION, "ana.gomez@amis.org", contexto())

        self.assertIn("[prueba -> ana.gomez@amis.org]", mail.outbox[0].subject)

    def test_el_destinatario_real_viaja_en_una_cabecera(self):
        correo.enviar(VERIFICACION, "ana.gomez@amis.org", contexto())

        self.assertEqual(
            mail.outbox[0].extra_headers["X-Destinatario-Real"],
            "ana.gomez@amis.org",
        )

    def test_el_registro_guarda_el_destinatario_real_y_no_el_desviado(self):
        # Lo que se anota es lo que la aplicacion decidio, no lo que hizo la
        # jaula: si no, el registro mentiria sobre a quien se escribio.
        registro = correo.enviar(
            VERIFICACION, "ana.gomez@amis.org", contexto()
        )

        self.assertEqual(registro.destinatario_email, "ana.gomez@amis.org")


class SinJaulaTest(TestCase):
    @override_settings(EMAIL_REDIRIGIR_A="")
    def test_vacia_es_el_comportamiento_de_produccion(self):
        # Se activa por ausencia, no por presencia: olvidarla en produccion no
        # rompe nada.
        correo.enviar(VERIFICACION, "ana.gomez@amis.org", contexto())

        self.assertEqual(mail.outbox[0].to, ["ana.gomez@amis.org"])
        self.assertNotIn("[prueba", mail.outbox[0].subject)


class TokensTest(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.otro = Usuario.objects.get(email__iexact="admin@amis.org")

    def test_emitir_devuelve_un_token_usable(self):
        token = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)

        self.assertEqual(
            tokens.canjear(token, PropositoToken.VERIFICACION), self.usuario
        )

    def test_en_la_base_solo_vive_el_hash(self):
        # Un respaldo filtrado no puede servir para entrar a una cuenta.
        token = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)

        fila = TokenRecuperacion.objects.get(usuario=self.usuario)
        self.assertNotEqual(fila.token_hash, token)
        self.assertNotIn(token, fila.token_hash)
        self.assertEqual(len(fila.token_hash), 64)

    def test_un_token_solo_se_canjea_una_vez(self):
        token = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)
        tokens.canjear(token, PropositoToken.VERIFICACION)

        with self.assertRaises(tokens.TokenInvalido):
            tokens.canjear(token, PropositoToken.VERIFICACION)

    def test_un_token_caducado_no_sirve(self):
        token = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)
        TokenRecuperacion.objects.filter(usuario=self.usuario).update(
            expira_en=timezone.now() - timezone.timedelta(minutes=1)
        )

        with self.assertRaises(tokens.TokenInvalido):
            tokens.canjear(token, PropositoToken.VERIFICACION)

    def test_un_token_no_sirve_para_otro_proposito(self):
        # Un enlace de verificacion no puede convertirse en uno de cambio de
        # contrasena.
        token = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)

        with self.assertRaises(tokens.TokenInvalido):
            tokens.canjear(token, PropositoToken.RECUPERACION)

    def test_emitir_de_nuevo_invalida_el_anterior(self):
        viejo = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)
        nuevo = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)

        with self.assertRaises(tokens.TokenInvalido):
            tokens.canjear(viejo, PropositoToken.VERIFICACION)
        self.assertEqual(
            tokens.canjear(nuevo, PropositoToken.VERIFICACION), self.usuario
        )

    def test_los_dos_propositos_conviven_en_la_misma_cuenta(self):
        verificar = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)
        recuperar = tokens.emitir(self.usuario, PropositoToken.RECUPERACION)

        self.assertEqual(
            tokens.canjear(verificar, PropositoToken.VERIFICACION), self.usuario
        )
        self.assertEqual(
            tokens.canjear(recuperar, PropositoToken.RECUPERACION), self.usuario
        )

    def test_el_token_de_una_cuenta_no_abre_la_de_otra(self):
        token = tokens.emitir(self.usuario, PropositoToken.VERIFICACION)

        self.assertNotEqual(
            tokens.canjear(token, PropositoToken.VERIFICACION), self.otro
        )

    def test_un_token_inventado_se_rechaza(self):
        for basura in ("", None, "no-es-un-token"):
            with self.subTest(token=basura):
                with self.assertRaises(tokens.TokenInvalido):
                    tokens.canjear(basura, PropositoToken.VERIFICACION)

    def test_dos_emisiones_nunca_dan_el_mismo_valor(self):
        valores = {
            tokens.emitir(self.usuario, PropositoToken.VERIFICACION)
            for _ in range(5)
        }

        self.assertEqual(len(valores), 5)

    @override_settings(FRONTEND_BASE_URL="https://amis.test")
    def test_el_enlace_apunta_al_frontend(self):
        # Nunca al API, y nunca a una base que venga de la peticion: eso seria
        # una redireccion abierta repartida por correo.
        enlace = tokens.construir_enlace("/verificar-correo", "abc123")

        self.assertEqual(
            enlace, "https://amis.test/verificar-correo?token=abc123"
        )

    def test_las_vigencias_son_distintas_por_proposito(self):
        # Recuperar dura menos porque permite mas.
        tokens.emitir(self.usuario, PropositoToken.VERIFICACION)
        tokens.emitir(self.usuario, PropositoToken.RECUPERACION)

        filas = {
            f.proposito: f.expira_en
            for f in TokenRecuperacion.objects.filter(usuario=self.usuario)
        }
        self.assertGreater(
            filas[PropositoToken.VERIFICACION],
            filas[PropositoToken.RECUPERACION],
        )


class ComandoReintentarTest(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.get(email__iexact="aspirante@amis.org")

    def _fallido(self):
        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("caido")
        ):
            return correo.enviar(VERIFICACION, self.usuario.email, contexto())

    def test_no_enumera_lo_que_no_puede_reintentar(self):
        """Lo no reintentable se cuenta, no se lista.

        Esas filas nunca cambian de estado, asi que enumerarlas las repetiria
        en cada pasada del cron para siempre, y un fallo nuevo quedaria
        enterrado bajo el ruido de ayer.
        """
        from io import StringIO

        from django.core.management import call_command

        self._fallido()
        salida = StringIO()
        call_command("reintentar_correos", stdout=salida)

        texto = salida.getvalue()
        self.assertIn("1 fallidos no reintentables", texto)
        self.assertNotIn("verificacion", texto)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_toca_los_que_agotaron_los_intentos(self):
        from io import StringIO

        from django.core.management import call_command

        registro = self._fallido()
        EnvioCorreo.objects.filter(pk=registro.pk).update(numero_intento=3)

        salida = StringIO()
        call_command("reintentar_correos", stdout=salida)

        self.assertIn("No hay correos fallidos", salida.getvalue())
