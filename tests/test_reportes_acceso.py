"""Qué abre el pago de un reporte suelto.

Un informe que aplicó la plataforma tiene precio: hasta que se paga, su dueño
sabe que existe pero no ve los resultados ni descarga el PDF. El que archivó
la propia persona es suyo desde el principio, y el administrador lo ve todo.
"""

import tempfile
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.api.serializers import permisos_de
from core.api.views import PERMISO_ADMIN_REPORTES
from core.models import (
    Aspirante,
    EstadoPagoPaypal,
    OrdenPagoPaypal,
    OrigenReportePsicometrico,
    ReportePsicometrico,
    Usuario,
)

PDF = b"%PDF-1.4\nreporte de prueba\n%%EOF"


class AccesoReportePsicometricoTest(TestCase):
    def setUp(self):
        self.media_temporal = tempfile.TemporaryDirectory()
        self.configuracion_media = override_settings(
            MEDIA_ROOT=self.media_temporal.name
        )
        self.configuracion_media.enable()
        self.admin = Usuario.objects.get(email__iexact="admin@amis.org")
        self.dueno = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.aspirante = Aspirante.objects.get(usuario=self.dueno)

    def tearDown(self):
        self.configuracion_media.disable()
        self.media_temporal.cleanup()

    def cliente_de(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    def reporte(self, **cambios):
        ahora = timezone.now()
        datos = {
            "id": uuid.uuid4(),
            "aspirante": self.aspirante,
            "referencia_evaluacion_externa": "EVAL-ACC-001",
            "nombre_original": "razonamiento.pdf",
            "mime_type": "application/pdf",
            "tamano_bytes": len(PDF),
            "checksum_sha256": "a" * 64,
            "precio": Decimal("499.00"),
            "moneda": "MXN",
            "origen": OrigenReportePsicometrico.PLATAFORMA,
            "disponible_para_compra": True,
            "puntaje": 86,
            "nivel": "Alto",
            "escalas": [{"nombre": "Series numericas", "puntaje": 88}],
            "creado_en": ahora,
            "actualizado_en": ahora,
            **cambios,
        }
        reporte = ReportePsicometrico(**datos)
        reporte.archivo.save("reporte.pdf", ContentFile(PDF), save=False)
        reporte.save(force_insert=True)
        return reporte

    def pagar(self, reporte, estado=EstadoPagoPaypal.COMPLETED):
        ahora = timezone.now()
        return OrdenPagoPaypal.objects.create(
            id=uuid.uuid4(),
            referencia_interna=f"PAY-{uuid.uuid4().hex[:12]}",
            comprador=self.dueno,
            reporte=reporte,
            monto=reporte.precio,
            moneda=reporte.moneda,
            estado=estado,
            creado_en=ahora,
            actualizado_en=ahora,
            pagado_en=ahora if estado == EstadoPagoPaypal.COMPLETED else None,
        )

    def detalle(self, reporte, usuario=None):
        return self.cliente_de(usuario or self.dueno).get(
            reverse("api:reporte-psicometrico-detail", args=[reporte.id])
        )

    def descargar(self, reporte, usuario=None):
        respuesta = self.cliente_de(usuario or self.dueno).get(
            reverse("api:reporte-psicometrico-descargar", args=[reporte.id])
        )
        if respuesta.streaming:
            # Se lee y se suelta aquí: Windows no deja borrar el directorio
            # temporal con el PDF abierto.
            respuesta.contenido = b"".join(respuesta.streaming_content)
            # `close()` cerraría también la conexión de la prueba con la base.
            for cerrar in respuesta._resource_closers:
                cerrar()
        return respuesta

    # -- lo que dice el listado -------------------------------------------

    def test_sin_pagar_se_ve_que_existe_pero_no_sus_resultados(self):
        reporte = self.reporte()

        datos = self.detalle(reporte).data

        self.assertFalse(datos["desbloqueado"])
        self.assertEqual(datos["precio"], "499.00")
        self.assertTrue(datos["disponible_para_compra"])
        self.assertIsNone(datos["puntaje"])
        self.assertIsNone(datos["nivel"])
        self.assertEqual(datos["escalas"], [])

    def test_pagado_muestra_los_resultados(self):
        reporte = self.reporte()
        self.pagar(reporte)

        datos = self.detalle(reporte).data

        self.assertTrue(datos["desbloqueado"])
        self.assertEqual(datos["puntaje"], 86)
        self.assertEqual(datos["nivel"], "Alto")
        self.assertEqual(len(datos["escalas"]), 1)

    def test_una_orden_sin_cobrar_no_desbloquea(self):
        reporte = self.reporte()
        self.pagar(reporte, estado=EstadoPagoPaypal.APPROVED)

        self.assertFalse(self.detalle(reporte).data["desbloqueado"])

    def test_un_reembolso_vuelve_a_bloquear(self):
        reporte = self.reporte()
        self.pagar(reporte, estado=EstadoPagoPaypal.REFUNDED)

        self.assertFalse(self.detalle(reporte).data["desbloqueado"])

    def test_el_informe_propio_no_se_cobra(self):
        reporte = self.reporte(
            origen=OrigenReportePsicometrico.PROPIA,
            disponible_para_compra=False,
        )

        datos = self.detalle(reporte).data

        self.assertTrue(datos["desbloqueado"])
        self.assertEqual(datos["puntaje"], 86)

    def test_un_informe_sin_precio_esta_abierto(self):
        reporte = self.reporte(precio=Decimal("0.00"))

        self.assertTrue(self.detalle(reporte).data["desbloqueado"])

    def test_el_administrador_lo_ve_todo(self):
        reporte = self.reporte()

        datos = self.detalle(reporte, usuario=self.admin).data

        self.assertTrue(datos["desbloqueado"])
        self.assertEqual(datos["puntaje"], 86)

    def test_el_listado_marca_cada_reporte_por_separado(self):
        pagado = self.reporte(referencia_evaluacion_externa="EVAL-A")
        self.pagar(pagado)
        pendiente = self.reporte(referencia_evaluacion_externa="EVAL-B")

        respuesta = self.cliente_de(self.dueno).get(
            reverse("api:reporte-psicometrico-list")
        )
        por_id = {fila["id"]: fila for fila in respuesta.data}

        self.assertTrue(por_id[str(pagado.id)]["desbloqueado"])
        self.assertFalse(por_id[str(pendiente.id)]["desbloqueado"])

    # -- descarga ---------------------------------------------------------

    def test_descarga_el_pdf_ya_pagado(self):
        reporte = self.reporte()
        self.pagar(reporte)

        respuesta = self.descargar(reporte)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/pdf")
        self.assertEqual(respuesta["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", respuesta["Content-Disposition"])
        self.assertIn("EVAL-ACC-001.pdf", respuesta["Content-Disposition"])
        self.assertEqual(respuesta.contenido, PDF)

    def test_no_descarga_lo_que_no_ha_pagado(self):
        respuesta = self.descargar(self.reporte())

        self.assertEqual(respuesta.status_code, 403)

    def test_descarga_el_informe_propio(self):
        reporte = self.reporte(origen=OrigenReportePsicometrico.PROPIA)

        self.assertEqual(self.descargar(reporte).status_code, 200)

    def test_nadie_descarga_el_reporte_de_otro(self):
        reporte = self.reporte(origen=OrigenReportePsicometrico.PROPIA)
        otro = next(
            usuario
            for usuario in Usuario.objects.exclude(pk=self.dueno.pk)
            if PERMISO_ADMIN_REPORTES not in permisos_de(usuario)
        )

        self.assertEqual(self.descargar(reporte, usuario=otro).status_code, 404)

    def test_descarga_requiere_sesion(self):
        reporte = self.reporte()

        respuesta = APIClient().get(
            reverse("api:reporte-psicometrico-descargar", args=[reporte.id])
        )

        self.assertEqual(respuesta.status_code, 401)

    # -- no se cobra dos veces --------------------------------------------

    @patch("core.services.pagos.paypal_client.crear_orden")
    def test_no_abre_orden_de_un_reporte_ya_pagado(self, crear_orden):
        reporte = self.reporte()
        self.pagar(reporte)

        respuesta = self.cliente_de(self.dueno).post(
            reverse("api:ordenes-paypal"),
            {"reporte_id": str(reporte.id)},
            format="json",
        )

        self.assertGreaterEqual(respuesta.status_code, 400)
        crear_orden.assert_not_called()
