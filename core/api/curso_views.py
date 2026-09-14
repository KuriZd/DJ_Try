from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Curso, Leccion, Inscripcion, ProgresoLeccion, CertificadoCurso, Video
from core.services.cursos import recalcular_curso, recalcular_inscripcion
from .curso_permissions import PuedeGestionarCursos, administra_cursos, gestiona_curso, permisos_cursos
from .curso_serializers import (
    CursoSerializer, LeccionSerializer, InscripcionSerializer, InscribirSerializer,
    ProgresoSerializer, RegistrarProgresoSerializer, CertificadoCursoSerializer,
    ResultadoProgresoSerializer,
)


UUID_REGEX = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'


class APIPrivada:
    permission_classes = [IsAuthenticated]
    lookup_value_regex = UUID_REGEX

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


def filtro_gestion(request, prefijo=''):
    if administra_cursos(request):
        return Q(pk__isnull=False)
    if 'cursos:crear' in permisos_cursos(request):
        return Q(**{prefijo + 'instructor': request.user})
    return Q(pk__in=[])


def exigir_inscripcion(request, curso):
    if not curso.activo or not Inscripcion.objects.filter(curso=curso, usuario=request.user).exists():
        raise PermissionDenied('Necesitas estar inscrito en un curso activo para acceder al contenido.')


@transaction.atomic
def inscribir_usuario(request, curso_id):
    curso = get_object_or_404(Curso.objects.select_for_update(), pk=curso_id, activo=True)
    inscripcion, creada = Inscripcion.objects.get_or_create(curso=curso, usuario=request.user)
    recalcular_inscripcion(inscripcion)
    return Response(InscripcionSerializer(inscripcion).data, status=201 if creada else 200)


class CursoViewSet(APIPrivada, viewsets.ModelViewSet):
    serializer_class = CursoSerializer
    permission_classes = [IsAuthenticated, PuedeGestionarCursos]

    def get_queryset(self):
        qs = Curso.objects.select_related('instructor')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        if self.action in ('list', 'inscribir'):
            return qs.filter(Q(activo=True) | filtro_gestion(self.request)).distinct()
        acceso = Q(activo=True, inscripciones__usuario=self.request.user)
        return qs.filter(acceso | filtro_gestion(self.request)).distinct()

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        get_object_or_404(Curso.objects.select_for_update(), pk=kwargs['pk'])
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        get_object_or_404(Curso.objects.select_for_update(), pk=kwargs['pk'])
        return super().destroy(request, *args, **kwargs)

    @swagger_auto_schema(request_body=no_body, responses={200: InscripcionSerializer, 201: InscripcionSerializer})
    @action(detail=True, methods=['post'])
    def inscribir(self, request, pk=None):
        return inscribir_usuario(request, pk)

    @swagger_auto_schema(responses={200: LeccionSerializer(many=True)})
    @action(detail=True, methods=['get'])
    def lecciones(self, request, pk=None):
        curso = self.get_object()
        qs = curso.lecciones.all()
        if not gestiona_curso(request, curso):
            exigir_inscripcion(request, curso)
            qs = qs.filter(activo=True)
        return Response(LeccionSerializer(qs, many=True, context=self.get_serializer_context()).data)


class LeccionViewSet(APIPrivada, viewsets.ModelViewSet):
    serializer_class = LeccionSerializer
    permission_classes = [IsAuthenticated, PuedeGestionarCursos]

    def get_queryset(self):
        qs = Leccion.objects.select_related('curso', 'video')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        acceso = Q(activo=True, curso__activo=True, curso__inscripciones__usuario=self.request.user)
        qs = qs.filter(acceso | filtro_gestion(self.request, 'curso__')).distinct()
        if curso_id := self.request.query_params.get('curso'):
            from rest_framework.fields import UUIDField
            qs = qs.filter(curso_id=UUIDField().run_validation(curso_id))
        return qs

    def comprobar_orden(self, serializer, curso):
        orden = serializer.validated_data.get('orden', serializer.instance.orden if serializer.instance else None)
        qs = Leccion.objects.filter(curso=curso, orden=orden)
        if serializer.instance:
            qs = qs.exclude(pk=serializer.instance.pk)
        if qs.exists():
            raise ValidationError({'orden': 'Ya existe una leccion con ese orden en el curso.'})

    def bloquear_video(self, serializer):
        # Comparte bloqueo con DELETE /videos/: no se puede vincular un video
        # que haya sido eliminado entre la validacion y la escritura.
        video = serializer.validated_data.get('video', serializer.instance.video if serializer.instance else None)
        video = Video.objects.select_for_update().get(pk=video.pk)
        if video.eliminado_en is not None:
            raise ValidationError({'video': 'El video fue eliminado.'})
        serializer.validated_data['video'] = video

    @transaction.atomic
    def perform_create(self, serializer):
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=serializer.validated_data['curso'].pk)
        if not gestiona_curso(self.request, curso):
            raise PermissionDenied('No puedes administrar este curso.')
        self.comprobar_orden(serializer, curso)
        self.bloquear_video(serializer)
        serializer.save(curso=curso)
        recalcular_curso(curso)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        leccion = self.get_object()
        get_object_or_404(Curso.objects.select_for_update(), pk=leccion.curso_id)
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        self.comprobar_orden(serializer, serializer.instance.curso)
        self.bloquear_video(serializer)
        cambio_video = 'video' in serializer.validated_data and serializer.validated_data['video'].pk != serializer.instance.video_id
        leccion = serializer.save()
        if cambio_video:
            leccion.progresos.update(visto=False, fecha_completado=None)
        recalcular_curso(leccion.curso)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        leccion = self.get_object()
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=leccion.curso_id)
        response = super().destroy(request, *args, **kwargs)
        recalcular_curso(curso)
        return response

    @swagger_auto_schema(request_body=RegistrarProgresoSerializer, responses={200: ResultadoProgresoSerializer})
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    @transaction.atomic
    def progreso(self, request, pk=None):
        serializer = RegistrarProgresoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        leccion = self.get_object()
        curso = get_object_or_404(Curso.objects.select_for_update(), pk=leccion.curso_id)
        leccion = get_object_or_404(Leccion, pk=pk, activo=True)
        exigir_inscripcion(request, curso)
        progreso, _ = ProgresoLeccion.objects.get_or_create(usuario=request.user, leccion=leccion)
        visto = serializer.validated_data['visto']
        progreso.fecha_completado = (progreso.fecha_completado or timezone.now()) if visto else None
        progreso.visto = visto
        progreso.save(update_fields=['visto', 'fecha_completado'])
        inscripcion = Inscripcion.objects.select_related('usuario', 'curso__instructor').get(usuario=request.user, curso=curso)
        recalcular_inscripcion(inscripcion)
        return Response({'progreso': ProgresoSerializer(progreso).data,
                         'inscripcion': InscripcionSerializer(inscripcion).data})


class InscripcionViewSet(APIPrivada, mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = InscripcionSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Inscripcion.objects.none()
        return Inscripcion.objects.filter(usuario=self.request.user)

    def get_serializer_class(self):
        return InscribirSerializer if self.action == 'create' else InscripcionSerializer

    @swagger_auto_schema(responses={200: InscripcionSerializer, 201: InscripcionSerializer})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return inscribir_usuario(request, serializer.validated_data['curso'].pk)


class ProgresoLeccionViewSet(APIPrivada, viewsets.ReadOnlyModelViewSet):
    serializer_class = ProgresoSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return ProgresoLeccion.objects.none()
        return ProgresoLeccion.objects.filter(usuario=self.request.user)


class CertificadoCursoViewSet(APIPrivada, viewsets.ReadOnlyModelViewSet):
    serializer_class = CertificadoCursoSerializer

    def get_queryset(self):
        qs = CertificadoCurso.objects.defer('archivo_pdf')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        return qs.filter(usuario=self.request.user)

    @swagger_auto_schema(responses={200: openapi.Response('Certificado PDF privado', schema=openapi.Schema(type=openapi.TYPE_FILE))})
    @action(detail=True, methods=['get'])
    def descargar(self, request, pk=None):
        certificado = self.get_object()
        response = HttpResponse(bytes(certificado.archivo_pdf), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="certificado-{certificado.codigo_certificado}.pdf"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
