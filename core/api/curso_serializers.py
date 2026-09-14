from django.urls import reverse
from rest_framework import serializers

from core.models import Curso, Leccion, Inscripcion, ProgresoLeccion, CertificadoCurso, Usuario, Video
from .curso_permissions import administra_cursos, gestiona_curso
from .serializers import permisos_de


class CursoSerializer(serializers.ModelSerializer):
    instructor = serializers.PrimaryKeyRelatedField(
        queryset=Usuario.objects.filter(estado='activo', eliminado_en__isnull=True), required=False,
    )
    instructor_nombre = serializers.CharField(source='instructor.nombre_completo', read_only=True)

    class Meta:
        model = Curso
        fields = ['id', 'titulo', 'descripcion', 'imagen', 'instructor', 'instructor_nombre',
                  'fecha_creacion', 'activo', 'duracion_estimada', 'categoria']
        read_only_fields = ['id', 'fecha_creacion']

    def validate(self, attrs):
        request = self.context['request']
        instructor = attrs.get('instructor', self.instance.instructor if self.instance else request.user)
        if not administra_cursos(request) and instructor.pk != request.user.pk:
            raise serializers.ValidationError({'instructor': 'Solo puedes asignarte a ti mismo.'})
        if not {'cursos:crear', 'cursos:administrar'} & permisos_de(instructor):
            raise serializers.ValidationError({'instructor': 'El usuario no tiene permiso para impartir cursos.'})
        attrs['instructor'] = instructor
        return attrs


class LeccionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Leccion
        fields = ['id', 'curso', 'titulo', 'descripcion', 'video', 'orden', 'duracion', 'activo']
        read_only_fields = ['id']
        # La unicidad se comprueba bajo el bloqueo del curso, tambien en PATCH.
        validators = []

    def validate(self, attrs):
        curso = attrs.get('curso', self.instance.curso if self.instance else None)
        request = self.context['request']
        if not gestiona_curso(request, curso):
            raise serializers.ValidationError({'curso': 'No puedes administrar este curso.'})
        if self.instance and curso.pk != self.instance.curso_id:
            raise serializers.ValidationError({'curso': 'Una leccion no puede cambiar de curso.'})
        video = attrs.get('video', self.instance.video if self.instance else None)
        if video.eliminado_en is not None or video.status != Video.Status.UPLOADED or not video.renditions.filter(
            profile='original', status=Video.Status.UPLOADED,
        ).exists():
            raise serializers.ValidationError({'video': 'Primero confirma la carga del video.'})
        if not administra_cursos(request) and video.owner_id != request.user.pk:
            raise serializers.ValidationError({'video': 'El video debe pertenecerte.'})
        return attrs


class InscripcionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Inscripcion
        fields = ['id', 'usuario', 'curso', 'fecha_inscripcion', 'completado', 'porcentaje_avance']
        read_only_fields = fields


class InscribirSerializer(serializers.Serializer):
    curso = serializers.PrimaryKeyRelatedField(queryset=Curso.objects.all())


class ProgresoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgresoLeccion
        fields = ['id', 'usuario', 'leccion', 'visto', 'fecha_completado']
        read_only_fields = fields


class RegistrarProgresoSerializer(serializers.Serializer):
    visto = serializers.BooleanField(default=True)


class ResultadoProgresoSerializer(serializers.Serializer):
    progreso = ProgresoSerializer(read_only=True)
    inscripcion = InscripcionSerializer(read_only=True)


class CertificadoCursoSerializer(serializers.ModelSerializer):
    archivo_pdf = serializers.SerializerMethodField()

    class Meta:
        model = CertificadoCurso
        fields = ['id', 'usuario', 'curso', 'codigo_certificado', 'fecha_emision', 'archivo_pdf']
        read_only_fields = fields

    def get_archivo_pdf(self, obj):
        return self.context['request'].build_absolute_uri(reverse('api:certificado-curso-descargar', args=[obj.pk]))
