"""Hallazgos de prioridad baja de la auditoria del muro: cache compartida,
limite de reacciones y vista previa al compartir."""

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.compartir import resumen
from core.models import Publicacion
from tests.test_publicaciones import crear_usuario


class CacheCompartidaTests(TestCase):
    def test_los_limites_cuentan_en_la_base_y_no_en_cada_proceso(self):
        self.assertEqual(settings.CACHES['default']['BACKEND'],
                         'django.core.cache.backends.db.DatabaseCache')
        self.assertIn('cache_compartida', connection.introspection.table_names())
        cache.set('prueba', 1)
        self.assertEqual(cache.get('prueba'), 1)


class LimiteReaccionesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(crear_usuario('aspirante'))
        self.url = f"/api/publicaciones/{Publicacion.objects.create(autor=crear_usuario('aspirante'), cuerpo='x').pk}/reaccion/"

    def test_corta_la_rafaga_de_poner_y_quitar(self):
        codigos = [
            (self.client.post if i % 2 == 0 else self.client.delete)(self.url).status_code
            for i in range(121)
        ]
        self.assertEqual(set(codigos[:120]), {200})
        self.assertEqual(codigos[120], 429)


class CompartirTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.autor = crear_usuario('aspirante')
        cls.publicacion = Publicacion.objects.create(
            autor=cls.autor, cuerpo='Hoy cerramos el foro <b>anual</b>.\n\nGracias a todos.',
        )

    def url(self, publicacion=None):
        return reverse('compartir-publicacion', args=[(publicacion or self.publicacion).pk])

    def test_entrega_las_etiquetas_de_vista_previa_sin_sesion(self):
        respuesta = self.client.get(self.url())
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        destino = f'{settings.FRONTEND_BASE_URL}/actualiza/publicacion/{self.publicacion.pk}'
        self.assertIn(f'<meta property="og:url" content="{destino}">', html)
        self.assertIn(f'content="{self.autor.nombre_completo} en Actualiza · AMIS"', html)
        self.assertIn(f'<meta http-equiv="refresh" content="0; url={destino}">', html)
        self.assertEqual(respuesta['Cache-Control'], 'public, max-age=300')

    def test_el_texto_va_escapado_y_en_una_linea(self):
        html = self.client.get(self.url()).content.decode()
        self.assertIn('Hoy cerramos el foro &lt;b&gt;anual&lt;/b&gt;. Gracias a todos.', html)
        self.assertNotIn('<b>anual</b>', html)

    def test_lo_de_una_cuenta_bloqueada_no_se_previsualiza(self):
        bloqueada = crear_usuario('aspirante')
        oculta = Publicacion.objects.create(autor=bloqueada, cuerpo='spam')
        bloqueada.estado = 'bloqueado'
        bloqueada.save(update_fields=['estado'])
        self.assertEqual(self.client.get(self.url(oculta)).status_code, 404)

    def test_resumen_corta_en_palabra(self):
        self.assertEqual(resumen('uno dos tres', limite=7), 'uno dos…')
        self.assertEqual(resumen('  corto\n'), 'corto')
