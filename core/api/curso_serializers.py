from django.urls import reverse
from rest_framework import serializers

from core.models import (
    CategoriaCurso, Curso, Leccion, Modulo, Inscripcion, ProgresoLeccion,
    CertificadoCurso, Usuario, Video,
)
from .curso_permissions import administra_cursos, gestiona_curso
from .serializers import permisos_de


class CategoriaField(serializers.ChoiceField):
    """Categoria como par clave/nombre.

    Se escribe con la clave y se lee con las dos: el filtro del catalogo
    compara por clave y la etiqueta que se dibuja es el nombre. Devolver solo
    la clave obligaria al frontend a mantener su propia tabla de nombres, que
    es justo lo que se desincroniza el dia que se agrega una categoria.
    """

    def __init__(self, **kwargs):
        super().__init__(choices=CategoriaCurso.choices, allow_blank=True, required=False, **kwargs)

    def to_representation(self, value):
        if not value:
            return None
        return {'clave': value, 'nombre': CategoriaCurso(value).label}


class ResumenTemarioMixin:
    """Cuenta de modulos, de lecciones y duracion, calculadas del temario.

    Salen de las lecciones activas y no de un campo escrito a mano: el
    catalogo no puede anunciar doce lecciones y el temario tener cinco.
    Requieren que la consulta traiga `modulos__lecciones` prefetcheado.
    """

    def get_total_modulos(self, obj):
        return len(obj.modulos.all())

    def get_total_lecciones(self, obj):
        return sum(1 for leccion in self._lecciones_activas(obj))

    def get_duracion_total(self, obj):
        return sum(leccion.duracion for leccion in self._lecciones_activas(obj))

    @staticmethod
    def _lecciones_activas(curso):
        return [leccion for modulo in curso.modulos.all()
                for leccion in modulo.lecciones.all() if leccion.activo]


class CursoSerializer(ResumenTemarioMixin, serializers.ModelSerializer):
    instructor = serializers.PrimaryKeyRelatedField(
        queryset=Usuario.objects.filter(estado='activo', eliminado_en__isnull=True), required=False,
    )
    instructor_nombre = serializers.CharField(source='instructor.nombre_completo', read_only=True)
    categoria = CategoriaField()
    total_modulos = serializers.SerializerMethodField()
    total_lecciones = serializers.SerializerMethodField()
    duracion_total = serializers.SerializerMethodField()

    class Meta:
        model = Curso
        fields = ['id', 'slug', 'titulo', 'resumen', 'descripcion', 'objetivos', 'imagen',
                  'instructor', 'instructor_nombre', 'fecha_creacion', 'activo',
                  'duracion_estimada', 'categoria', 'nivel',
                  'total_modulos', 'total_lecciones', 'duracion_total']
        read_only_fields = ['id', 'slug', 'fecha_creacion']

    def validate_objetivos(self, value):
        if not isinstance(value, list) or any(
            not isinstance(objetivo, str) or not objetivo.strip() for objetivo in value
        ):
            raise serializers.ValidationError('Debe ser una lista de textos no vacios.')
        return [objetivo.strip() for objetivo in value]

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
    curso = serializers.PrimaryKeyRelatedField(source='modulo.curso', read_only=True)

    class Meta:
        model = Leccion
        fields = ['id', 'modulo', 'curso', 'titulo', 'descripcion', 'video', 'orden', 'duracion', 'activo']
        read_only_fields = ['id', 'curso']
        # La unicidad se comprueba bajo el bloqueo del curso, tambien en PATCH.
        validators = []

    def validate(self, attrs):
        modulo = attrs.get('modulo', self.instance.modulo if self.instance else None)
        request = self.context['request']
        if not gestiona_curso(request, modulo.curso):
            raise serializers.ValidationError({'modulo': 'No puedes administrar este curso.'})
        if self.instance and modulo.curso_id != self.instance.modulo.curso_id:
            raise serializers.ValidationError({'modulo': 'Una leccion no puede cambiar de curso.'})
        video = attrs.get('video', self.instance.video if self.instance else None)
        if video.eliminado_en is not None or video.status != Video.Status.UPLOADED or not video.renditions.filter(
            profile='original', status=Video.Status.UPLOADED,
        ).exists():
            raise serializers.ValidationError({'video': 'Primero confirma la carga del video.'})
        if not administra_cursos(request) and video.owner_id != request.user.pk:
            raise serializers.ValidationError({'video': 'El video debe pertenecerte.'})
        return attrs


class ModuloSerializer(serializers.ModelSerializer):
    lecciones = serializers.SerializerMethodField()

    class Meta:
        model = Modulo
        fields = ['id', 'curso', 'titulo', 'resumen', 'orden', 'lecciones']
        read_only_fields = ['id']
        # El orden unico se comprueba bajo el bloqueo del curso, igual que en Leccion.
        validators = []

    def get_lecciones(self, obj):
        lecciones = list(obj.lecciones.all())
        if not self.context.get('temario_completo'):
            # Una leccion desactivada esta en revision: todavia no es temario.
            lecciones = [leccion for leccion in lecciones if leccion.activo]
        return LeccionSerializer(lecciones, many=True, context=self.context).data

    def validate(self, attrs):
        curso = attrs.get('curso', self.instance.curso if self.instance else None)
        request = self.context['request']
        if not gestiona_curso(request, curso):
            raise serializers.ValidationError({'curso': 'No puedes administrar este curso.'})
        if self.instance and curso.pk != self.instance.curso_id:
            raise serializers.ValidationError({'curso': 'Un modulo no puede cambiar de curso.'})
        return attrs


class CursoDetalleSerializer(CursoSerializer):
    """El curso con su temario.

    El temario viaja en la ficha porque la pantalla lo dibuja antes de que la
    persona se inscriba: ensena que hay dentro y marca bajo llave lo que no
    puede abrir. Lo que sigue exigiendo inscripcion es reproducir, que es lo
    unico que el candado protege de verdad: `videos/{id}/playback/` la
    comprueba antes de firmar la URL de S3.
    """

    modulos = serializers.SerializerMethodField()

    class Meta(CursoSerializer.Meta):
        fields = CursoSerializer.Meta.fields + ['modulos']

    def get_modulos(self, obj):
        contexto = {**self.context, 'temario_completo': gestiona_curso(self.context['request'], obj)}
        return ModuloSerializer(obj.modulos.all(), many=True, context=contexto).data


class InscripcionSerializer(serializers.ModelSerializer):
    curso_slug = serializers.SlugField(source='curso.slug', read_only=True)

    class Meta:
        model = Inscripcion
        fields = ['id', 'usuario', 'curso', 'curso_slug', 'fecha_inscripcion',
                  'completado', 'porcentaje_avance']
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
    curso_slug = serializers.SlugField(source='curso.slug', read_only=True)
    curso_titulo = serializers.CharField(source='curso.titulo', read_only=True)

    class Meta:
        model = CertificadoCurso
        fields = ['id', 'usuario', 'curso', 'curso_slug', 'curso_titulo',
                  'codigo_certificado', 'fecha_emision', 'archivo_pdf']
        read_only_fields = fields

    def get_archivo_pdf(self, obj):
        return self.context['request'].build_absolute_uri(
            reverse('api:certificado-curso-descargar', args=[obj.pk]),
        )
