"""Correcciones de la auditoria del muro (2026-10-01).

1. Escribir pide el correo verificado.
2. Los totales se cuentan con subconsultas, sin cruzar reacciones y comentarios.
3. Lo de una cuenta bloqueada o dada de baja no se ve.
"""

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Comentario, Publicacion, Reaccion
from tests.test_publicaciones import crear_usuario


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.autor = crear_usuario('aspirante')
        cls.sin_verificar = crear_usuario('aspirante', verificado=False)
        cls.publicacion = Publicacion.objects.create(autor=cls.autor, cuerpo='Hola')

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.url = f'/api/publicaciones/{self.publicacion.pk}/'


class CorreoVerificadoTests(Base):
    def assertSinVerificar(self, respuesta):
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.data['detail'].code, 'correo_no_verificado')

    def test_sin_correo_verificado_no_se_publica_comenta_ni_reacciona(self):
        self.client.force_authenticate(self.sin_verificar)
        self.assertSinVerificar(self.client.post('/api/publicaciones/', {'cuerpo': 'x'}, format='json'))
        self.assertSinVerificar(self.client.post(self.url + 'comentarios/', {'cuerpo': 'x'}, format='json'))
        self.assertSinVerificar(self.client.post(self.url + 'reaccion/'))
        self.assertFalse(Publicacion.objects.filter(autor=self.sin_verificar).exists())

    def test_sin_correo_verificado_si_lee_y_consulta_sus_reacciones(self):
        self.client.force_authenticate(self.sin_verificar)
        self.assertEqual(self.client.get('/api/publicaciones/').status_code, 200)
        self.assertEqual(self.client.get(self.url + 'comentarios/').status_code, 200)
        respuesta = self.client.get(f'/api/publicaciones/mis-reacciones/?ids={self.publicacion.pk}')
        self.assertEqual(respuesta.status_code, 200)

    def test_quien_perdio_la_verificacion_puede_retirar_lo_suyo_pero_no_editarlo(self):
        # Pasa al cambiar el correo: la direccion nueva aun no esta verificada.
        propia = Publicacion.objects.create(autor=self.sin_verificar, cuerpo='mia')
        comentario = Comentario.objects.create(publicacion=self.publicacion, autor=self.sin_verificar, cuerpo='c')
        Reaccion.objects.create(publicacion=self.publicacion, usuario=self.sin_verificar)
        self.client.force_authenticate(self.sin_verificar)

        self.assertSinVerificar(self.client.patch(
            f'/api/publicaciones/{propia.pk}/', {'cuerpo': 'spam'}, format='json'))
        self.assertSinVerificar(self.client.patch(
            f'/api/comentarios/{comentario.pk}/', {'cuerpo': 'spam'}, format='json'))
        self.assertEqual(self.client.delete(self.url + 'reaccion/').status_code, 200)
        self.assertEqual(self.client.delete(f'/api/comentarios/{comentario.pk}/').status_code, 204)
        self.assertEqual(self.client.delete(f'/api/publicaciones/{propia.pk}/').status_code, 204)

    def test_sin_sesion_sigue_siendo_401(self):
        self.assertEqual(self.client.post('/api/publicaciones/', {'cuerpo': 'x'}, format='json').status_code, 401)


class TotalesTests(Base):
    def test_cuenta_bien_con_reacciones_y_comentarios_a_la_vez(self):
        lectores = [crear_usuario('aspirante') for _ in range(3)]
        for lector in lectores:
            Reaccion.objects.create(publicacion=self.publicacion, usuario=lector)
        for i in range(4):
            Comentario.objects.create(publicacion=self.publicacion, autor=lectores[0], cuerpo=f'c{i}')

        primera = self.client.get('/api/publicaciones/').data['results'][0]
        self.assertEqual((primera['total_reacciones'], primera['total_comentarios']), (3, 4))

    def test_el_muro_no_une_reacciones_con_comentarios(self):
        with CaptureQueriesContext(connection) as consultas:
            self.client.get('/api/publicaciones/')
        sql = ' '.join(c['sql'] for c in consultas.captured_queries if 'core_publicacion' in c['sql'])
        self.assertNotIn('GROUP BY "core_publicacion"', sql)
        self.assertNotRegex(sql, r'JOIN "core_reaccion".*JOIN "core_comentario"')

    def test_la_cantidad_de_consultas_no_crece_con_las_publicaciones(self):
        def consultas_del_muro():
            with CaptureQueriesContext(connection) as consultas:
                self.client.get('/api/publicaciones/')
            return len(consultas.captured_queries)

        antes = consultas_del_muro()
        for i in range(5):
            Publicacion.objects.create(autor=self.autor, cuerpo=f'p{i}')
        self.assertEqual(consultas_del_muro(), antes)


class CuentasInactivasTests(Base):
    def bloquear(self, usuario, **cambios):
        for campo, valor in cambios.items():
            setattr(usuario, campo, valor)
        usuario.save(update_fields=list(cambios))

    def test_lo_de_una_cuenta_bloqueada_desaparece_del_muro(self):
        spammer = crear_usuario('aspirante')
        spam = Publicacion.objects.create(autor=spammer, cuerpo='spam')
        Comentario.objects.create(publicacion=self.publicacion, autor=spammer, cuerpo='spam')
        Reaccion.objects.create(publicacion=self.publicacion, usuario=spammer)
        self.bloquear(spammer, estado='bloqueado')

        muro = self.client.get('/api/publicaciones/').data['results']
        self.assertEqual([p['cuerpo'] for p in muro], ['Hola'])
        self.assertEqual((muro[0]['total_reacciones'], muro[0]['total_comentarios']), (0, 0))
        self.assertEqual(self.client.get(f'/api/publicaciones/{spam.pk}/').status_code, 404)
        self.assertEqual(self.client.get(self.url + 'comentarios/').data['results'], [])

    def test_y_tambien_la_de_una_cuenta_dada_de_baja(self):
        baja = crear_usuario('aspirante')
        Publicacion.objects.create(autor=baja, cuerpo='adios')
        self.bloquear(baja, eliminado_en=timezone.now())

        muro = self.client.get('/api/publicaciones/').data['results']
        self.assertEqual([p['cuerpo'] for p in muro], ['Hola'])

    def test_vuelve_si_la_cuenta_se_reactiva(self):
        cuenta = crear_usuario('aspirante')
        Publicacion.objects.create(autor=cuenta, cuerpo='de vuelta')
        self.bloquear(cuenta, estado='bloqueado')
        self.bloquear(cuenta, estado='activo')

        cuerpos = [p['cuerpo'] for p in self.client.get('/api/publicaciones/').data['results']]
        self.assertIn('de vuelta', cuerpos)

    def test_el_total_de_una_reaccion_nueva_no_cuenta_cuentas_bloqueadas(self):
        bloqueada = crear_usuario('aspirante')
        Reaccion.objects.create(publicacion=self.publicacion, usuario=bloqueada)
        self.bloquear(bloqueada, estado='bloqueado')
        self.client.force_authenticate(self.autor)

        self.assertEqual(self.client.post(self.url + 'reaccion/').data['total_reacciones'], 1)
