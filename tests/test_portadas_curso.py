"""Caratula de un curso en S3. S3 se simula: estas pruebas no tocan el bucket."""

import io
import uuid
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Curso, Rol, Usuario, UsuarioRol

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 12
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
HTML = b'<html><script>x'


def s3_falso(tamano=1000, content_type='image/jpeg', inicio=JPEG):
    cliente = MagicMock()
    cliente.generate_presigned_url.side_effect = (
        lambda op, Params, **kw: f"https://s3.test/{Params['Key']}?{op}"
    )
    cliente.head_object.return_value = {'ContentLength': tamano, 'ContentType': content_type}
    # Un flujo nuevo en cada lectura: S3 entrega el archivo cada vez.
    cliente.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(inicio)}
    return cliente


def crear_usuario(rol):
    usuario = Usuario.objects.create(
        id=uuid.uuid4(), nombre_completo=f'Persona {rol}',
        email=f'{uuid.uuid4()}@example.test', password_hash='!', estado='activo',
        creado_en=timezone.now(), actualizado_en=timezone.now(),
    )
    UsuarioRol.objects.create(usuario=usuario, rol=Rol.objects.get(clave=rol), asignado_en=timezone.now())
    return usuario


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = crear_usuario('administrador')
        cls.instructor = crear_usuario('instructor')
        cls.otro_instructor = crear_usuario('instructor')
        cls.alumno = crear_usuario('aspirante')
        cls.curso = Curso.objects.create(titulo='Marco normativo', instructor=cls.instructor, activo=True)

    def setUp(self):
        self.cliente_s3 = s3_falso()
        parche = patch('core.services.medios_publicacion.s3_video_storage',
                       side_effect=lambda: (self.cliente_s3, 'bucket-pruebas'))
        parche.start()
        self.addCleanup(parche.stop)
        self.client = APIClient()
        self.client.force_authenticate(self.instructor)
        self.url = f'/api/cursos/{self.curso.slug}/portada/'

    def pedir_subida(self, content_type='image/jpeg', tamano=1000):
        return self.client.post(self.url, {'content_type': content_type, 'tamano': tamano}, format='json')

    def confirmar(self, clave):
        return self.client.post(self.url + 'confirmar/', {'clave': clave}, format='json')


class PedirSubidaTests(Base):
    def test_devuelve_url_firmada_bajo_la_carpeta_del_curso(self):
        respuesta = self.pedir_subida('image/png', 2048)

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        clave = respuesta.data['clave']
        self.assertRegex(clave, rf'^cursos/{self.curso.pk}/portada-[0-9a-f-]{{36}}\.png$')
        self.assertEqual(respuesta.data['method'], 'PUT')
        self.assertEqual(respuesta.data['headers'], {'Content-Type': 'image/png', 'If-None-Match': '*'})
        # Pedir la subida no cambia el curso: la caratula se fija al confirmar.
        self.curso.refresh_from_db()
        self.assertEqual(self.curso.imagen_clave, '')

    def test_solo_acepta_imagenes(self):
        for content_type in ('video/mp4', 'text/html', 'image/gif'):
            self.assertEqual(self.pedir_subida(content_type).status_code, 400, content_type)

    def test_rechaza_lo_que_pasa_del_tamano(self):
        with self.settings(CURSO_PORTADA_MAX_BYTES=500):
            self.assertEqual(self.pedir_subida(tamano=501).status_code, 400)

    def test_solo_quien_gestiona_el_curso(self):
        self.client.force_authenticate(self.otro_instructor)
        self.assertIn(self.pedir_subida().status_code, (403, 404))
        self.client.force_authenticate(self.alumno)
        self.assertEqual(self.pedir_subida().status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.pedir_subida().status_code, 401)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.pedir_subida().status_code, 201)


class ConfirmarTests(Base):
    def clave_pedida(self, content_type='image/jpeg'):
        return self.pedir_subida(content_type).data['clave']

    def test_fija_la_caratula_y_la_devuelve_firmada(self):
        clave = self.clave_pedida()

        respuesta = self.confirmar(clave)

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.curso.refresh_from_db()
        self.assertEqual((self.curso.imagen_clave, self.curso.imagen_tipo), (clave, 'image/jpeg'))
        self.assertEqual(respuesta.data['imagen'], f'https://s3.test/{clave}?get_object')

    def test_reemplazar_borra_la_anterior(self):
        primera = self.clave_pedida()
        self.confirmar(primera)
        segunda = self.clave_pedida()

        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.confirmar(segunda).status_code, 200)

        self.curso.refresh_from_db()
        self.assertEqual(self.curso.imagen_clave, segunda)
        self.cliente_s3.delete_object.assert_called_once_with(Bucket='bucket-pruebas', Key=primera)

    def test_no_acepta_una_clave_ajena_al_curso(self):
        otro = Curso.objects.create(titulo='Otro', instructor=self.instructor)
        for clave in (
            f'cursos/{otro.pk}/portada-{uuid.uuid4()}.jpg',
            f'publicaciones/{self.instructor.pk}/{uuid.uuid4()}.jpg',
            f'cursos/{self.curso.pk}/../videos/x.jpg',
            f'cursos/{self.curso.pk}/portada-{uuid.uuid4()}.gif',
        ):
            self.assertEqual(self.confirmar(clave).status_code, 400, clave)
        self.cliente_s3.head_object.assert_not_called()

    def test_rechaza_y_borra_lo_que_no_es_la_imagen_declarada(self):
        clave = self.clave_pedida()
        self.cliente_s3.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(HTML)}

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.confirmar(clave)

        self.assertEqual(respuesta.status_code, 400)
        self.curso.refresh_from_db()
        self.assertEqual(self.curso.imagen_clave, '')
        self.cliente_s3.delete_object.assert_called_once_with(Bucket='bucket-pruebas', Key=clave)

    def test_rechaza_lo_que_paso_del_tamano_al_subir(self):
        clave = self.clave_pedida()
        with self.settings(CURSO_PORTADA_MAX_BYTES=500):
            self.cliente_s3.head_object.return_value = {'ContentLength': 900, 'ContentType': 'image/jpeg'}
            self.assertEqual(self.confirmar(clave).status_code, 400)

    def test_avisa_si_el_archivo_todavia_no_llega(self):
        clave = self.clave_pedida()
        self.cliente_s3.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')

        self.assertEqual(self.confirmar(clave).status_code, 409)


class LecturaYBajaTests(Base):
    def fijar(self, clave=None, tipo='image/webp'):
        clave = clave or f'cursos/{self.curso.pk}/portada-{uuid.uuid4()}.webp'
        Curso.objects.filter(pk=self.curso.pk).update(imagen_clave=clave, imagen_tipo=tipo)
        return clave

    def test_el_catalogo_publico_la_entrega_firmada(self):
        clave = self.fijar()
        self.client.force_authenticate(None)

        detalle = self.client.get(f'/api/cursos/{self.curso.slug}/')
        listado = self.client.get('/api/cursos/')

        self.assertEqual(detalle.data['imagen'], f'https://s3.test/{clave}?get_object')
        filas = listado.data['results'] if isinstance(listado.data, dict) else listado.data
        self.assertEqual(filas[0]['imagen'], f'https://s3.test/{clave}?get_object')
        params = self.cliente_s3.generate_presigned_url.call_args.kwargs['Params']
        self.assertEqual(params['ResponseContentType'], 'image/webp')

    def test_sin_caratula_conserva_la_url_que_ya_tenia(self):
        Curso.objects.filter(pk=self.curso.pk).update(imagen='https://cdn.test/vieja.jpg')
        self.assertEqual(
            self.client.get(f'/api/cursos/{self.curso.slug}/').data['imagen'],
            'https://cdn.test/vieja.jpg',
        )

    def test_si_s3_no_responde_el_curso_se_sirve_sin_imagen(self):
        self.fijar()
        self.cliente_s3.generate_presigned_url.side_effect = ClientError({'Error': {'Code': '500'}}, 'Get')

        respuesta = self.client.get(f'/api/cursos/{self.curso.slug}/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.data['imagen'])

    def test_quitar_la_caratula_la_borra_de_s3(self):
        clave = self.fijar()

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.delete(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.curso.refresh_from_db()
        self.assertEqual((self.curso.imagen_clave, self.curso.imagen_tipo, self.curso.imagen), ('', '', ''))
        self.cliente_s3.delete_object.assert_called_once_with(Bucket='bucket-pruebas', Key=clave)

    def test_borrar_el_curso_borra_su_caratula(self):
        clave = self.fijar()

        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(f'/api/cursos/{self.curso.slug}/').status_code, 204)

        self.cliente_s3.delete_object.assert_called_once_with(Bucket='bucket-pruebas', Key=clave)

    def test_imagen_ya_no_se_escribe_como_url(self):
        # La caratula entra sólo por la subida comprobada: una URL suelta no
        # pasa por la revision de tipo ni de tamaño.
        self.client.patch(f'/api/cursos/{self.curso.slug}/', {'imagen': 'https://otro.test/x.jpg'}, format='json')
        self.curso.refresh_from_db()
        self.assertEqual(self.curso.imagen, '')
