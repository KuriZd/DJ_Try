import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIClient

from core.models import Usuario, Video, VideoRendition
from core.services import videos


class VideoAPITests(TestCase):
    @patch('core.services.videos.upload_url', return_value=('https://signed.example/put', {}))
    def test_create_standard_route(self, sign):
        response = self.client.post('/api/videos/', {
            'filename': 'tema.mp4', 'content_type': 'video/mp4',
            'titulo': 'Tema uno', 'descripcion': 'Presentacion',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        video = Video.objects.get(pk=response.data['video']['id'])
        self.assertEqual(video.owner, self.owner)
        self.assertEqual(video.titulo, 'Tema uno')
        self.assertEqual(video.descripcion, 'Presentacion')
        self.assertEqual(video.status, 'pending')
        self.assertEqual(response.data['method'], 'PUT')

    def test_update_metadata_and_protected_fields(self):
        response = self.client.put(self.base, {
            'titulo': 'Introduccion', 'descripcion': 'Contenido',
            'duration_seconds': 90, 'visibility': 'unlisted',
            'status': 'uploaded', 'owner': str(self.other.pk),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.titulo, 'Introduccion')
        self.assertEqual(self.video.duration_seconds, 90)
        self.assertEqual(self.video.visibility, 'unlisted')
        self.assertEqual(self.video.owner, self.owner)
        self.assertEqual(self.video.status, 'pending')
        response = self.client.patch(self.base, {'titulo': 'Actualizado'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['descripcion'], 'Contenido')

    def test_invalid_update(self):
        for data in ({'duration_seconds': -1}, {'visibility': 'public'}, {'titulo': 'x' * 201}):
            self.assertEqual(self.client.patch(self.base, data, format='json').status_code, 400)

    def test_only_owner_can_update_or_delete_even_if_unlisted(self):
        self.video.visibility = 'unlisted'
        self.video.save()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(self.base, {'titulo': 'Ajeno'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.base).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.patch(self.base, {'titulo': 'Anonimo'}, format='json').status_code, 401)
        self.assertEqual(self.client.delete(self.base).status_code, 401)
        self.assertEqual(self.client.post('/api/videos/', {}, format='json').status_code, 401)

    @patch('core.services.videos.playback_url')
    @patch('core.services.videos.head_video')
    def test_soft_delete_hides_all_access_and_retains_storage_reference(self, head, sign):
        self.video.visibility = 'unlisted'
        self.video.save()
        response = self.client.delete(self.base)
        self.assertEqual(response.status_code, 204)
        self.video.refresh_from_db()
        self.assertIsNotNone(self.video.eliminado_en)
        self.assertTrue(VideoRendition.objects.filter(pk=self.rendition.pk).exists())
        self.assertEqual(self.client.get('/api/videos/').data, [])
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(self.client.post(self.base + 'confirm/').status_code, 404)
        self.assertEqual(self.client.patch(self.base, {'titulo': 'Borrado'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.base).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.base + 'playback/').status_code, 404)
        head.assert_not_called()
        sign.assert_not_called()

    def test_cannot_delete_video_in_inactive_lesson(self):
        from core.models import Curso, Leccion, Modulo

        curso = Curso.objects.create(titulo='Curso', instructor=self.owner)
        modulo = Modulo.objects.create(curso=curso, titulo='Contenido', orden=1)
        leccion = Leccion.objects.create(modulo=modulo, video=self.video, titulo='Tema', orden=1, activo=False)
        self.assertEqual(self.client.delete(self.base).status_code, 409)
        self.video.refresh_from_db()
        self.assertIsNone(self.video.eliminado_en)
        leccion.delete()
        self.assertEqual(self.client.delete(self.base).status_code, 204)

    def test_migration_schema_verification(self):
        from io import StringIO
        from django.core.management import call_command

        output = StringIO()
        call_command('verificar_video_schema', stdout=output)
        self.assertIn('OK: core_video,', output.getvalue())
        self.assertIn('OK: core_videorendition,', output.getvalue())

    def setUp(self):
        self.owner = Usuario.objects.first()
        self.other = Usuario.objects.exclude(pk=self.owner.pk).first()
        self.assertIsNotNone(self.other)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.video = Video.objects.create(owner=self.owner)
        self.rendition = VideoRendition.objects.create(video=self.video, s3_key=f'videos/{uuid.uuid4()}.mp4')
        self.base = f'/api/videos/{self.video.pk}/'

    @patch('core.services.videos.upload_url', return_value=('https://signed.example/put', {'Content-Type': 'video/mp4', 'If-None-Match': '*'}))
    def test_upload(self, sign):
        response = self.client.post('/api/videos/upload/', {'filename': '../../private.mp4', 'content_type': 'video/mp4'}, format='json')
        self.assertEqual(response.status_code, 201)
        video = Video.objects.get(pk=response.data['video']['id'])
        self.assertEqual(video.owner, self.owner)
        self.assertEqual(video.status, 'pending')
        self.assertNotIn('private', sign.call_args.args[0])
        self.assertNotIn('s3_key', str(response.data))
        self.assertEqual(response.data['expires_in'], 600)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    @patch('core.services.videos.upload_url')
    def test_invalid_type_and_anonymous_upload(self, sign):
        response = self.client.post('/api/videos/upload/', {'filename': 'x.mp4', 'content_type': 'text/html'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.post('/api/videos/upload/', {}, format='json').status_code, (401, 403))
        sign.assert_not_called()

    @patch('core.services.videos.playback_url', return_value='https://signed.example/get')
    def test_playback_permissions(self, sign):
        self.video.status = self.rendition.status = 'uploaded'
        self.video.save()
        self.rendition.save()
        self.assertEqual(self.client.get(self.base + 'playback/').status_code, 200)
        sign.reset_mock()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.base + 'playback/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.base + 'playback/').status_code, 404)
        sign.assert_not_called()
        self.video.visibility = 'unlisted'
        self.video.save()
        self.assertEqual(self.client.get(self.base + 'playback/').status_code, 200)

    @patch('core.services.videos.head_video', return_value={'ContentLength': 512, 'ContentType': 'video/mp4'})
    def test_confirmation_is_owner_only_and_idempotent(self, head):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.base + 'confirm/').status_code, 404)
        head.assert_not_called()
        self.client.force_authenticate(self.owner)
        for _ in range(2):
            self.assertEqual(self.client.post(self.base + 'confirm/').status_code, 200)
        head.assert_called_once_with(self.rendition.s3_key)
        self.rendition.refresh_from_db()
        self.assertEqual(self.rendition.file_size, 512)
        self.assertEqual(self.rendition.status, 'uploaded')

    @patch('core.services.videos.head_video')
    def test_missing_upload_can_be_retried(self, head):
        head.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        self.assertEqual(self.client.post(self.base + 'confirm/').status_code, 409)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, 'pending')

    @override_settings(VIDEO_MAX_BYTES=10)
    @patch('core.services.videos.head_video', return_value={'ContentLength': 11, 'ContentType': 'video/mp4'})
    def test_oversized_upload_cannot_play(self, head):
        self.assertEqual(self.client.post(self.base + 'confirm/').status_code, 400)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, 'failed')
        self.assertEqual(self.client.get(self.base + 'playback/').status_code, 409)

    @patch('core.services.videos.upload_url')
    def test_signing_failure_rolls_back(self, sign):
        from botocore.exceptions import NoCredentialsError
        sign.side_effect = NoCredentialsError()
        count = Video.objects.count()
        response = self.client.post('/api/videos/upload/', {'filename': 'x.mp4', 'content_type': 'video/mp4'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Video.objects.count(), count)

    def test_list_only_includes_owner_and_detail_hides_key(self):
        Video.objects.create(owner=self.other, visibility='unlisted')
        response = self.client.get('/api/videos/')
        self.assertEqual([str(row['id']) for row in response.data], [str(self.video.pk)])
        self.assertNotIn('s3_key', str(self.client.get(self.base).data))


class VideoSigningTests(SimpleTestCase):
    def test_storage_signs_regional_host_without_redirect(self):
        from django.conf import settings
        from storages.backends.s3 import S3Storage

        options = dict(settings.STORAGES['videos']['OPTIONS'])
        options.update(bucket_name='regional-video-test', access_key='testing',
                       secret_key='testing', security_token=None)
        storage = S3Storage(**options)
        client = storage.connection.meta.client
        expected = f'regional-video-test.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com'
        with patch('core.services.videos.s3_video_storage', return_value=(client, storage.bucket_name)):
            self.assertEqual(urlparse(videos.upload_url('videos/test.mp4')[0]).hostname, expected)
            self.assertEqual(urlparse(videos.playback_url('videos/test.mp4')).hostname, expected)

    def test_real_sigv4_signatures_without_network(self):
        client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing',
                              aws_secret_access_key='testing', config=Config(signature_version='s3v4'))
        with patch('core.services.videos.s3_video_storage', return_value=(client, 'private-test')):
            url, headers = videos.upload_url('videos/test.mp4')
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query['X-Amz-Expires'], ['600'])
            self.assertIn('if-none-match', query['X-Amz-SignedHeaders'][0])
            self.assertIn('content-type', query['X-Amz-SignedHeaders'][0])
            self.assertEqual(headers['If-None-Match'], '*')
            query = parse_qs(urlparse(videos.playback_url('videos/test.mp4')).query)
            self.assertEqual(query['X-Amz-Expires'], ['3600'])
            self.assertNotIn('range', query['X-Amz-SignedHeaders'][0])
            self.assertEqual(query['response-content-type'], ['video/mp4'])
