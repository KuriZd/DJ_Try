import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Comentario, Publicacion, Reaccion
from tests.test_publicaciones import crear_usuario


class InteraccionesBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.autor = crear_usuario('aspirante')
        cls.lector = crear_usuario('aspirante')
        cls.otro = crear_usuario('aspirante')
        cls.admin = crear_usuario('administrador')
        cls.publicacion = Publicacion.objects.create(autor=cls.autor, cuerpo='Hola, muro')

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.url = f'/api/publicaciones/{self.publicacion.pk}/'


class ReaccionesAPITests(InteraccionesBase):
    def test_reaccionar_pide_sesion(self):
        self.assertEqual(self.client.post(self.url + 'reaccion/').status_code, 401)
        self.assertEqual(self.client.get('/api/publicaciones/mis-reacciones/?ids=').status_code, 401)

    def test_poner_es_idempotente_y_quitar_tambien(self):
        self.client.force_authenticate(self.lector)
        for _ in range(2):
            respuesta = self.client.post(self.url + 'reaccion/')
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.data, {'reaccionaste': True, 'total_reacciones': 1})
        for _ in range(2):
            respuesta = self.client.delete(self.url + 'reaccion/')
            self.assertEqual(respuesta.data, {'reaccionaste': False, 'total_reacciones': 0})

    def test_cualquiera_reacciona_tambien_lo_ajeno(self):
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.post(self.url + 'reaccion/').status_code, 200)
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.post(self.url + 'reaccion/').data['total_reacciones'], 2)

    def test_el_muro_trae_los_totales(self):
        Reaccion.objects.create(publicacion=self.publicacion, usuario=self.lector)
        Reaccion.objects.create(publicacion=self.publicacion, usuario=self.otro)
        Comentario.objects.create(publicacion=self.publicacion, autor=self.lector, cuerpo='a')
        primera = self.client.get('/api/publicaciones/').data['results'][0]
        self.assertEqual((primera['total_reacciones'], primera['total_comentarios']), (2, 1))

    def test_editar_conserva_los_totales_en_la_respuesta(self):
        Reaccion.objects.create(publicacion=self.publicacion, usuario=self.lector)
        self.client.force_authenticate(self.autor)
        respuesta = self.client.patch(self.url, {'cuerpo': 'Otra'}, format='json')
        self.assertEqual(respuesta.data['total_reacciones'], 1)

    def test_mis_reacciones_solo_devuelve_las_propias(self):
        otra = Publicacion.objects.create(autor=self.autor, cuerpo='otra')
        Reaccion.objects.create(publicacion=self.publicacion, usuario=self.lector)
        Reaccion.objects.create(publicacion=otra, usuario=self.otro)
        self.client.force_authenticate(self.lector)
        respuesta = self.client.get(
            f'/api/publicaciones/mis-reacciones/?ids={self.publicacion.pk},{otra.pk}',
        )
        self.assertEqual(respuesta.data, {'reaccionadas': [str(self.publicacion.pk)]})

    def test_mis_reacciones_valida_los_ids(self):
        self.client.force_authenticate(self.lector)
        base = '/api/publicaciones/mis-reacciones/?ids='
        self.assertEqual(self.client.get(base + 'no-es-uuid').status_code, 400)
        demasiados = ','.join(str(uuid.uuid4()) for _ in range(51))
        self.assertEqual(self.client.get(base + demasiados).status_code, 400)
        self.assertEqual(self.client.get(base).data, {'reaccionadas': []})

    def test_eliminar_la_publicacion_se_lleva_sus_reacciones(self):
        Reaccion.objects.create(publicacion=self.publicacion, usuario=self.lector)
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertFalse(Reaccion.objects.exists())


class ComentariosAPITests(InteraccionesBase):
    def comentar(self, user, cuerpo='Buen punto'):
        self.client.force_authenticate(user)
        return self.client.post(self.url + 'comentarios/', {'cuerpo': cuerpo}, format='json')

    def test_leer_es_publico_y_comentar_pide_sesion(self):
        self.assertEqual(self.client.get(self.url + 'comentarios/').status_code, 200)
        self.assertEqual(
            self.client.post(self.url + 'comentarios/', {'cuerpo': 'x'}, format='json').status_code, 401,
        )

    def test_cualquier_cuenta_comenta_como_si_misma(self):
        respuesta = self.comentar(self.lector, '  Buen punto\r\nGracias  ')
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data['cuerpo'], 'Buen punto\nGracias')
        self.assertEqual(respuesta.data['autor']['id'], str(self.lector.pk))
        self.assertEqual(respuesta.data['publicacion'], self.publicacion.pk)

    def test_valida_el_cuerpo(self):
        self.assertEqual(self.comentar(self.lector, '  ').status_code, 400)
        self.assertEqual(self.comentar(self.lector, 'a' * (Comentario.LIMITE_CUERPO + 1)).status_code, 400)

    def test_comentar_algo_que_no_existe_es_404(self):
        self.client.force_authenticate(self.lector)
        url = f'/api/publicaciones/{uuid.uuid4()}/comentarios/'
        self.assertEqual(self.client.post(url, {'cuerpo': 'x'}, format='json').status_code, 404)

    def test_se_listan_del_mas_antiguo_y_paginan_por_cursor(self):
        base = timezone.now()
        for i in range(22):
            nuevo = Comentario.objects.create(publicacion=self.publicacion, autor=self.lector, cuerpo=f'c{i}')
            Comentario.objects.filter(pk=nuevo.pk).update(fecha_publicacion=base + timedelta(minutes=i))
        otra = Publicacion.objects.create(autor=self.autor, cuerpo='otra')
        Comentario.objects.create(publicacion=otra, autor=self.lector, cuerpo='ajeno')

        primera = self.client.get(self.url + 'comentarios/').data
        self.assertEqual(len(primera['results']), 20)
        self.assertEqual(primera['results'][0]['cuerpo'], 'c0')
        segunda = self.client.get(primera['next']).data
        self.assertEqual([c['cuerpo'] for c in segunda['results']], ['c20', 'c21'])

    def test_solo_quien_comento_edita(self):
        comentario = self.comentar(self.lector).data
        url = f"/api/comentarios/{comentario['id']}/"
        for user in (self.autor, self.admin, self.otro):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.patch(url, {'cuerpo': 'no'}, format='json').status_code, 403)
        self.client.force_authenticate(self.lector)
        respuesta = self.client.patch(url, {'cuerpo': 'Corregido'}, format='json')
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNotNone(respuesta.data['fecha_edicion'])

    def test_eliminan_quien_comento_el_autor_de_la_publicacion_y_quien_modera(self):
        for quien_borra in (self.lector, self.autor, self.admin):
            comentario = self.comentar(self.lector).data
            url = f"/api/comentarios/{comentario['id']}/"
            self.client.force_authenticate(self.otro)
            self.assertEqual(self.client.delete(url).status_code, 403)
            self.client.force_authenticate(quien_borra)
            self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(Comentario.objects.exists())

    def test_limite_de_comentarios_por_cuenta(self):
        codigos = [self.comentar(self.lector, f'c{i}').status_code for i in range(31)]
        self.assertEqual(codigos[:30], [201] * 30)
        self.assertEqual(codigos[30], 429)

    def test_eliminar_la_publicacion_se_lleva_sus_comentarios(self):
        self.comentar(self.lector)
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertFalse(Comentario.objects.exists())
