from django.conf import settings
from django.db import transaction
from rest_framework import serializers

from core.models import (
    AdjuntoPublicacion, Comentario, EstadoAdjunto, MotivoReporte, Publicacion, Reporte,
    TipoAdjunto, Usuario,
)
from core.services import medios_publicacion


class AutorPublicacionSerializer(serializers.ModelSerializer):
    """Solo lo que el muro dibuja: el correo de la cuenta no es publico."""

    class Meta:
        model = Usuario
        fields = ['id', 'nombre_completo']


def normalizar_cuerpo(value):
    # Se normalizan los finales de linea para que el limite cuente igual
    # venga el texto de Windows o de un movil.
    return value.replace('\r\n', '\n').strip()


class AdjuntoSerializer(serializers.ModelSerializer):
    """Una foto o un video. `url` es una firma temporal de lectura; si S3 no
    esta disponible llega en nulo y el muro dibuja la publicacion sin el."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = AdjuntoPublicacion
        fields = ['id', 'tipo', 'content_type', 'descripcion', 'estado', 'url']
        read_only_fields = ['id', 'tipo', 'content_type', 'estado', 'url']

    def get_url(self, obj):
        if obj.estado != EstadoAdjunto.LISTO:
            return None
        try:
            return medios_publicacion.url_de_lectura(obj.s3_key, obj.content_type)
        except medios_publicacion.ERRORES_S3:
            return None


class CrearAdjuntoSerializer(serializers.Serializer):
    content_type = serializers.ChoiceField(choices=sorted(medios_publicacion.FORMATOS))
    tamano = serializers.IntegerField(min_value=1)
    descripcion = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')

    def validate(self, attrs):
        # Lo declarado se comprueba aqui para no hacer viajar un archivo que no
        # va a caber; lo real se vuelve a medir en S3 al confirmar.
        tipo = medios_publicacion.tipo_de(attrs['content_type'])
        maximo = medios_publicacion.tamano_maximo(tipo)
        if attrs['tamano'] > maximo:
            raise serializers.ValidationError(
                {'tamano': f'El archivo supera el maximo de {maximo // (1024 * 1024)} MB.'})
        return {**attrs, 'tipo': tipo}


class PublicacionSerializer(serializers.ModelSerializer):
    """Sin banderas de "puede editar" ni de "ya reaccionaste": el muro se lee
    sin token, asi que el frontend lo decide con su sesion y pregunta sus
    reacciones aparte. Quien protege es la vista.

    Los totales salen de la anotacion de la consulta; una instancia sin anotar
    (recien creada) los reporta en cero, que es lo que tiene.
    """

    autor = AutorPublicacionSerializer(read_only=True)
    # Opcional si hay fotos o video; `validate` exige una de las dos cosas.
    cuerpo = serializers.CharField(
        max_length=Publicacion.LIMITE_CUERPO, required=False, allow_blank=True, default='',
    )
    total_reacciones = serializers.SerializerMethodField()
    total_comentarios = serializers.SerializerMethodField()
    adjuntos = serializers.SerializerMethodField()
    # Fotos o video ya subidos y confirmados, en el orden en que se muestran.
    adjuntos_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False, allow_empty=True,
    )

    class Meta:
        model = Publicacion
        fields = ['id', 'autor', 'cuerpo', 'fecha_publicacion', 'fecha_edicion',
                  'total_reacciones', 'total_comentarios', 'adjuntos', 'adjuntos_ids']
        read_only_fields = ['id', 'fecha_publicacion', 'fecha_edicion']

    def validate_cuerpo(self, value):
        return normalizar_cuerpo(value)

    def validate(self, attrs):
        ids = attrs.get('adjuntos_ids')
        if self.instance is not None:
            if ids is not None:
                raise serializers.ValidationError(
                    {'adjuntos_ids': 'Las fotos y el video no se cambian al editar.'})
            if 'cuerpo' in attrs and not attrs['cuerpo'] and not self._listos(self.instance):
                raise serializers.ValidationError({'cuerpo': 'Escribe algo para publicar.'})
            return attrs

        ids = ids or []
        if not attrs.get('cuerpo') and not ids:
            raise serializers.ValidationError({'cuerpo': 'Escribe algo o agrega una foto o un video.'})
        attrs['adjuntos_ids'] = self._validar_adjuntos(ids)
        return attrs

    def _validar_adjuntos(self, ids):
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError({'adjuntos_ids': 'Hay archivos repetidos.'})
        disponibles = {
            adjunto.pk: adjunto for adjunto in AdjuntoPublicacion.objects.filter(
                pk__in=ids, autor=self.context['request'].user,
                estado=EstadoAdjunto.LISTO, publicacion__isnull=True,
            )
        }
        if len(disponibles) != len(ids):
            raise serializers.ValidationError(
                {'adjuntos_ids': 'Algun archivo no termino de subirse o ya se uso.'})
        adjuntos = [disponibles[i] for i in ids]
        videos = sum(1 for a in adjuntos if a.tipo == TipoAdjunto.VIDEO)
        imagenes = len(adjuntos) - videos
        if videos and imagenes:
            raise serializers.ValidationError(
                {'adjuntos_ids': 'Una publicacion lleva fotos o un video, no ambos.'})
        if videos > 1:
            raise serializers.ValidationError({'adjuntos_ids': 'Solo cabe un video por publicacion.'})
        if imagenes > settings.PUBLICACION_MAX_IMAGENES:
            raise serializers.ValidationError(
                {'adjuntos_ids': f'Caben hasta {settings.PUBLICACION_MAX_IMAGENES} fotos por publicacion.'})
        return adjuntos

    @transaction.atomic
    def create(self, validated_data):
        adjuntos = validated_data.pop('adjuntos_ids', [])
        publicacion = super().create(validated_data)
        if adjuntos:
            # Bajo bloqueo y solo si siguen libres: dos publicaciones enviadas a
            # la vez no pueden quedarse con el mismo archivo.
            libres = AdjuntoPublicacion.objects.select_for_update().filter(
                pk__in=[a.pk for a in adjuntos], publicacion__isnull=True,
            )
            if len(libres) != len(adjuntos):
                raise serializers.ValidationError({'adjuntos_ids': 'Algun archivo ya se uso.'})
            for orden, adjunto in enumerate(adjuntos):
                adjunto.publicacion = publicacion
                adjunto.orden = orden
            AdjuntoPublicacion.objects.bulk_update(adjuntos, ['publicacion', 'orden'])
        return publicacion

    @staticmethod
    def _listos(obj):
        # En el muro llegan prefetcheados; recien creada, se consultan aqui.
        return [a for a in obj.adjuntos.all() if a.estado == EstadoAdjunto.LISTO]

    def get_adjuntos(self, obj):
        return AdjuntoSerializer(self._listos(obj), many=True).data

    def get_total_reacciones(self, obj):
        return getattr(obj, 'total_reacciones', 0)

    def get_total_comentarios(self, obj):
        return getattr(obj, 'total_comentarios', 0)


class ComentarioSerializer(serializers.ModelSerializer):
    autor = AutorPublicacionSerializer(read_only=True)
    cuerpo = serializers.CharField(max_length=Comentario.LIMITE_CUERPO)

    class Meta:
        model = Comentario
        fields = ['id', 'publicacion', 'autor', 'cuerpo', 'fecha_publicacion', 'fecha_edicion']
        read_only_fields = ['id', 'publicacion', 'fecha_publicacion', 'fecha_edicion']

    def validate_cuerpo(self, value):
        return normalizar_cuerpo(value)


class EstadoReaccionSerializer(serializers.Serializer):
    """Respuesta de poner o quitar la reaccion: el total ya recontado."""

    reaccionaste = serializers.BooleanField()
    total_reacciones = serializers.IntegerField()


class MotivoField(serializers.ChoiceField):
    """Motivo como par clave/nombre, igual que la categoria de un curso: se
    escribe con la clave y la cola de moderacion dibuja el nombre."""

    def __init__(self, **kwargs):
        super().__init__(choices=MotivoReporte.choices, **kwargs)

    def to_representation(self, value):
        return {'clave': value, 'nombre': MotivoReporte(value).label}


class CrearReporteSerializer(serializers.Serializer):
    motivo = MotivoField()
    detalle = serializers.CharField(
        max_length=Reporte.LIMITE_DETALLE, required=False, allow_blank=True, default='',
    )


class ReporteSerializer(serializers.ModelSerializer):
    """Un caso de la cola de moderacion, legible aunque el contenido ya no exista."""

    tipo = serializers.SerializerMethodField()
    motivo = MotivoField(read_only=True)
    autor_reportado = AutorPublicacionSerializer(read_only=True)
    reportado_por = AutorPublicacionSerializer(read_only=True)
    # A donde lleva "ver en el muro": la publicacion, o la del comentario.
    publicacion_id = serializers.SerializerMethodField()

    class Meta:
        model = Reporte
        fields = ['id', 'tipo', 'publicacion', 'comentario', 'publicacion_id', 'motivo',
                  'detalle', 'cuerpo_reportado', 'autor_reportado', 'reportado_por',
                  'estado', 'creado_en']
        read_only_fields = fields

    def get_tipo(self, obj):
        return 'comentario' if obj.comentario_id else 'publicacion'

    def get_publicacion_id(self, obj):
        if obj.publicacion_id:
            return str(obj.publicacion_id)
        return str(obj.comentario.publicacion_id) if obj.comentario_id else None


class ResolverReporteSerializer(serializers.Serializer):
    accion = serializers.ChoiceField(choices=[('eliminar', 'Eliminar'), ('descartar', 'Descartar')])
