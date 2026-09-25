import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from core.api.views import abrir_sesion

from core.models import (
    Curso, Modulo, Leccion, Inscripcion, ProgresoLeccion, CertificadoCurso,
    Rol, Usuario, UsuarioRol, Video, VideoRendition,
)


class CursosAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def usuario(rol):
            user = Usuario.objects.create(
                id=uuid.uuid4(), nombre_completo='Alumno <prueba> ' + rol,
                email=f'{uuid.uuid4()}@example.test', password_hash='!', estado='activo',
                creado_en=timezone.now(), actualizado_en=timezone.now(),
            )
            UsuarioRol.objects.create(usuario=user, rol=Rol.objects.get(clave=rol), asignado_en=timezone.now())
            return user
        cls.admin = usuario('administrador')
        cls.empresa = usuario('empresa')
        cls.instructor = usuario('instructor')
        cls.otro_instructor = usuario('instructor')
        cls.alumno = usuario('aspirante')
        cls.otro = usuario('aspirante')
        cls.curso = Curso.objects.create(titulo='Introducción & práctica', instructor=cls.instructor, activo=True)
        cls.video = Video.objects.create(owner=cls.instructor, status='uploaded', visibility='unlisted')
        VideoRendition.objects.create(video=cls.video, s3_key=f'videos/{cls.video.id}.mp4', status='uploaded')
        cls.modulo = Modulo.objects.create(curso=cls.curso, titulo='Contenido', orden=1)
        cls.leccion = Leccion.objects.create(modulo=cls.modulo, video=cls.video, titulo='Inicio', orden=1)
        cls.segunda = Leccion.objects.create(modulo=cls.modulo, video=cls.video, titulo='Final', orden=2)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.alumno)
        self.curso_url = f'/api/cursos/{self.curso.slug}/'
        self.leccion_url = f'/api/lecciones/{self.leccion.pk}/'

    def inscribir(self):
        response = self.client.post(self.curso_url + 'inscribir/', {}, format='json')
        self.assertIn(response.status_code, (200, 201), response.data)
        return response

    def completar(self, leccion=None, visto=True):
        return self.client.post(f'/api/lecciones/{(leccion or self.leccion).pk}/progreso/',
                                {'visto': visto}, format='json')

    def test_autenticacion_jwt_y_anonimos(self):
        self.client.force_authenticate(None)
        for url in ('/api/modulos/', '/api/lecciones/', '/api/inscripciones/', '/api/progresos-lecciones/', '/api/certificados-cursos/'):
            self.assertEqual(self.client.get(url).status_code, 401)
        # El catalogo y la ficha son publicos; inscribirse no.
        self.assertEqual(self.client.get('/api/cursos/').status_code, 200)
        self.assertEqual(self.client.get(self.curso_url).status_code, 200)
        self.assertEqual(self.client.post(self.curso_url + 'inscribir/').status_code, 401)
        token = abrir_sesion(self.alumno, APIRequestFactory().post('/api/auth/login/'))['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(self.client.get('/api/inscripciones/').status_code, 200)
        self.alumno.estado = 'bloqueado'
        self.alumno.save(update_fields=['estado'])
        self.assertEqual(self.client.get('/api/inscripciones/').status_code, 401)

    def test_catalogo_sin_acceso_al_contenido(self):
        borrador = Curso.objects.create(titulo='Borrador', instructor=self.instructor)
        response = self.client.get('/api/cursos/')
        self.assertEqual([str(self.curso.pk)], [str(c['id']) for c in response.data])
        # La ficha ensena el temario sin inscripcion: dice que hay dentro y lo
        # marca bajo llave. Lo que no entrega es el contenido de la leccion.
        ficha = self.client.get(self.curso_url)
        self.assertEqual(ficha.status_code, 200)
        self.assertEqual([m['titulo'] for m in ficha.data['modulos']], ['Contenido'])
        self.assertEqual(len(ficha.data['modulos'][0]['lecciones']), 2)
        self.assertEqual(self.client.get(self.curso_url + 'lecciones/').status_code, 403)
        self.assertEqual(self.client.get(self.leccion_url).status_code, 404)
        self.assertEqual(self.client.get(f'/api/cursos/{borrador.slug}/').status_code, 404)
        self.assertEqual(self.completar().status_code, 404)

    def test_ficha_publica_omite_lecciones_desactivadas(self):
        self.segunda.activo = False
        self.segunda.save(update_fields=['activo'])
        self.client.force_authenticate(None)
        ficha = self.client.get(self.curso_url)
        self.assertEqual([l['titulo'] for l in ficha.data['modulos'][0]['lecciones']], ['Inicio'])
        self.assertEqual(ficha.data['total_lecciones'], 1)
        self.client.force_authenticate(self.instructor)
        ficha = self.client.get(self.curso_url)
        self.assertEqual(len(ficha.data['modulos'][0]['lecciones']), 2)

    def test_slug_unico_y_estable(self):
        self.client.force_authenticate(self.instructor)
        primero = self.client.post('/api/cursos/', {'titulo': 'Marco normativo'}, format='json')
        segundo = self.client.post('/api/cursos/', {'titulo': 'Marco normativo'}, format='json')
        self.assertEqual(primero.data['slug'], 'marco-normativo')
        self.assertEqual(segundo.data['slug'], 'marco-normativo-2')
        url = f"/api/cursos/{primero.data['slug']}/"
        renombrado = self.client.patch(url, {'titulo': 'Otro nombre'}, format='json')
        self.assertEqual(renombrado.data['slug'], 'marco-normativo')

    def test_ficha_con_metadatos_del_catalogo(self):
        self.client.force_authenticate(self.instructor)
        response = self.client.patch(self.curso_url, {
            'resumen': 'Que es un contrato de seguro.',
            'nivel': 'basico',
            'categoria': 'normativo',
            'objetivos': ['  Leer una poliza  ', 'Calcular una prima'],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['categoria'], {'clave': 'normativo', 'nombre': 'Normativo'})
        self.assertEqual(response.data['objetivos'], ['Leer una poliza', 'Calcular una prima'])
        self.assertEqual(response.data['nivel'], 'basico')
        self.assertEqual(response.data['total_modulos'], 1)
        self.assertEqual(response.data['total_lecciones'], 2)
        for invalido in ({'nivel': 'experto'}, {'categoria': 'inventada'}, {'objetivos': ['  ']}):
            self.assertEqual(self.client.patch(self.curso_url, invalido, format='json').status_code, 400, invalido)

    def test_crud_modulos_y_orden_unico(self):
        self.client.force_authenticate(self.instructor)
        data = {'curso': str(self.curso.pk), 'titulo': 'Segundo bloque', 'orden': 2}
        response = self.client.post('/api/modulos/', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        url = f"/api/modulos/{response.data['id']}/"
        self.assertEqual(self.client.post('/api/modulos/', data, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'orden': 1}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'orden': 3}, format='json').status_code, 200)
        # Dos modulos del mismo curso pueden tener cada uno su leccion 1.
        segunda = self.client.post('/api/lecciones/', {
            'modulo': response.data['id'], 'titulo': 'Arranque', 'video': str(self.video.pk), 'orden': 1,
        }, format='json')
        self.assertEqual(segunda.status_code, 201, segunda.data)
        self.client.force_authenticate(self.otro_instructor)
        self.assertEqual(self.client.patch(url, {'titulo': 'Ajeno'}, format='json').status_code, 403)

    def test_borrar_modulo_recalcula_el_avance(self):
        self.inscribir()
        self.completar()
        self.client.force_authenticate(self.instructor)
        otro = self.client.post('/api/modulos/', {
            'curso': str(self.curso.pk), 'titulo': 'Extra', 'orden': 2,
        }, format='json')
        self.assertEqual(otro.status_code, 201, otro.data)
        suelta = self.client.post('/api/lecciones/', {
            'modulo': otro.data['id'], 'titulo': 'Suelta', 'video': str(self.video.pk), 'orden': 1,
        }, format='json')
        self.assertEqual(suelta.status_code, 201, suelta.data)
        self.assertEqual(str(Inscripcion.objects.get().porcentaje_avance), '33.33')
        # Borrar el modulo se lleva su leccion por cascada, y el avance del
        # alumno vuelve a medirse sobre lo que queda.
        self.assertEqual(self.client.delete(f"/api/modulos/{otro.data['id']}/").status_code, 204)
        self.assertEqual(str(Inscripcion.objects.get().porcentaje_avance), '50.00')
        self.assertFalse(Leccion.objects.filter(titulo='Suelta').exists())

    def test_no_mover_modulo_entre_cursos(self):
        self.client.force_authenticate(self.instructor)
        otro = Curso.objects.create(titulo='Otro curso', instructor=self.instructor)
        response = self.client.patch(f'/api/modulos/{self.modulo.pk}/', {'curso': str(otro.pk)}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_inscripcion_idempotente_y_solo_propia(self):
        response = self.client.post('/api/inscripciones/', {
            'curso': str(self.curso.pk), 'usuario': str(self.otro.pk),
            'completado': True, 'porcentaje_avance': 100,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data['usuario']), str(self.alumno.pk))
        self.assertEqual(response.data['porcentaje_avance'], '0.00')
        self.assertEqual(self.inscribir().status_code, 200)
        self.assertEqual(Inscripcion.objects.count(), 1)
        self.assertEqual(self.client.get(self.curso_url).status_code, 200)
        temario = self.client.get(self.curso_url + 'lecciones/').data
        self.assertEqual([len(modulo['lecciones']) for modulo in temario], [2])
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.get('/api/inscripciones/').data, [])
        self.assertEqual(self.client.get(f"/api/inscripciones/{response.data['id']}/").status_code, 404)

    def test_crud_cursos_y_roles(self):
        self.assertEqual(self.client.post('/api/cursos/', {'titulo': 'Nuevo'}, format='json').status_code, 403)
        self.client.force_authenticate(self.instructor)
        response = self.client.post('/api/cursos/', {'titulo': 'Nuevo'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        url = f"/api/cursos/{response.data['slug']}/"
        self.assertFalse(response.data['activo'])
        self.assertEqual(self.client.patch(url, {'categoria': 'tecnico'}, format='json').status_code, 200)
        self.assertEqual(self.client.put(url, {'titulo': 'Reemplazo', 'activo': True}, format='json').status_code, 200)
        self.client.force_authenticate(self.otro_instructor)
        self.assertEqual(self.client.patch(url, {'titulo': 'Ajeno'}, format='json').status_code, 404)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(url).status_code, 204)

    def test_admin_y_empresa_ven_borradores_y_asignan_instructor(self):
        for user in (self.admin, self.empresa):
            self.client.force_authenticate(user)
            response = self.client.post('/api/cursos/', {'titulo': 'Borrador', 'instructor': str(self.instructor.pk)}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(self.client.get(f"/api/cursos/{response.data['slug']}/").status_code, 200)
            self.assertIn(str(response.data['id']), [str(c['id']) for c in self.client.get('/api/cursos/').data])
        response = self.client.post('/api/cursos/', {'titulo': 'Error', 'instructor': str(self.alumno.pk)}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_instructor_no_puede_transferir_curso(self):
        self.client.force_authenticate(self.instructor)
        response = self.client.patch(self.curso_url, {'instructor': str(self.otro_instructor.pk)}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_crud_lecciones_y_orden_unico(self):
        self.client.force_authenticate(self.instructor)
        data = {'modulo': str(self.modulo.pk), 'titulo': 'Extra', 'video': str(self.video.pk), 'orden': 3}
        response = self.client.post('/api/lecciones/', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        url = f"/api/lecciones/{response.data['id']}/"
        self.assertEqual(self.client.post('/api/lecciones/', data, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'orden': 1}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'orden': 4, 'duracion': 90}, format='json').status_code, 200)
        data['orden'] = 5
        self.assertEqual(self.client.put(url, data, format='json').status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get('/api/lecciones/?curso=no-es-uuid').status_code, 400)

    def test_validar_video_y_propiedad_de_curso(self):
        self.client.force_authenticate(self.otro_instructor)
        data = {'modulo': str(self.modulo.pk), 'titulo': 'Error', 'video': str(self.video.pk), 'orden': 3}
        self.assertEqual(self.client.post('/api/lecciones/', data, format='json').status_code, 400)
        self.client.force_authenticate(self.instructor)
        ajeno = Video.objects.create(owner=self.otro_instructor, status='uploaded')
        VideoRendition.objects.create(video=ajeno, s3_key=f'videos/{ajeno.id}', status='uploaded')
        data['video'] = str(ajeno.pk)
        self.assertEqual(self.client.post('/api/lecciones/', data, format='json').status_code, 400)
        self.video.status = 'pending'
        self.video.save()
        data['video'] = str(self.video.pk)
        self.assertEqual(self.client.post('/api/lecciones/', data, format='json').status_code, 400)

    def test_no_mover_leccion_entre_cursos(self):
        self.client.force_authenticate(self.instructor)
        otro = Curso.objects.create(titulo='Otro', instructor=self.instructor)
        ajeno = Modulo.objects.create(curso=otro, titulo='Contenido', orden=1)
        self.assertEqual(self.client.patch(self.leccion_url, {'modulo': str(ajeno.pk)}, format='json').status_code, 400)

    def test_no_vincular_video_eliminado(self):
        self.client.force_authenticate(self.instructor)
        borrado = Video.objects.create(owner=self.instructor, status='uploaded', eliminado_en=timezone.now())
        VideoRendition.objects.create(video=borrado, s3_key=f'videos/{borrado.id}', status='uploaded')
        response = self.client.post('/api/lecciones/', {
            'modulo': str(self.modulo.pk), 'video': str(borrado.pk), 'titulo': 'Error', 'orden': 3,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.patch(self.leccion_url, {'video': str(borrado.pk)}, format='json').status_code, 400)

    def test_video_eliminado_durante_validacion_no_se_vincula(self):
        from core.api.curso_serializers import LeccionSerializer
        self.client.force_authenticate(self.instructor)
        nuevo = Video.objects.create(owner=self.instructor, status='uploaded')
        VideoRendition.objects.create(video=nuevo, s3_key=f'videos/{nuevo.id}', status='uploaded')
        validar = LeccionSerializer.validate

        def eliminar_despues_de_validar(serializer, attrs):
            result = validar(serializer, attrs)
            Video.objects.filter(pk=nuevo.pk).update(eliminado_en=timezone.now())
            return result

        with patch.object(LeccionSerializer, 'validate', eliminar_despues_de_validar):
            response = self.client.post('/api/lecciones/', {
                'modulo': str(self.modulo.pk), 'video': str(nuevo.pk), 'titulo': 'Carrera', 'orden': 3,
            }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Leccion.objects.filter(video=nuevo).exists())

    @patch('core.services.videos.playback_url', return_value='https://signed.example/video')
    def test_video_unlisted_del_curso_exige_inscripcion(self, sign):
        url = f'/api/videos/{self.video.pk}/'
        self.assertEqual(self.client.get(url + 'playback/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url + 'playback/').status_code, 404)
        sign.assert_not_called()
        self.client.force_authenticate(self.alumno)
        self.inscribir()
        self.assertEqual(self.client.get(url + 'playback/').status_code, 200)
        self.curso.activo = False
        self.curso.save()
        self.assertEqual(self.client.get(url + 'playback/').status_code, 404)
        self.assertEqual(self.completar().status_code, 404)
        self.assertEqual(self.client.post(self.curso_url + 'inscribir/').status_code, 404)

    def test_progreso_certificado_pdf_y_reintentos(self):
        self.inscribir()
        response = self.completar()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['inscripcion']['porcentaje_avance'], '50.00')
        self.assertFalse(CertificadoCurso.objects.exists())
        fecha = response.data['progreso']['fecha_completado']
        self.assertEqual(self.completar().data['progreso']['fecha_completado'], fecha)
        response = self.completar(self.segunda)
        self.assertEqual(response.data['inscripcion']['porcentaje_avance'], '100.00')
        self.assertTrue(response.data['inscripcion']['completado'])
        self.completar(self.segunda)
        self.assertEqual(CertificadoCurso.objects.count(), 1)
        certificado = CertificadoCurso.objects.get()
        self.assertTrue(bytes(certificado.archivo_pdf).startswith(b'%PDF-'))
        url = f'/api/certificados-cursos/{certificado.pk}/'
        response = self.client.get(url)
        self.assertTrue(response.data['archivo_pdf'].endswith(url + 'descargar/'))
        response = self.client.get(url + 'descargar/')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response.content, bytes(certificado.archivo_pdf))
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url + 'descargar/').status_code, 404)
        self.assertEqual(self.client.get('/api/progresos-lecciones/').data, [])
        self.assertEqual(self.client.get('/api/certificados-cursos/').data, [])

    def test_desmarcar_y_cambios_temario_recalculan(self):
        self.inscribir()
        self.completar()
        self.completar(self.segunda)
        codigo = CertificadoCurso.objects.get().codigo_certificado
        self.assertEqual(self.completar(visto=False).data['inscripcion']['porcentaje_avance'], '50.00')
        self.assertIsNone(ProgresoLeccion.objects.get(leccion=self.leccion).fecha_completado)
        self.client.force_authenticate(self.instructor)
        self.assertEqual(self.client.patch(self.leccion_url, {'activo': False}, format='json').status_code, 200)
        self.assertEqual(Inscripcion.objects.get().porcentaje_avance, 100)
        self.assertEqual(self.client.patch(self.leccion_url, {'activo': True}, format='json').status_code, 200)
        self.assertEqual(Inscripcion.objects.get().porcentaje_avance, 50)
        self.assertEqual(self.client.delete(self.leccion_url).status_code, 204)
        self.assertEqual(Inscripcion.objects.get().porcentaje_avance, 100)
        self.assertEqual(CertificadoCurso.objects.get().codigo_certificado, codigo)
        self.assertEqual(self.client.delete(f'/api/lecciones/{self.segunda.pk}/').status_code, 204)
        inscripcion = Inscripcion.objects.get()
        self.assertEqual(inscripcion.porcentaje_avance, 0)
        self.assertFalse(inscripcion.completado)

    def test_agregar_leccion_y_reemplazar_video_recalcula(self):
        self.inscribir()
        self.completar()
        self.completar(self.segunda)
        self.client.force_authenticate(self.instructor)
        response = self.client.post('/api/lecciones/', {
            'modulo': str(self.modulo.pk), 'titulo': 'Nueva', 'video': str(self.video.pk), 'orden': 3,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(Inscripcion.objects.get().porcentaje_avance), '66.66')
        nuevo = Video.objects.create(owner=self.instructor, status='uploaded')
        VideoRendition.objects.create(video=nuevo, s3_key=f'videos/{nuevo.pk}', status='uploaded')
        response = self.client.patch(self.leccion_url, {'video': str(nuevo.pk)}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProgresoLeccion.objects.get(leccion=self.leccion).visto)
        self.assertEqual(str(Inscripcion.objects.get().porcentaje_avance), '33.33')

    def test_curso_vacio_no_emite_certificado(self):
        Leccion.objects.filter(modulo__curso=self.curso).delete()
        response = self.inscribir()
        self.assertFalse(response.data['completado'])
        self.assertFalse(CertificadoCurso.objects.exists())

    def test_leccion_inactiva_no_cuenta_ni_admite_progreso(self):
        self.segunda.activo = False
        self.segunda.save()
        self.inscribir()
        self.assertEqual(self.completar(self.segunda).status_code, 404)
        self.assertEqual(self.completar().data['inscripcion']['porcentaje_avance'], '100.00')

    @patch('core.services.cursos.generar_pdf', side_effect=RuntimeError('Fallo de PDF'))
    def test_error_pdf_revierte_ultima_leccion(self, pdf):
        self.inscribir()
        self.completar()
        with self.assertRaises(RuntimeError):
            self.completar(self.segunda)
        self.assertEqual(Inscripcion.objects.get().porcentaje_avance, 50)
        self.assertFalse(ProgresoLeccion.objects.filter(leccion=self.segunda).exists())
        self.assertFalse(CertificadoCurso.objects.exists())

    def test_alumno_no_modifica_curso_ni_avance_directamente(self):
        response = self.inscribir()
        self.assertEqual(self.client.patch(self.curso_url, {'titulo': 'Alterado'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(self.leccion_url).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/inscripciones/{response.data['id']}/", {'porcentaje_avance': 100}, format='json').status_code, 405)
        self.assertEqual(self.client.post('/api/certificados-cursos/', {}, format='json').status_code, 405)

    def test_restriccion_bd_inscripcion_unica(self):
        self.inscribir()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inscripcion.objects.create(usuario=self.alumno, curso=self.curso)

    def test_esquema_openapi_incluye_flujo_de_progreso(self):
        response = self.client.get('/api/schema.json')
        self.assertEqual(response.status_code, 200)
        paths = response.data['paths']
        self.assertIn('/api/cursos/', paths)
        self.assertIn('/api/lecciones/{id}/progreso/', paths)
        operation = paths['/api/lecciones/{id}/progreso/']['post']
        self.assertEqual(operation['parameters'][0]['schema']['$ref'], '#/definitions/RegistrarProgreso')


class CursosConcurrentesTests(TransactionTestCase):
    def test_dos_ultimas_lecciones_simultaneas_emiten_un_solo_certificado(self):
        alumno = Usuario.objects.create(
            id=uuid.uuid4(), nombre_completo='Alumno concurrente',
            email=f'{uuid.uuid4()}@example.test', password_hash='!', estado='activo',
            creado_en=timezone.now(), actualizado_en=timezone.now(),
        )
        curso = Curso.objects.create(titulo='Concurrencia', instructor=alumno, activo=True)
        video = Video.objects.create(owner=alumno, status='uploaded')
        modulo = Modulo.objects.create(curso=curso, titulo='Contenido', orden=1)
        lecciones = [Leccion.objects.create(modulo=modulo, video=video, titulo=str(i), orden=i) for i in range(2)]
        Inscripcion.objects.create(curso=curso, usuario=alumno)
        inicio = Barrier(2)

        def completar(leccion):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(Usuario.objects.get(pk=alumno.pk))
                inicio.wait(timeout=10)
                response = client.post(f'/api/lecciones/{leccion.pk}/progreso/', {'visto': True}, format='json')
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            respuestas = list(executor.map(completar, lecciones))
        self.assertEqual(respuestas, [200, 200])
        inscripcion = Inscripcion.objects.get(curso=curso, usuario=alumno)
        self.assertTrue(inscripcion.completado)
        self.assertEqual(inscripcion.porcentaje_avance, 100)
        self.assertEqual(CertificadoCurso.objects.filter(curso=curso, usuario=alumno).count(), 1)
