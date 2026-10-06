"""Auditoría aislada: ejecutar explícitamente con scripts/qa_audit_run.py.

No cambia la aplicación, no consulta S3 real ni envía correo.
"""
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AdjuntoPublicacion, Curso, EstadoAdjunto, TipoAdjunto
from core.services import portadas_curso
from tests import test_portadas_curso as fixtures
from tests import test_publicaciones as muro


class SeniorMediaChecks(TestCase):
    def setUp(self):
        cache.clear()
        self.instructor = fixtures.crear_usuario('instructor')
        self.course = Curso.objects.create(titulo='QA senior portada', instructor=self.instructor, activo=True)
        self.client = APIClient()
        self.client.force_authenticate(self.instructor)
        self.url = f'/api/cursos/{self.course.slug}/portada/confirmar/'

    def test_QAS01_png_header_without_image_must_be_rejected_as_cover(self):
        # Un PNG de ocho bytes contiene solo la firma: no tiene IHDR,
        # dimensiones, pixels ni IEND. No puede ser una imagen completa.
        fake = fixtures.s3_falso(tamano=8, content_type='image/png', inicio=b'\x89PNG\r\n\x1a\n')
        key = portadas_curso.clave_nueva(self.course, 'image/png')
        with patch('core.services.medios_publicacion.s3_video_storage', return_value=(fake, 'qa')):
            response = self.client.post(self.url, {'clave': key}, format='json')
        print('QAS01 cover with PNG header only:', response.status_code)
        self.assertEqual(response.status_code, 400)

    def test_QAS02_png_header_without_image_must_be_rejected_as_attachment(self):
        owner = muro.crear_usuario('aspirante')
        self.client.force_authenticate(owner)
        attachment = AdjuntoPublicacion.objects.create(
            autor=owner, tipo=TipoAdjunto.IMAGEN, content_type='image/png',
            estado=EstadoAdjunto.PENDIENTE, s3_key='qa/png-header-only',
        )
        fake = fixtures.s3_falso(tamano=8, content_type='image/png', inicio=b'\x89PNG\r\n\x1a\n')
        with patch('core.services.medios_publicacion.s3_video_storage', return_value=(fake, 'qa')):
            response = self.client.post(f'/api/adjuntos-publicacion/{attachment.pk}/confirmar/')
        attachment.refresh_from_db()
        print('QAS02 invalid attachment:', response.status_code, attachment.estado)
        self.assertEqual(response.status_code, 400)

    def test_QAS03_cover_missing_without_list_bucket_must_report_pending_upload(self):
        fake = fixtures.s3_falso()
        fake.head_object.side_effect = ClientError({'Error': {'Code': '403'}}, 'HeadObject')
        key = portadas_curso.clave_nueva(self.course, 'image/jpeg')
        with patch('core.services.medios_publicacion.s3_video_storage', return_value=(fake, 'qa')):
            response = self.client.post(self.url, {'clave': key}, format='json')
        print('QAS03 missing key with least-privilege S3:', response.status_code, response.data)
        self.assertEqual(response.status_code, 409)

    def test_QAS04_delayed_cleanup_must_not_delete_reactivated_cover(self):
        first = portadas_curso.clave_nueva(self.course, 'image/jpeg')
        second = portadas_curso.clave_nueva(self.course, 'image/jpeg')
        fake = fixtures.s3_falso()
        with patch('core.services.medios_publicacion.s3_video_storage', return_value=(fake, 'qa')):
            self.assertEqual(self.client.post(self.url, {'clave': first}).status_code, 200)
            # B confirma y libera el bloqueo. Se retrasa su callback de borrado;
            # otra solicitud vuelve a confirmar A mientras el archivo aun existe.
            with self.captureOnCommitCallbacks(execute=False) as cleanup:
                self.assertEqual(self.client.post(self.url, {'clave': second}).status_code, 200)
            self.assertEqual(self.client.post(self.url, {'clave': first}).status_code, 200)
            for callback in cleanup:
                callback()
        self.course.refresh_from_db()
        deleted = [call.kwargs['Key'] for call in fake.delete_object.call_args_list]
        print('QAS04 current cover deleted:', self.course.imagen_clave in deleted)
        self.assertNotIn(self.course.imagen_clave, deleted)

    def test_QAS05_catalog_measurement(self):
        self.client.force_authenticate(None)
        with CaptureQueriesContext(connection) as small:
            response = self.client.get('/api/cursos/')
        self.assertEqual(response.status_code, 200)
        for index in range(12):
            Curso.objects.create(titulo=f'QA catalog {index}', instructor=self.instructor, activo=True)
        with CaptureQueriesContext(connection) as large:
            response = self.client.get('/api/cursos/')
        print('QAS05 catalog rows and queries:', len(response.data), len(small), len(large))
        self.assertEqual(response.status_code, 200)

    def test_QAS06_client_forwarded_header_must_not_bypass_login_limit(self):
        self.client.force_authenticate(None)
        statuses = []
        with patch('core.api.serializers.verify_password', return_value=False):
            control = [self.client.post('/api/auth/login/', {
                'email': 'qa-limit@example.test', 'password': 'Wrong-password-983!',
            }, format='json', REMOTE_ADDR='127.0.0.1').status_code for _ in range(6)]
            self.assertEqual(control[-1], 429)
            cache.clear()
            for index in range(6):
                response = self.client.post('/api/auth/login/', {
                    'email': 'qa-limit@example.test', 'password': 'Wrong-password-983!',
                }, format='json', HTTP_X_FORWARDED_FOR=f'198.51.100.{index + 1}',
                   REMOTE_ADDR='127.0.0.1')
                statuses.append(response.status_code)
        print('QAS06 control and spoofed headers:', control, statuses)
        self.assertEqual(statuses[-1], 429)
