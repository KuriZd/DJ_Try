from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import (
    CompraPaquetePsicometrico,
    EstadoCompraPaquete,
    EstadoPagoPaypal,
    OrdenPagoPaypal,
    PaquetePsicometrico,
    TipoTransaccionPaypal,
    TransaccionPagoPaypal,
    Usuario,
)


class CatalogoPaquetesTest(TestCase):
    def test_catalogo_es_publico_y_trae_los_precios(self):
        respuesta = APIClient().get(reverse("api:paquete-psicometrico-list"))

        self.assertEqual(respuesta.status_code, 200)
        por_clave = {fila["clave"]: fila for fila in respuesta.data}
        self.assertEqual(por_clave["perfil"]["precio_total"], "2190.00")
        self.assertEqual(por_clave["perfil"]["cantidad_pruebas"], 3)
        self.assertFalse(por_clave["perfil"]["es_a_medida"])
        self.assertTrue(por_clave["medida"]["es_a_medida"])
        self.assertEqual(por_clave["medida"]["precio_unitario"], "640.00")
        self.assertEqual(por_clave["medida"]["cantidad_minima"], 6)

    def test_un_paquete_retirado_deja_de_publicarse(self):
        PaquetePsicometrico.objects.filter(clave="perfil").update(activo=False)

        respuesta = APIClient().get(reverse("api:paquete-psicometrico-list"))

        self.assertNotIn(
            "perfil", [fila["clave"] for fila in respuesta.data]
        )


class ComprarPaqueteApiTest(TestCase):
    def setUp(self):
        self.comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.perfil = PaquetePsicometrico.objects.get(clave="perfil")
        self.medida = PaquetePsicometrico.objects.get(clave="medida")
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.comprador)

    @staticmethod
    def respuesta_crear():
        return {
            "paypal_order_id": "5O190127TN364715T",
            "estado": "CREATED",
            "approval_url": "https://paypal.test/approve",
            "respuesta": {"id": "5O190127TN364715T", "status": "CREATED"},
        }

    @staticmethod
    def respuesta_capturar(monto="2190.00", estado_captura="COMPLETED"):
        return {
            "paypal_order_id": "5O190127TN364715T",
            "estado": "COMPLETED",
            "paypal_capture_id": "3C679366HH908993F",
            "estado_captura": estado_captura,
            "monto": monto,
            "moneda": "MXN",
            "comision": "80.31",
            "monto_neto": "2109.69",
            "respuesta": {"id": "5O190127TN364715T", "status": "COMPLETED"},
        }

    def url_ordenes(self):
        return reverse("api:ordenes-paypal")

    def url_capturar(self):
        return reverse("api:capturar-orden-paypal")

    def url_cancelar(self):
        return reverse("api:cancelar-orden-paypal")

    def comprar(self, crear_orden, cuerpo, **extra):
        crear_orden.return_value = self.respuesta_crear()
        return self.cliente.post(
            self.url_ordenes(), cuerpo, format="json", **extra
        )

    # -- reserva ----------------------------------------------------------

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_cobra_el_precio_del_catalogo_y_no_el_que_manda_el_cliente(
        self, crear_orden
    ):
        respuesta = self.comprar(
            crear_orden, {"paquete_clave": "perfil", "monto": "1.00"}
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data["monto"], "2190.00")
        self.assertEqual(respuesta.data["compra"]["cantidad_pruebas"], 3)
        self.assertEqual(
            respuesta.data["compra"]["estado"], EstadoCompraPaquete.PENDIENTE
        )
        self.assertEqual(
            crear_orden.call_args.kwargs["monto"], Decimal("2190.00")
        )

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_paquete_a_la_medida_multiplica_por_la_cantidad(self, crear_orden):
        respuesta = self.comprar(
            crear_orden, {"paquete_clave": "medida", "cantidad": 8}
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data["monto"], "5120.00")
        self.assertEqual(respuesta.data["compra"]["cantidad_pruebas"], 8)

    def test_rechaza_cantidad_fuera_del_rango(self):
        respuesta = self.cliente.post(
            self.url_ordenes(),
            {"paquete_clave": "medida", "cantidad": 99},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(CompraPaquetePsicometrico.objects.exists())

    def test_paquete_a_la_medida_exige_cantidad(self):
        respuesta = self.cliente.post(
            self.url_ordenes(), {"paquete_clave": "medida"}, format="json"
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_paquete_cerrado_no_acepta_cantidad(self):
        respuesta = self.cliente.post(
            self.url_ordenes(),
            {"paquete_clave": "perfil", "cantidad": 9},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_exige_reporte_o_paquete_pero_no_ninguno(self):
        respuesta = self.cliente.post(self.url_ordenes(), {}, format="json")

        self.assertEqual(respuesta.status_code, 400)

    def test_rechaza_paquete_inexistente(self):
        respuesta = self.cliente.post(
            self.url_ordenes(), {"paquete_clave": "regalado"}, format="json"
        )

        self.assertEqual(respuesta.status_code, 400)

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_la_misma_clave_de_idempotencia_no_abre_una_segunda_compra(
        self, crear_orden
    ):
        cuerpo = {"paquete_clave": "perfil"}

        primera = self.comprar(
            crear_orden, cuerpo, HTTP_IDEMPOTENCY_KEY="checkout-77"
        )
        segunda = self.comprar(
            crear_orden, cuerpo, HTTP_IDEMPOTENCY_KEY="checkout-77"
        )

        self.assertEqual(primera.status_code, 201)
        self.assertEqual(segunda.status_code, 200)
        self.assertTrue(segunda.data["reutilizada"])
        self.assertEqual(CompraPaquetePsicometrico.objects.count(), 1)
        crear_orden.assert_called_once()

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_sin_clave_de_idempotencia_dos_compras_son_dos_compras(
        self, crear_orden
    ):
        # Un paquete se puede comprar dos veces a proposito, a diferencia de un
        # reporte suelto: sin clave que las una, son dos compras distintas.
        crear_orden.side_effect = [
            self.respuesta_crear(),
            {**self.respuesta_crear(), "paypal_order_id": "OTRA-ORDEN"},
        ]
        cuerpo = {"paquete_clave": "perfil"}

        primera = self.cliente.post(self.url_ordenes(), cuerpo, format="json")
        segunda = self.cliente.post(self.url_ordenes(), cuerpo, format="json")

        self.assertEqual(primera.status_code, 201)
        self.assertEqual(segunda.status_code, 201)
        self.assertEqual(CompraPaquetePsicometrico.objects.count(), 2)

    # -- cobro ------------------------------------------------------------

    def _orden_creada(self, crear_orden, cuerpo=None):
        self.comprar(crear_orden, cuerpo or {"paquete_clave": "perfil"})
        return OrdenPagoPaypal.objects.get()

    @patch("core.services.pagos.paypal_client.capturar_orden")
    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_captura_marca_la_compra_pagada_y_deja_creditos(
        self, crear_orden, capturar_orden
    ):
        orden = self._orden_creada(crear_orden)
        capturar_orden.return_value = self.respuesta_capturar()

        respuesta = self.cliente.post(
            self.url_capturar(),
            {"paypal_order_id": orden.paypal_order_id},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.data["cobrada_ahora"])
        self.assertEqual(respuesta.data["estado"], EstadoPagoPaypal.COMPLETED)

        orden.refresh_from_db()
        self.assertIsNotNone(orden.pagado_en)

        compra = CompraPaquetePsicometrico.objects.get()
        self.assertEqual(compra.estado, EstadoCompraPaquete.PAGADA)
        self.assertEqual(compra.creditos_disponibles, 3)
        self.assertIsNotNone(compra.pagada_en)
        # El paquete Perfil vence a los 12 meses.
        self.assertIsNotNone(compra.vigente_hasta)

        transaccion = TransaccionPagoPaypal.objects.get()
        self.assertEqual(transaccion.tipo, TipoTransaccionPaypal.CAPTURE)
        self.assertEqual(transaccion.paypal_capture_id, "3C679366HH908993F")
        self.assertEqual(transaccion.comision, Decimal("80.31"))
        self.assertTrue(
            orden.eventos.filter(tipo_evento="ORDER.CAPTURED").exists()
        )

    @patch("core.services.pagos.paypal_client.capturar_orden")
    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_capturar_dos_veces_no_cobra_dos_veces(
        self, crear_orden, capturar_orden
    ):
        orden = self._orden_creada(crear_orden)
        capturar_orden.return_value = self.respuesta_capturar()
        cuerpo = {"paypal_order_id": orden.paypal_order_id}

        primera = self.cliente.post(self.url_capturar(), cuerpo, format="json")
        segunda = self.cliente.post(self.url_capturar(), cuerpo, format="json")

        self.assertTrue(primera.data["cobrada_ahora"])
        self.assertEqual(segunda.status_code, 200)
        self.assertFalse(segunda.data["cobrada_ahora"])
        capturar_orden.assert_called_once()
        self.assertEqual(TransaccionPagoPaypal.objects.count(), 1)

    @patch("core.services.pagos.paypal_client.capturar_orden")
    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_no_entrega_nada_si_paypal_cobro_otro_importe(
        self, crear_orden, capturar_orden
    ):
        orden = self._orden_creada(crear_orden)
        capturar_orden.return_value = self.respuesta_capturar(monto="1.00")

        respuesta = self.cliente.post(
            self.url_capturar(),
            {"paypal_order_id": orden.paypal_order_id},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.data["code"], "MONTO_CAPTURADO_DISTINTO")

        orden.refresh_from_db()
        self.assertNotEqual(orden.estado, EstadoPagoPaypal.COMPLETED)
        compra = CompraPaquetePsicometrico.objects.get()
        self.assertEqual(compra.estado, EstadoCompraPaquete.PENDIENTE)
        self.assertEqual(TransaccionPagoPaypal.objects.count(), 0)

    @patch("core.services.pagos.paypal_client.capturar_orden")
    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_cobro_retenido_por_paypal_no_entrega_creditos(
        self, crear_orden, capturar_orden
    ):
        orden = self._orden_creada(crear_orden)
        capturar_orden.return_value = self.respuesta_capturar(
            estado_captura="PENDING"
        )

        respuesta = self.cliente.post(
            self.url_capturar(),
            {"paypal_order_id": orden.paypal_order_id},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.data["cobrada_ahora"])
        compra = CompraPaquetePsicometrico.objects.get()
        self.assertEqual(compra.estado, EstadoCompraPaquete.PENDIENTE)
        self.assertEqual(
            TransaccionPagoPaypal.objects.get().estado,
            EstadoPagoPaypal.PENDING,
        )

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_no_se_puede_capturar_la_orden_de_otra_persona(self, crear_orden):
        orden = self._orden_creada(crear_orden)
        otro = Usuario.objects.exclude(pk=self.comprador.pk).first()
        intruso = APIClient()
        intruso.force_authenticate(user=otro)

        respuesta = intruso.post(
            self.url_capturar(),
            {"paypal_order_id": orden.paypal_order_id},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)
        orden.refresh_from_db()
        self.assertNotEqual(orden.estado, EstadoPagoPaypal.COMPLETED)

    def test_capturar_requiere_autenticacion(self):
        respuesta = APIClient().post(
            self.url_capturar(),
            {"paypal_order_id": "5O190127TN364715T"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 401)

    # -- cancelacion y consulta -------------------------------------------

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_cancelar_cierra_la_orden_y_su_compra(self, crear_orden):
        orden = self._orden_creada(crear_orden)

        respuesta = self.cliente.post(
            self.url_cancelar(),
            {"paypal_order_id": orden.paypal_order_id},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.data["cancelada_ahora"])
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoPagoPaypal.CANCELLED)
        self.assertEqual(
            CompraPaquetePsicometrico.objects.get().estado,
            EstadoCompraPaquete.CANCELADA,
        )

    @patch("core.services.pagos.paypal_client.capturar_orden")
    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_cancelar_despues_de_pagar_no_deshace_el_cobro(
        self, crear_orden, capturar_orden
    ):
        orden = self._orden_creada(crear_orden)
        capturar_orden.return_value = self.respuesta_capturar()
        cuerpo = {"paypal_order_id": orden.paypal_order_id}
        self.cliente.post(self.url_capturar(), cuerpo, format="json")

        respuesta = self.cliente.post(self.url_cancelar(), cuerpo, format="json")

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.data["cancelada_ahora"])
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoPagoPaypal.COMPLETED)
        self.assertEqual(
            CompraPaquetePsicometrico.objects.get().estado,
            EstadoCompraPaquete.PAGADA,
        )

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_consulta_la_orden_por_su_referencia(self, crear_orden):
        orden = self._orden_creada(crear_orden)

        respuesta = self.cliente.get(
            reverse("api:orden-paypal", args=[orden.referencia_interna])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.data["referencia_interna"], orden.referencia_interna
        )

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_no_expone_la_orden_de_otra_persona(self, crear_orden):
        orden = self._orden_creada(crear_orden)
        otro = Usuario.objects.exclude(pk=self.comprador.pk).first()
        intruso = APIClient()
        intruso.force_authenticate(user=otro)

        respuesta = intruso.get(
            reverse("api:orden-paypal", args=[orden.referencia_interna])
        )

        self.assertEqual(respuesta.status_code, 404)

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_lista_solo_las_ordenes_propias(self, crear_orden):
        self._orden_creada(crear_orden)
        otro = Usuario.objects.exclude(pk=self.comprador.pk).first()
        intruso = APIClient()
        intruso.force_authenticate(user=otro)

        propias = self.cliente.get(self.url_ordenes())
        ajenas = intruso.get(self.url_ordenes())

        self.assertEqual(len(propias.data), 1)
        self.assertEqual(len(ajenas.data), 0)


class CotizarPaqueteTest(TestCase):
    """La cotizacion vive en el modelo: es el unico lugar que fija importes."""

    def test_paquete_cerrado_ignora_la_cantidad_omitida(self):
        perfil = PaquetePsicometrico.objects.get(clave="perfil")

        self.assertEqual(perfil.cotizar(), (3, Decimal("2190.00")))
        self.assertEqual(perfil.cotizar(3), (3, Decimal("2190.00")))

    def test_paquete_cerrado_rechaza_otra_cantidad(self):
        perfil = PaquetePsicometrico.objects.get(clave="perfil")

        with self.assertRaises(ValueError):
            perfil.cotizar(4)

    def test_paquete_a_la_medida_cobra_por_volumen(self):
        medida = PaquetePsicometrico.objects.get(clave="medida")

        self.assertEqual(medida.cotizar(6), (6, Decimal("3840.00")))
        self.assertEqual(medida.cotizar(30), (30, Decimal("19200.00")))

    def test_paquete_a_la_medida_respeta_los_limites(self):
        medida = PaquetePsicometrico.objects.get(clave="medida")

        for cantidad in (None, 5, 31):
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(ValueError):
                    medida.cotizar(cantidad)
