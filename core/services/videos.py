from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import storages


def s3_video_storage():
    if not settings.AWS_STORAGE_BUCKET_NAME:
        raise ImproperlyConfigured('Falta AWS_STORAGE_BUCKET_NAME.')
    storage = storages['videos']
    return storage.connection.meta.client, storage.bucket_name


def upload_url(key):
    client, bucket = s3_video_storage()
    headers = {'Content-Type': 'video/mp4', 'If-None-Match': '*'}
    url = client.generate_presigned_url(
        'put_object',
        Params={'Bucket': bucket, 'Key': key, 'ContentType': 'video/mp4', 'IfNoneMatch': '*'},
        ExpiresIn=settings.VIDEO_UPLOAD_URL_TTL,
        HttpMethod='PUT',
    )
    return url, headers


def head_video(key):
    client, bucket = s3_video_storage()
    return client.head_object(Bucket=bucket, Key=key)


def playback_url(key):
    client, bucket = s3_video_storage()
    # Range no se firma: el navegador puede pedir cualquier intervalo.
    return client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': bucket, 'Key': key,
            'ResponseContentType': 'video/mp4',
            'ResponseContentDisposition': 'inline',
            'ResponseCacheControl': 'private, no-store',
        },
        ExpiresIn=settings.VIDEO_PLAYBACK_URL_TTL,
        HttpMethod='GET',
    )
