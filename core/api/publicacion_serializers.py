from rest_framework import serializers

from core.models import Publicacion, Usuario


class AutorPublicacionSerializer(serializers.ModelSerializer):
    """Solo lo que el muro dibuja: el correo de la cuenta no es publico."""

    class Meta:
        model = Usuario
        fields = ['id', 'nombre_completo']


class PublicacionSerializer(serializers.ModelSerializer):
    """Sin banderas de "puede editar": el muro se lee sin token, asi que el
    frontend lo decide con la cuenta de su sesion. Quien protege es la vista."""

    autor = AutorPublicacionSerializer(read_only=True)
    cuerpo = serializers.CharField(max_length=Publicacion.LIMITE_CUERPO)

    class Meta:
        model = Publicacion
        fields = ['id', 'autor', 'cuerpo', 'fecha_publicacion', 'fecha_edicion']
        read_only_fields = ['id', 'fecha_publicacion', 'fecha_edicion']

    def validate_cuerpo(self, value):
        # Se normalizan los finales de linea para que el limite cuente igual
        # venga el texto de Windows o de un movil.
        return value.replace('\r\n', '\n').strip()
