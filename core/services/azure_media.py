"""Azure Blob adapter for the existing private media operations.

The small S3-shaped interface keeps stored object keys and existing callers
compatible while media is moved between providers.
"""
import io
from datetime import datetime, timedelta, timezone

from azure.core.exceptions import AzureError, HttpResponseError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class AzureMediaClient:
    def __init__(self):
        if not all((settings.AZURE_ACCOUNT_NAME, settings.AZURE_ACCOUNT_KEY, settings.AZURE_CONTAINER)):
            raise ImproperlyConfigured('Faltan AZURE_ACCOUNT_NAME, AZURE_ACCOUNT_KEY o AZURE_CONTAINER.')
        self.service = BlobServiceClient(
            f'https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net',
            credential=settings.AZURE_ACCOUNT_KEY,
        )

    def _blob(self, bucket, key):
        return self.service.get_blob_client(container=bucket, blob=key)

    def _call(self, operation, function):
        try:
            return function()
        except HttpResponseError as error:
            # Never expose SDK messages, request URLs or SAS tokens to callers.
            code = str(error.status_code or 503)
            raise ClientError({'Error': {'Code': code, 'Message': 'Azure Blob operation failed'}}, operation) from None
        except AzureError:
            raise BotoCoreError() from None

    def generate_presigned_url(self, operation, Params, ExpiresIn, HttpMethod):
        if operation not in ('put_object', 'get_object'):
            raise ValueError('Unsupported media operation')
        now = datetime.now(timezone.utc)
        token = generate_blob_sas(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY,
            container_name=Params['Bucket'], blob_name=Params['Key'],
            # Create-only SAS prevents overwriting, even if a browser drops
            # If-None-Match. Read URLs cannot write, list or delete blobs.
            permission=BlobSasPermissions(create=True) if operation == 'put_object' else BlobSasPermissions(read=True),
            start=now - timedelta(minutes=5), expiry=now + timedelta(seconds=ExpiresIn),
            protocol='https',
            content_type=Params.get('ResponseContentType'),
            content_disposition=Params.get('ResponseContentDisposition'),
            cache_control=Params.get('ResponseCacheControl'),
        )
        return f"{self._blob(Params['Bucket'], Params['Key']).url}?{token}"

    def head_object(self, *, Bucket, Key):
        properties = self._call('HeadObject', lambda: self._blob(Bucket, Key).get_blob_properties())
        return {'ContentLength': properties.size, 'ContentType': properties.content_settings.content_type}

    def get_object(self, *, Bucket, Key, Range):
        start, end = map(int, Range.removeprefix('bytes=').split('-'))
        data = self._call('GetObject', lambda: self._blob(Bucket, Key).download_blob(
            offset=start, length=end - start + 1).readall())
        return {'Body': io.BytesIO(data)}

    def delete_object(self, *, Bucket, Key):
        try:
            self._call('DeleteObject', lambda: self._blob(Bucket, Key).delete_blob(delete_snapshots='include'))
        except ClientError as error:
            if error.response['Error']['Code'] != '404':
                raise
