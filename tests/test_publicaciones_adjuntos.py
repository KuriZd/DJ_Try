"""Fotos y videos del muro. S3 se simula: estas pruebas no tocan el bucket."""

import io
from datetime import timedelta
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AdjuntoPublicacion, EstadoAdjunto, Publicacion, Reporte, TipoAdjunto
from tests.test_publicaciones import crear_usuario

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 12
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 4
HTML = b'<html><script>x'


def s3_falso(tamano=1000, content_type='image/jpeg', inicio=JPEG):
    cliente = MagicMock()
    cliente.generate_presigned_url.side_effect = lambda op, Params, **kw: f"https://s3.test/{Params['Key']}?{op}"
    cliente.head_object.return_value = {'ContentLength': tamano, 'ContentType': content_type}
    cliente.get_object.return_value = {'Body': io.BytesIO(inicio)}
    return cliente


class Base(TestCase):
    URL = '/api/adjuntos-publicacion/'

    @classmethod
    def setUpTestData(cls):
        cls.autor = crear_usuario('aspirante')
        cls.otro = crear_usuario('aspirante')

    def setUp(self):
        cache.clear()
        self.cliente_s3 = s3_falso()
        parche = patch('core.services.medios_publicacion.s3_video_storage',
                       side_effect=lambda: (self.cliente_s3, 'bucket-pruebas'))
        parche.start()
        self.addCleanup(parche.stop)
        self.client = APIClient()
        self.client.force_authenticate(self.autor)

    def adjunto(self, autor=None, tipo=TipoAdjunto.IMAGEN, estado=EstadoAdjunto.LISTO, **extra):
        autor = autor or self.autor
        content_type = 'video/mp4' if tipo == TipoAdjunto.VIDEO else 'image/jpeg'
        adjunto = AdjuntoPublicacion(autor=autor, tipo=tipo, content_type=content_type, estado=estado, **extra)
        adjunto.s3_key = f'publicaciones/{autor.pk}/{adjunto.id}'
        adjunto.save()
        return adjunto

    def publicar(self, cuerpo='', adjuntos=()):
        return self.client.post('/api/publicaciones/', {
            'cuerpo': cuerpo, 'adjuntos_ids': [str(a.pk) for a in adjuntos],
        }, format='json')


class SubirTests(Base):
    def test_pedir_la_subida_devuelve_url_firmada_con_el_tipo(self):
        respuesta = self.client.post(self.URL, {'content_type': 'image/png', 'tamano': 2048}, format='json')

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        adjunto = AdjuntoPublicacion.objects.get()
        self.assertEqual((adjunto.tipo, adjunto.estado), (TipoAdjunto.IMAGEN, EstadoAdjunto.PENDIENTE))
        self.assertEqual(adjunto.s3_key, f'publicaciones/{self.autor.pk}/{adjunto.pk}.png')
        self.assertEqual(respuesta.data['headers'], {'Content-Type': 'image/png', 'If-None-Match': '*'})
        params = self.cliente_s3.generate_presigned_url.call_args.kwargs['Params']
        self.assertEqual((params['ContentType'], params['IfNoneMatch']), ('image/png', '*'))

    def test_pide_sesion_y_correo_verificado(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.URL, {'content_type': 'image/png', 'tamano': 1}).status_code, 401)
        self.client.force_authenticate(crear_usuario('aspirante', verificado=False))
        self.assertEqual(self.client.post(self.URL, {'content_type': 'image/png', 'tamano': 1}).status_code, 403)

    def test_rechaza_formatos_no_permitidos_y_lo_que_no_cabe(self):
        for datos in ({'content_type': 'image/svg+xml', 'tamano': 10},
                      {'content_type': 'image/gif', 'tamano': 10},
                      {'content_type': 'image/jpeg', 'tamano': 11 * 1024 * 1024},
                      {'content_type': 'video/mp4', 'tamano': 201 * 1024 * 1024}):
            with self.subTest(**datos):
                self.assertEqual(self.client.post(self.URL, datos, format='json').status_code, 400)
        self.assertFalse(AdjuntoPublicacion.objects.exists())

    def test_sin_s3_responde_503_y_no_deja_nada(self):
        with patch('core.services.medios_publicacion.s3_video_storage',
                   side_effect=ImproperlyConfigured('sin bucket')):
            respuesta = self.client.post(self.URL, {'content_type': 'image/jpeg', 'tamano': 10}, format='json')
        self.assertEqual(respuesta.status_code, 503)
        self.assertFalse(AdjuntoPublicacion.objects.exists())


class ConfirmarTests(Base):
    def confirmar(self, adjunto):
        return self.client.post(f'{self.URL}{adjunto.pk}/confirmar/')

    def test_un_jpeg_real_queda_listo(self):
        adjunto = self.adjunto(estado=EstadoAdjunto.PENDIENTE)
        respuesta = self.confirmar(adjunto)
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        adjunto.refresh_from_db()
        self.assertEqual((adjunto.estado, adjunto.tamano), (EstadoAdjunto.LISTO, 1000))
        self.assertTrue(respuesta.data['url'].startswith('https://s3.test/'))

    def test_un_html_disfrazado_de_imagen_se_rechaza_y_se_borra(self):
        self.cliente_s3 = s3_falso(inicio=HTML)
        adjunto = self.adjunto(estado=EstadoAdjunto.PENDIENTE)
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.confirmar(adjunto)
        self.assertEqual(respuesta.status_code, 400)
        adjunto.refresh_from_db()
        self.assertEqual(adjunto.estado, EstadoAdjunto.RECHAZADO)
        self.cliente_s3.delete_object.assert_called_once_with(Bucket='bucket-pruebas', Key=adjunto.s3_key)

    def test_rechaza_tipo_distinto_y_tamano_real_excedido(self):
        for cliente in (s3_falso(content_type='image/png', inicio=PNG),
                        s3_falso(tamano=11 * 1024 * 1024)):
            with self.subTest(cliente=cliente):
                self.cliente_s3 = cliente
                self.assertEqual(self.confirmar(self.adjunto(estado=EstadoAdjunto.PENDIENTE)).status_code, 400)

    def test_un_video_mp4_queda_listo(self):
        self.cliente_s3 = s3_falso(content_type='video/mp4', inicio=MP4, tamano=50 * 1024 * 1024)
        adjunto = self.adjunto(tipo=TipoAdjunto.VIDEO, estado=EstadoAdjunto.PENDIENTE)
        self.assertEqual(self.confirmar(adjunto).status_code, 200)

    def test_si_el_archivo_no_ha_llegado_responde_409(self):
        # Sin s3:ListBucket, S3 contesta 403 y no 404 a lo que no existe.
        for codigo in ('404', '403'):
            with self.subTest(codigo=codigo):
                self.cliente_s3.head_object.side_effect = ClientError({'Error': {'Code': codigo}}, 'HeadObject')
                self.assertEqual(self.confirmar(self.adjunto(estado=EstadoAdjunto.PENDIENTE)).status_code, 409)

    def test_otro_error_de_s3_al_confirmar_es_503(self):
        self.cliente_s3.head_object.side_effect = ClientError({'Error': {'Code': '500'}}, 'HeadObject')
        self.assertEqual(self.confirmar(self.adjunto(estado=EstadoAdjunto.PENDIENTE)).status_code, 503)

    def test_no_se_confirma_lo_ajeno(self):
        self.assertEqual(self.confirmar(self.adjunto(autor=self.otro, estado=EstadoAdjunto.PENDIENTE)).status_code, 404)


class PublicarTests(Base):
    def test_publica_solo_fotos_sin_texto_y_en_orden(self):
        fotos = [self.adjunto(), self.adjunto()]
        respuesta = self.publicar(adjuntos=[fotos[1], fotos[0]])
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual([a['id'] for a in respuesta.data['adjuntos']], [str(fotos[1].pk), str(fotos[0].pk)])

        muro = APIClient().get('/api/publicaciones/').data['results'][0]
        self.assertEqual(len(muro['adjuntos']), 2)
        self.assertTrue(all(a['url'].startswith('https://s3.test/') for a in muro['adjuntos']))

    def test_sin_texto_ni_archivos_no_publica(self):
        self.assertEqual(self.publicar().status_code, 400)

    def test_reglas_de_combinacion(self):
        casos = {
            'foto y video': [self.adjunto(), self.adjunto(tipo=TipoAdjunto.VIDEO)],
            'dos videos': [self.adjunto(tipo=TipoAdjunto.VIDEO), self.adjunto(tipo=TipoAdjunto.VIDEO)],
            'cinco fotos': [self.adjunto() for _ in range(5)],
            'pendiente': [self.adjunto(estado=EstadoAdjunto.PENDIENTE)],
            'ajeno': [self.adjunto(autor=self.otro)],
        }
        for nombre, adjuntos in casos.items():
            with self.subTest(nombre):
                self.assertEqual(self.publicar('x', adjuntos).status_code, 400)
        repetido = self.adjunto()
        respuesta = self.client.post('/api/publicaciones/', {
            'cuerpo': 'x', 'adjuntos_ids': [str(repetido.pk)] * 2}, format='json')
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Publicacion.objects.exists())

    def test_un_archivo_no_se_usa_en_dos_publicaciones(self):
        foto = self.adjunto()
        self.assertEqual(self.publicar('una', [foto]).status_code, 201)
        self.assertEqual(self.publicar('otra', [foto]).status_code, 400)

    def test_editar_no_cambia_archivos_pero_permite_quitar_el_texto(self):
        foto = self.adjunto()
        url = f"/api/publicaciones/{self.publicar('con texto', [foto]).data['id']}/"
        self.assertEqual(self.client.patch(url, {'adjuntos_ids': []}, format='json').status_code, 400)
        respuesta = self.client.patch(url, {'cuerpo': ''}, format='json')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(len(respuesta.data['adjuntos']), 1)

    def test_quitar_el_texto_a_una_sin_archivos_no_se_permite(self):
        url = f"/api/publicaciones/{self.publicar('solo texto').data['id']}/"
        self.assertEqual(self.client.patch(url, {'cuerpo': ''}, format='json').status_code, 400)

    def test_borrar_la_publicacion_borra_sus_archivos_de_s3(self):
        fotos = [self.adjunto(), self.adjunto()]
        url = f"/api/publicaciones/{self.publicar('x', fotos).data['id']}/"
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(AdjuntoPublicacion.objects.exists())
        borradas = {c.kwargs['Key'] for c in self.cliente_s3.delete_object.call_args_list}
        self.assertEqual(borradas, {f.s3_key for f in fotos})

    def test_si_s3_falla_al_leer_la_publicacion_sale_sin_url(self):
        self.publicar('x', [self.adjunto()])
        self.cliente_s3.generate_presigned_url.side_effect = ImproperlyConfigured('sin bucket')
        adjunto = APIClient().get('/api/publicaciones/').data['results'][0]['adjuntos'][0]
        self.assertIsNone(adjunto['url'])

    def test_el_reporte_de_una_publicacion_sin_texto_describe_su_contenido(self):
        publicacion = self.publicar(adjuntos=[self.adjunto()]).data['id']
        self.client.force_authenticate(self.otro)
        self.client.post(f'/api/publicaciones/{publicacion}/reportar/', {'motivo': 'spam'}, format='json')
        self.assertIn('sin texto, con 1 foto', Reporte.objects.get().cuerpo_reportado)


class GestionAdjuntoTests(Base):
    def test_describir_una_foto(self):
        foto = self.adjunto()
        respuesta = self.client.patch(f'{self.URL}{foto.pk}/', {'descripcion': 'Equipo en el foro'}, format='json')
        self.assertEqual(respuesta.status_code, 200)
        foto.refresh_from_db()
        self.assertEqual(foto.descripcion, 'Equipo en el foro')

    def test_descartar_uno_sin_publicar_lo_borra_de_s3(self):
        foto = self.adjunto()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(f'{self.URL}{foto.pk}/').status_code, 204)
        self.cliente_s3.delete_object.assert_called_once_with(Bucket='bucket-pruebas', Key=foto.s3_key)

    def test_no_se_descarta_uno_ya_publicado(self):
        foto = self.adjunto()
        self.publicar('x', [foto])
        self.assertEqual(self.client.delete(f'{self.URL}{foto.pk}/').status_code, 409)

    def test_limpiar_adjuntos_borra_solo_los_viejos_sin_publicar(self):
        viejo = self.adjunto(estado=EstadoAdjunto.PENDIENTE)
        reciente = self.adjunto()
        publicado = self.adjunto()
        self.publicar('x', [publicado])
        AdjuntoPublicacion.objects.filter(pk__in=[viejo.pk, publicado.pk]).update(
            creado_en=timezone.now() - timedelta(hours=30))

        with self.captureOnCommitCallbacks(execute=True):
            call_command('limpiar_adjuntos', stdout=io.StringIO())

        self.assertEqual(set(AdjuntoPublicacion.objects.values_list('pk', flat=True)), {reciente.pk, publicado.pk})
        self.cliente_s3.delete_object.assert_called_once_with(Bucket='bucket-pruebas', Key=viejo.s3_key)


class CompartirConFotoTests(Base):
    def test_la_vista_previa_lleva_la_primera_foto(self):
        publicacion = self.publicar(adjuntos=[self.adjunto()]).data['id']
        html = self.client.get(f'/compartir/publicacion/{publicacion}/').content.decode()
        self.assertIn('<meta property="og:image" content="https://s3.test/', html)
        self.assertIn('summary_large_image', html)
        self.assertIn('Publicación con fotos o video en Actualiza.', html)
