import uuid

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import Video, VideoRendition
from core.services import videos
from .video_serializers import VideoSerializer, VideoUploadSerializer


class VideoStorageUnavailable(APIException):
    status_code = 503
    default_detail = 'Almacenamiento de video no disponible. Intenta de nuevo.'


class VideoViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = VideoSerializer
    parser_classes = [JSONParser]
    permission_classes = [IsAuthenticated]
    lookup_value_regex = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

    def get_queryset(self):
        queryset = Video.objects.prefetch_related('renditions')
        if getattr(self, 'swagger_fake_view', False):
            return queryset.none()
        owner = Q(owner=self.request.user) if self.request.user.is_authenticated else Q(pk__in=[])
        if self.action in ('retrieve', 'playback'):
            return queryset.filter(owner | Q(visibility=Video.Visibility.UNLISTED))
        return queryset.filter(owner)

    def get_permissions(self):
        if self.action in ('retrieve', 'playback'):
            return [AllowAny()]
        return super().get_permissions()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response

    @action(detail=False, methods=['post'], url_path='upload')
    def upload(self, request):
        serializer = VideoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                video = Video.objects.create(owner=request.user, visibility=serializer.validated_data['visibility'])
                key = f'videos/{video.id}/{uuid.uuid4()}.mp4'
                VideoRendition.objects.create(video=video, s3_key=key)
                url, headers = videos.upload_url(key)
        except (BotoCoreError, ClientError, ImproperlyConfigured) as exc:
            raise VideoStorageUnavailable() from exc
        return Response({'video': VideoSerializer(video).data, 'upload_url': url, 'method': 'PUT',
                         'headers': headers, 'expires_in': settings.VIDEO_UPLOAD_URL_TTL}, status=201)

    @action(detail=True, methods=['post'], url_path='confirm')
    def confirm(self, request, pk=None):
        with transaction.atomic():
            video = get_object_or_404(Video.objects.select_for_update(), pk=pk, owner=request.user)
            if video.status == Video.Status.UPLOADED:
                return Response(VideoSerializer(video).data)
            if video.status == Video.Status.FAILED:
                return Response({'detail': 'Carga rechazada. Inicia una nueva.'}, status=409)
            rendition = video.renditions.get(profile='original')
            try:
                metadata = videos.head_video(rendition.s3_key)
            except ClientError as exc:
                if exc.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                    return Response({'detail': 'La carga aun no existe en S3.'}, status=409)
                raise VideoStorageUnavailable() from exc
            except (BotoCoreError, ImproperlyConfigured) as exc:
                raise VideoStorageUnavailable() from exc
            size = metadata.get('ContentLength', 0)
            valid = metadata.get('ContentType') == 'video/mp4' and 0 < size <= settings.VIDEO_MAX_BYTES
            video.status = Video.Status.UPLOADED if valid else Video.Status.FAILED
            rendition.status = video.status
            rendition.file_size = size
            rendition.save(update_fields=['status', 'file_size', 'updated_at'])
            video.save(update_fields=['status', 'updated_at'])
        if not valid:
            return Response({'detail': 'Tipo o tamano del archivo no permitido.'}, status=400)
        return Response(VideoSerializer(video).data)

    @action(detail=True, methods=['get'])
    def playback(self, request, pk=None):
        video = self.get_object()
        if video.status != Video.Status.UPLOADED:
            return Response({'detail': 'El video aun no esta disponible.'}, status=409)
        rendition = get_object_or_404(video.renditions, profile='original', status=Video.Status.UPLOADED)
        try:
            url = videos.playback_url(rendition.s3_key)
        except (BotoCoreError, ClientError, ImproperlyConfigured) as exc:
            raise VideoStorageUnavailable() from exc
        return Response({'url': url, 'expires_in': settings.VIDEO_PLAYBACK_URL_TTL})
