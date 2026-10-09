"""Azure media contracts, without credentials or external connections."""
import base64
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from azure.core.exceptions import HttpResponseError, ServiceRequestError
from botocore.exceptions import BotoCoreError, ClientError
from django.test import SimpleTestCase, override_settings

from core.services import medios_publicacion, videos
from core.services.azure_media import AzureMediaClient


@override_settings(
    MEDIA_STORAGE_PROVIDER='azure', AZURE_ACCOUNT_NAME='testaccount',
    AZURE_ACCOUNT_KEY=base64.b64encode(b'fake-key-for-offline-tests-only').decode(),
    AZURE_CONTAINER='private-media',
)
class AzureMediaTests(SimpleTestCase):
    def test_upload_sas_is_create_only_and_headers_support_browser_put(self):
        for sign, args in ((videos.upload_url, ('videos/fake.mp4',)),
                           (medios_publicacion.url_de_subida, ('publicaciones/fake.png', 'image/png'))):
            url, headers = sign(*args)
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            self.assertEqual(parts.hostname, 'testaccount.blob.core.windows.net')
            self.assertEqual(query['sp'], ['c'])
            self.assertEqual(query['spr'], ['https'])
            self.assertEqual(headers['x-ms-blob-type'], 'BlockBlob')
            self.assertEqual(headers['If-None-Match'], '*')

    def test_read_sas_cannot_write_and_preserves_content_type(self):
        url = medios_publicacion.url_de_lectura('publicaciones/fake.png', 'image/png')
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['sp'], ['r'])
        self.assertEqual(query['rsct'], ['image/png'])
        self.assertEqual(query['rscd'], ['inline'])
        query = parse_qs(urlsplit(videos.playback_url('videos/fake.mp4')).query)
        self.assertEqual(query['rscc'], ['private, no-store'])

    @patch('core.services.azure_media.BlobServiceClient')
    def test_head_and_bounded_download(self, service):
        blob = service.return_value.get_blob_client.return_value
        blob.get_blob_properties.return_value = MagicMock(
            size=42, content_settings=MagicMock(content_type='image/png'))
        blob.download_blob.return_value.readall.return_value = b'1234'
        client = AzureMediaClient()
        self.assertEqual(client.head_object(Bucket='private-media', Key='fake.png'),
                         {'ContentLength': 42, 'ContentType': 'image/png'})
        body = client.get_object(Bucket='private-media', Key='fake.png', Range='bytes=0-3')['Body']
        self.assertEqual(body.read(), b'1234')
        blob.download_blob.assert_called_once_with(offset=0, length=4)
        body.close()

    @patch('core.services.azure_media.BlobServiceClient')
    def test_missing_blob_and_permission_denied_are_distinct(self, service):
        blob = service.return_value.get_blob_client.return_value
        client = AzureMediaClient()
        for status in (404, 403):
            error = HttpResponseError(message='private request URL')
            error.status_code = status
            blob.get_blob_properties.side_effect = error
            with self.assertRaises(ClientError) as caught:
                client.head_object(Bucket='private-media', Key='fake.png')
            self.assertEqual(medios_publicacion.aun_no_existe(caught.exception), status == 404)
            self.assertNotIn('private request URL', str(caught.exception))

    @patch('core.services.azure_media.BlobServiceClient')
    def test_network_failure_is_handled_by_existing_api_error_contract(self, service):
        service.return_value.get_blob_client.return_value.get_blob_properties.side_effect = ServiceRequestError('private URL')
        with self.assertRaises(BotoCoreError):
            AzureMediaClient().head_object(Bucket='private-media', Key='fake.png')

    @patch('core.services.azure_media.BlobServiceClient')
    def test_delete_is_idempotent_but_does_not_hide_permission_errors(self, service):
        blob = service.return_value.get_blob_client.return_value
        client = AzureMediaClient()
        error = HttpResponseError(message='missing')
        error.status_code = 404
        blob.delete_blob.side_effect = error
        client.delete_object(Bucket='private-media', Key='fake.png')
        error.status_code = 403
        with self.assertRaises(ClientError):
            client.delete_object(Bucket='private-media', Key='fake.png')
