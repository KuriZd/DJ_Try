from rest_framework import serializers
from rest_framework.reverse import reverse

from core.models import Certificado, EnvioCertificado, HistorialCertificado, PlantillaCertificado, Postulacion, TipoCertificado


class EmitirCertificadoSerializer(serializers.Serializer):
    postulacion = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    tipo = serializers.CharField(max_length=40)
    plantilla_id = serializers.CharField(max_length=40)
    plantilla_version = serializers.CharField(max_length=20)
    resultado = serializers.CharField(max_length=10000)
    justificacion_manual = serializers.CharField(max_length=5000)
    periodo_participacion = serializers.CharField(max_length=100, required=False, allow_blank=True)
    autoridad_emisora = serializers.CharField(max_length=180)
    cargo_autoridad = serializers.CharField(max_length=180, required=False, allow_blank=True)
    observaciones_internas = serializers.CharField(max_length=5000, required=False, allow_blank=True)

    def validate_postulacion(self, value):
        if not Postulacion.objects.filter(pk=value, aspirante__eliminado_en__isnull=True).exists():
            raise serializers.ValidationError('La postulación no existe o su aspirante está eliminado.')
        return value


class MotivoCertificadoSerializer(serializers.Serializer):
    motivo = serializers.CharField(max_length=5000)


class CertificadoSerializer(serializers.ModelSerializer):
    archivo_pdf = serializers.SerializerMethodField()
    aspirante_nombre = serializers.SerializerMethodField()
    postulacion_folio = serializers.SerializerMethodField()

    class Meta:
        model = Certificado
        fields = [
            'id', 'folio', 'codigo_verificacion', 'aspirante', 'aspirante_nombre',
            'postulacion', 'postulacion_folio', 'tipo', 'proceso', 'proceso_nombre',
            'plantilla_id', 'plantilla_version', 'estado', 'resultado', 'periodo_participacion',
            'tipo_generacion', 'autoridad_emisora', 'cargo_autoridad', 'emitido_en',
            'cancelado_en', 'revocado_en', 'enviado_en', 'creado_en', 'actualizado_en', 'archivo_pdf',
        ]
        read_only_fields = fields

    def get_archivo_pdf(self, obj):
        if obj.estado not in ('emitido', 'enviado', 'reenviado') or not obj.pdf_disponible:
            return None
        return reverse('api:certificado-descargar', args=[obj.pk], request=self.context.get('request'))

    def get_aspirante_nombre(self, obj):
        return obj.aspirante_snapshot.get('generales', {}).get('nombre_completo')

    def get_postulacion_folio(self, obj):
        return obj.aspirante_snapshot.get('postulacion', {}).get('folio')


class CertificadoDetalleSerializer(CertificadoSerializer):
    class Meta(CertificadoSerializer.Meta):
        fields = CertificadoSerializer.Meta.fields + ['aspirante_snapshot', 'motivo_cancelacion', 'motivo_revocacion']
        read_only_fields = fields


class CertificadoGestionSerializer(CertificadoDetalleSerializer):
    class Meta(CertificadoDetalleSerializer.Meta):
        fields = CertificadoDetalleSerializer.Meta.fields + ['justificacion_manual', 'observaciones_internas', 'emitido_por']
        read_only_fields = fields


class HistorialCertificadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistorialCertificado
        fields = ['id', 'accion', 'estado_anterior', 'estado_nuevo', 'realizado_por',
                  'realizado_por_email', 'descripcion', 'metadata', 'creado_en']
        read_only_fields = fields


class EnvioCertificadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnvioCertificado
        fields = ['id', 'destinatario_email', 'estado', 'numero_intento', 'proveedor_id',
                  'mensaje_error', 'enviado_en', 'creado_en']
        read_only_fields = fields


class TipoCertificadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoCertificado
        fields = ['clave', 'nombre', 'activo']
        read_only_fields = fields


class PlantillaCertificadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlantillaCertificado
        fields = ['id', 'version', 'tipo', 'nombre', 'texto_institucional', 'activa', 'creado_en']
        read_only_fields = ['creado_en']
        validators = []

    def validate(self, attrs):
        if PlantillaCertificado.objects.filter(id=attrs['id'], version=attrs['version']).exists():
            raise serializers.ValidationError('Ya existe esta versión de la plantilla; crea una versión nueva.')
        if not attrs['tipo'].activo:
            raise serializers.ValidationError({'tipo': 'El tipo está inactivo.'})
        return attrs


class ActivarPlantillaSerializer(serializers.Serializer):
    activa = serializers.BooleanField()
