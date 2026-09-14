"""Comprobante de pago por correo (fase 3).

El correo sale de un solo sitio —`aplicar_resultado_de_captura`— y por eso
cubre las dos vueltas del pago: la del navegador y la del webhook cuando PayPal
libera un cobro retenido. Lo que se prueba aqui es que salga una vez, con lo
que dice la orden, y nunca antes de que el dinero este confirmado.
"""

import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    CompraPaquetePsicometrico,
    EnvioCorreo,
    EstadoCompraPaquete,
    EstadoEnvio,
    EstadoPagoPaypal,
    OrdenPagoPaypal,
    PaquetePsicometrico,
    Usuario,
)
from core.services import avisos, correo


def _orden_cobrada(comprador, *, pruebas=3, monto="2190.00", moneda="MXN"):
    """Una orden ya en COMPLETED con su compra acreditada."""
    ahora = timezone.now()
    paquete = PaquetePsicometrico.objects.get(clave="perfil")

    compra = CompraPaquetePsicometrico.objects.create(
        id=uuid.uuid4(),
        comprador=comprador,
        paquete=paquete,
        paquete_nombre="Perfil profesional",
        cantidad_pruebas=pruebas,
        monto=Decimal(monto),
        moneda=moneda,
        estado=EstadoCompraPaquete.PAGADA,
        creditos_totales=pruebas,
        creditos_consumidos=0,
        vigente_hasta=ahora + timezone.timedelta(days=180),
        pagada_en=ahora,
        creado_en=ahora,
        actualizado_en=ahora,
    )

    return OrdenPagoPaypal.objects.create(
        id=uuid.uuid4(),
        referencia_interna=f"PAY-QA-{uuid.uuid4().hex[:12].upper()}",
        comprador=comprador,
        compra=compra,
        paypal_order_id=uuid.uuid4().hex[:17].upper(),
        monto=Decimal(monto),
        moneda=moneda,
        estado=EstadoPagoPaypal.COMPLETED,
        pagado_en=ahora,
        creado_en=ahora,
        actualizado_en=ahora,
    )


class ContenidoDelComprobanteTest(TestCase):
    def setUp(self):
        self.comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.orden = _orden_cobrada(self.comprador)

    def test_lleva_lo_mismo_que_el_comprobante_en_pantalla(self):
        # Si el correo y la pantalla dijeran cosas distintas, el folio dejaria
        # de servir para reclamar.
        avisos.enviar_comprobante(self.orden)

        cuerpo = mail.outbox[0].body
        self.assertIn(self.orden.referencia_interna, cuerpo)
        self.assertIn("Perfil profesional", cuerpo)
        self.assertIn("$2,190.00 MXN", cuerpo)
        self.assertIn("3 pruebas en tu expediente", cuerpo)

    def test_va_al_comprador_y_no_a_otro(self):
        avisos.enviar_comprobante(self.orden)

        self.assertEqual(mail.outbox[0].to, [self.comprador.email])

    def test_usa_el_singular_con_una_sola_prueba(self):
        orden = _orden_cobrada(self.comprador, pruebas=1, monto="890.00")

        avisos.enviar_comprobante(orden)

        self.assertIn("1 prueba en tu expediente", mail.outbox[0].body)

    def test_respeta_una_moneda_distinta_al_peso(self):
        orden = _orden_cobrada(self.comprador, monto="120.00", moneda="USD")

        avisos.enviar_comprobante(orden)

        self.assertIn("120.00 USD", mail.outbox[0].body)
        self.assertNotIn("$120.00 MXN", mail.outbox[0].body)

    def test_una_orden_sin_paquete_no_habla_de_pruebas(self):
        """Una orden puede cobrar un reporte suelto en vez de un paquete.

        La orden se arma en memoria y no se guarda: el CHECK
        `orden_pago_paypal_objeto_unico` exige que apunte a un reporte o a una
        compra, y montar un reporte entero para una sola asercion seria mucho
        andamio. Lo que se ejercita —el contexto y la plantilla cuando no hay
        compra detras— es identico.
        """
        orden = OrdenPagoPaypal(
            id=uuid.uuid4(),
            referencia_interna="PAY-QA-SIN-PAQUETE",
            comprador=self.comprador,
            compra=None,
            monto=Decimal("890.00"),
            moneda="MXN",
            estado=EstadoPagoPaypal.COMPLETED,
            pagado_en=timezone.now(),
        )

        avisos.enviar_comprobante(orden)

        cuerpo = mail.outbox[0].body
        self.assertIn("Tu pago quedó registrado", cuerpo)
        self.assertIn("PAY-QA-SIN-PAQUETE", cuerpo)
        self.assertIn("$890.00 MXN", cuerpo)
        self.assertNotIn("en tu expediente", cuerpo)

    def test_no_inventa_un_importe_con_un_monto_ilegible(self):
        # `Decimal(None)` revienta y `0` seria peor: afirmaria que no pago
        # nada. La ausencia se propaga como ausencia.
        self.assertIsNone(avisos._importe(None, "MXN"))
        self.assertIsNone(avisos._importe("", "MXN"))
        self.assertIsNone(avisos._importe("no-es-un-monto", "MXN"))

    def test_los_meses_van_en_minuscula(self):
        # El filtro `date` de Django los capitaliza incluso con es-mx, y el
        # comprobante en pantalla los escribe en minuscula. Dos formas de la
        # misma fecha en el mismo comprobante seria un descuido visible.
        avisos.enviar_comprobante(self.orden)

        cuerpo = mail.outbox[0].body
        for mes in avisos.MESES:
            self.assertNotIn(mes.capitalize(), cuerpo)

    def test_la_fecha_se_escribe_en_la_zona_de_quien_lee(self):
        """Un cobro nocturno en Mexico no puede aparecer fechado al dia
        siguiente solo porque en UTC ya lo sea."""
        from datetime import datetime, timezone as tz

        # 1 de septiembre, 20:00 en Ciudad de Mexico = 2 de septiembre, 02:00 UTC.
        self.orden.pagado_en = datetime(2026, 9, 2, 2, 0, tzinfo=tz.utc)

        contexto = avisos.contexto_comprobante(self.orden)

        self.assertEqual(contexto["pagado_en"], "1 de septiembre de 2026")

    def test_una_fecha_ausente_no_se_escribe(self):
        self.assertIsNone(avisos._fecha_larga(None))

    def test_manda_texto_y_html(self):
        avisos.enviar_comprobante(self.orden)

        tipos = [tipo for _, tipo in mail.outbox[0].alternatives]
        self.assertIn("text/html", tipos)


class IdempotenciaDelComprobanteTest(TestCase):
    def setUp(self):
        self.comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.orden = _orden_cobrada(self.comprador)

    def test_no_se_manda_dos_veces_para_la_misma_orden(self):
        avisos.enviar_comprobante(self.orden)
        segundo = avisos.enviar_comprobante(self.orden)

        self.assertIsNone(segundo)
        self.assertEqual(len(mail.outbox), 1)

    def test_un_envio_fallido_no_bloquea_el_siguiente(self):
        # `ya_se_envio` solo cuenta los entregados: si el primero se cayo, el
        # comprobante todavia debe poder salir.
        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("caido")
        ):
            avisos.enviar_comprobante(self.orden)

        self.assertEqual(len(mail.outbox), 0)

        avisos.enviar_comprobante(self.orden)
        self.assertEqual(len(mail.outbox), 1)

    def test_queda_registrado_contra_la_orden(self):
        avisos.enviar_comprobante(self.orden)

        registro = EnvioCorreo.objects.get(entidad="orden_pago")
        self.assertEqual(registro.entidad_id, self.orden.referencia_interna)
        self.assertEqual(registro.estado, EstadoEnvio.ENVIADO)
        self.assertEqual(registro.destinatario_email, self.comprador.email)


class ReintentoDelComprobanteTest(TestCase):
    def setUp(self):
        self.comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.orden = _orden_cobrada(self.comprador)

    def test_es_reintentable_porque_se_deriva_de_la_orden(self):
        from io import StringIO

        from django.core.management import call_command

        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("caido")
        ):
            avisos.enviar_comprobante(self.orden)

        salida = StringIO()
        call_command("reintentar_correos", stdout=salida)

        self.assertIn("enviado", salida.getvalue())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.orden.referencia_interna, mail.outbox[0].body)

    def test_se_omite_si_la_orden_desaparecio(self):
        from io import StringIO

        from django.core.management import call_command

        with patch.object(
            correo.EmailMultiAlternatives, "send", side_effect=OSError("caido")
        ):
            avisos.enviar_comprobante(self.orden)

        EnvioCorreo.objects.update(entidad_id="PAY-QUE-NO-EXISTE")
        salida = StringIO()
        call_command("reintentar_correos", stdout=salida)

        self.assertIn("ya no existe", salida.getvalue())
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_REDIRIGIR_A="pruebas@ejemplo.test")
class ComprobanteEnLaJaulaTest(TestCase):
    def test_no_alcanza_al_comprador_real(self):
        comprador = Usuario.objects.get(email__iexact="admin@amis.org")
        orden = _orden_cobrada(comprador)

        registro = avisos.enviar_comprobante(orden)

        self.assertEqual(mail.outbox[0].to, ["pruebas@ejemplo.test"])
        self.assertEqual(registro.destinatario_email, "admin@amis.org")


class SoloTrasElCommitTest(TestCase):
    """El correo se engancha con `transaction.on_commit`."""

    def test_un_cobro_deshecho_no_manda_comprobante(self):
        # Avisar de un cobro que despues no cuajo seria peor que no avisar:
        # nadie puede retirar un correo del buzon de otro.
        from django.db import transaction

        comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")

        class Deshecho(Exception):
            pass

        try:
            with transaction.atomic():
                orden = _orden_cobrada(comprador)
                transaction.on_commit(
                    lambda: avisos.enviar_comprobante(orden)
                )
                raise Deshecho
        except Deshecho:
            pass

        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EnvioCorreo.objects.exists())
