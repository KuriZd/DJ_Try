"""Listado de compras propias: `GET /api/compras-psicometricas/`.

Es lo que el archivero de pruebas necesita para decir cuantas pruebas quedan
por realizar. Hasta ahora una compra solo se leia anidada en la orden que la
pago, y para eso hay que acordarse de la referencia.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    CompraPaquetePsicometrico,
    EstadoCompraPaquete,
    PaquetePsicometrico,
    Usuario,
)


class ComprasPsicometricasApiTest(TestCase):
    def setUp(self):
        self.comprador = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.otra_persona = Usuario.objects.get(email__iexact="admin@amis.org")
        self.perfil = PaquetePsicometrico.objects.get(clave="perfil")
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.comprador)

    def url(self):
        return reverse("api:compra-psicometrica-list")

    def crear_compra(
        self,
        *,
        comprador=None,
        estado=EstadoCompraPaquete.PAGADA,
        creditos_totales=3,
        creditos_consumidos=0,
        vigente_hasta=None,
        pagada_en=None,
    ):
        ahora = timezone.now()
        return CompraPaquetePsicometrico.objects.create(
            id=uuid.uuid4(),
            comprador=comprador or self.comprador,
            paquete=self.perfil,
            paquete_nombre=self.perfil.nombre,
            cantidad_pruebas=creditos_totales,
            precio_unitario=None,
            monto=Decimal("2190.00"),
            moneda="MXN",
            estado=estado,
            creditos_totales=creditos_totales,
            creditos_consumidos=creditos_consumidos,
            vigente_hasta=vigente_hasta,
            pagada_en=pagada_en
            or (ahora if estado == EstadoCompraPaquete.PAGADA else None),
            creado_en=ahora,
            actualizado_en=ahora,
        )

    # -- acceso -----------------------------------------------------------

    def test_exige_sesion(self):
        respuesta = APIClient().get(self.url())

        self.assertEqual(respuesta.status_code, 401)

    def test_devuelve_las_compras_propias(self):
        compra = self.crear_compra()

        respuesta = self.cliente.get(self.url())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.data), 1)
        self.assertEqual(respuesta.data[0]["id"], str(compra.id))

    def test_no_deja_ver_la_compra_de_otra_persona(self):
        # Una compra es del comprador y de nadie mas: aqui no hay un permiso
        # de "ver todas" como en postulaciones.
        self.crear_compra(comprador=self.otra_persona)

        respuesta = self.cliente.get(self.url())

        self.assertEqual(respuesta.data, [])

    def test_el_detalle_de_otra_persona_responde_404(self):
        ajena = self.crear_compra(comprador=self.otra_persona)

        respuesta = self.cliente.get(
            reverse("api:compra-psicometrica-detail", args=[ajena.id])
        )

        # 404 y no 403: confirmar que existe ya diria de mas.
        self.assertEqual(respuesta.status_code, 404)

    # -- que compras salen ------------------------------------------------

    def test_omite_las_que_no_estan_pagadas(self):
        # Una pendiente no da creditos: la orden puede quedarse sin cobrar.
        # Ensenarla prometeria pruebas que no se pueden aplicar.
        for estado in (
            EstadoCompraPaquete.PENDIENTE,
            EstadoCompraPaquete.CANCELADA,
            EstadoCompraPaquete.REEMBOLSADA,
        ):
            with self.subTest(estado=estado):
                CompraPaquetePsicometrico.objects.all().delete()
                self.crear_compra(estado=estado)

                respuesta = self.cliente.get(self.url())

                self.assertEqual(respuesta.data, [])

    def test_conserva_las_agotadas(self):
        # Sin creditos ya no hay nada por realizar, pero la compra sigue
        # siendo suya: recortarla aqui la dejaria sin ver que compro.
        self.crear_compra(creditos_totales=3, creditos_consumidos=3)

        respuesta = self.cliente.get(self.url())

        self.assertEqual(len(respuesta.data), 1)
        self.assertEqual(respuesta.data[0]["creditos_disponibles"], 0)

    def test_conserva_las_vencidas(self):
        # Quien pinta decide como mostrar una vigencia terminada; el endpoint
        # manda la fecha y no la interpreta.
        vencida = timezone.now() - timedelta(days=1)
        self.crear_compra(vigente_hasta=vencida)

        respuesta = self.cliente.get(self.url())

        self.assertEqual(len(respuesta.data), 1)
        self.assertIsNotNone(respuesta.data[0]["vigente_hasta"])

    # -- forma de la respuesta --------------------------------------------

    def test_expone_los_creditos_y_la_ficha_del_paquete(self):
        self.crear_compra(creditos_totales=3, creditos_consumidos=1)

        fila = self.cliente.get(self.url()).data[0]

        self.assertEqual(fila["paquete_clave"], "perfil")
        self.assertEqual(fila["paquete_nombre"], self.perfil.nombre)
        self.assertEqual(fila["creditos_totales"], 3)
        self.assertEqual(fila["creditos_consumidos"], 1)
        self.assertEqual(fila["creditos_disponibles"], 2)
        self.assertEqual(fila["estado"], EstadoCompraPaquete.PAGADA)

    def test_ordena_lo_ultimo_comprado_primero(self):
        ahora = timezone.now()
        vieja = self.crear_compra(pagada_en=ahora - timedelta(days=10))
        reciente = self.crear_compra(pagada_en=ahora)

        respuesta = self.cliente.get(self.url())

        self.assertEqual(
            [fila["id"] for fila in respuesta.data],
            [str(reciente.id), str(vieja.id)],
        )

    def test_es_de_solo_lectura(self):
        respuesta = self.cliente.post(self.url(), {}, format="json")

        self.assertEqual(respuesta.status_code, 405)
