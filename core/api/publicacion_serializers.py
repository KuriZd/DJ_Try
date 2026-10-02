from rest_framework import serializers

from core.models import Comentario, MotivoReporte, Publicacion, Reporte, Usuario


class AutorPublicacionSerializer(serializers.ModelSerializer):
    """Solo lo que el muro dibuja: el correo de la cuenta no es publico."""

    class Meta:
        model = Usuario
        fields = ['id', 'nombre_completo']


def normalizar_cuerpo(value):
    # Se normalizan los finales de linea para que el limite cuente igual
    # venga el texto de Windows o de un movil.
    return value.replace('\r\n', '\n').strip()


class PublicacionSerializer(serializers.ModelSerializer):
    """Sin banderas de "puede editar" ni de "ya reaccionaste": el muro se lee
    sin token, asi que el frontend lo decide con su sesion y pregunta sus
    reacciones aparte. Quien protege es la vista.

    Los totales salen de la anotacion de la consulta; una instancia sin anotar
    (recien creada) los reporta en cero, que es lo que tiene.
    """

    autor = AutorPublicacionSerializer(read_only=True)
    cuerpo = serializers.CharField(max_length=Publicacion.LIMITE_CUERPO)
    total_reacciones = serializers.SerializerMethodField()
    total_comentarios = serializers.SerializerMethodField()

    class Meta:
        model = Publicacion
        fields = ['id', 'autor', 'cuerpo', 'fecha_publicacion', 'fecha_edicion',
                  'total_reacciones', 'total_comentarios']
        read_only_fields = ['id', 'fecha_publicacion', 'fecha_edicion']

    def validate_cuerpo(self, value):
        return normalizar_cuerpo(value)

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
