from rest_framework import serializers

from core.models import Video, VideoRendition


class VideoRenditionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoRendition
        fields = ('id', 'profile', 'file_size', 'content_type', 'status')
        read_only_fields = fields


class VideoSerializer(serializers.ModelSerializer):
    renditions = VideoRenditionSerializer(many=True, read_only=True)

    class Meta:
        model = Video
        fields = ('id', 'titulo', 'descripcion', 'duration_seconds', 'status', 'visibility', 'created_at', 'updated_at', 'renditions')
        read_only_fields = ('id', 'status', 'created_at', 'updated_at', 'renditions')


class VideoUploadSerializer(serializers.Serializer):
    titulo = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    descripcion = serializers.CharField(required=False, allow_blank=True, default='')
    filename = serializers.CharField(max_length=255)
    content_type = serializers.ChoiceField(choices=['video/mp4'])
    visibility = serializers.ChoiceField(choices=Video.Visibility.choices, default=Video.Visibility.PRIVATE)

    def validate_filename(self, value):
        if not value.lower().endswith('.mp4'):
            raise serializers.ValidationError('Solo se permiten archivos MP4.')
        return value
