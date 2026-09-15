import base64
import re
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.core import mail
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Aspirante, Certificado, PerfilProfesional, PlantillaCertificado,
    Postulacion, Rol, Usuario, UsuarioRol, Vacante,
)
from core.services import certificados


def crear_usuario(rol):
    user = Usuario.objects.create(
        id=uuid.uuid4(), nombre_completo='Persona ' + rol,
        email=f'{uuid.uuid4()}@example.test', password_hash='!', estado='activo',
        creado_en=timezone.now(), actualizado_en=timezone.now(),
    )
    UsuarioRol.objects.create(usuario=user, rol=Rol.objects.get(clave=rol), asignado_en=timezone.now())
    return user


def crear_postulacion(usuario):
    ahora = timezone.now()
    aspirante = Aspirante.objects.create(
        id='ASP-' + uuid.uuid4().hex[:20], matricula=uuid.uuid4().hex, usuario=usuario,
        nombre_completo='María <Prueba> & López', email=usuario.email,
        registrado_en=ahora, actualizado_en=ahora,
    )
    PerfilProfesional.objects.create(
        aspirante=aspirante, nivel_educativo='Licenciatura', institucion='Universidad de prueba',
        empresas=['Empresa A'], habilidades_tecnicas=['Python'], resultado_evaluaciones='Aprobado',
        actualizado_en=ahora,
    )
    vacante = Vacante.objects.create(
        titulo='Vacante certificados', modalidad='remoto', estado='publicada',
        creado_en=ahora, actualizado_en=ahora,
    )
    return Postulacion.objects.create(
        aspirante=aspirante, vacante=vacante, registrada_en=ahora, ultima_actividad_en=ahora,
    )


def datos_emision(postulacion):
    return {
        'postulacion': postulacion.pk, 'tipo': 'participacion',
        'plantilla_id': 'TPL-PART-01', 'plantilla_version': '1.0',
        'resultado': 'Participación acreditada', 'justificacion_manual': 'Evidencia revisada por el responsable.',
        'autoridad_emisora': 'Área de reclutamiento', 'observaciones_internas': 'Nota privada',
    }


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_REDIRIGIR_A='')
class CertificadosAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = crear_usuario('administrador')
        cls.empresa = crear_usuario('empresa')
        cls.alumno = crear_usuario('aspirante')
        cls.otro = crear_usuario('aspirante')
        cls.consulta = crear_usuario('consulta')
        cls.postulacion = crear_postulacion(cls.alumno)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def emitir(self):
        response = self.client.post('/api/certificados/', datos_emision(self.postulacion), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_emision_snapshot_pdf_y_folio_inmutables(self):
        data = self.emitir()
        certificado = Certificado.objects.get(pk=data['id'])
        self.assertTrue(bytes(certificado.archivo_pdf).startswith(b'%PDF-'))
        self.assertEqual(data['postulacion_folio'], self.postulacion.folio)
        snapshot = data['aspirante_snapshot']
        self.assertEqual(snapshot['academicos']['institucion'], 'Universidad de prueba')
        self.assertEqual(snapshot['laborales']['empresas'], ['Empresa A'])
        self.assertEqual(snapshot['competencias']['habilidades_tecnicas'], ['Python'])
        pdf = bytes(certificado.archivo_pdf)
        # Leer los flujos de texto del PDF real que genera ReportLab.
        contenido = b'\n'.join(
            zlib.decompress(base64.a85decode(stream.strip(), adobe=True))
            for stream in re.findall(rb'stream\r?\n(.*?)endstream', pdf, re.S)
        )
        for valor in (self.postulacion.folio, certificado.folio, certificado.codigo_verificacion,
                      'Universidad de prueba', 'Empresa A', 'Python', 'Aprobado'):
            self.assertIn(valor.encode('ascii'), contenido)
        self.assertNotIn(b'Nota privada', contenido)
        Aspirante.objects.filter(pk=self.postulacion.aspirante_id).update(nombre_completo='Nombre modificado')
        PerfilProfesional.objects.filter(aspirante_id=self.postulacion.aspirante_id).update(institucion='Otra')
        PlantillaCertificado.objects.filter(id='TPL-PART-01', version='1.0').update(texto_institucional='Modificado')
        url = '/api/certificados/' + data['id'] + '/'
        self.assertEqual(self.client.get(url).data['aspirante_snapshot'], snapshot)
        response = self.client.get(url + 'descargar/')
        self.assertEqual(response.content, pdf)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(certificado.historial.count(), 1)
        self.assertEqual(self.client.patch(url, {'resultado': 'Alterado'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_aislamiento_y_permisos(self):
        data = self.emitir()
        url = '/api/certificados/' + data['id'] + '/'
        self.client.force_authenticate(self.alumno)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('observaciones_internas', response.data)
        self.assertNotIn('justificacion_manual', response.data)
        self.assertEqual(self.client.get(url + 'descargar/').status_code, 200)
        for accion in ('cancelar', 'revocar', 'enviar'):
            self.assertEqual(self.client.post(url + accion + '/', {'motivo': 'Intento'}, format='json').status_code, 403)
        self.assertEqual(self.client.get(url + 'historial/').status_code, 403)
        self.assertEqual(self.client.post('/api/certificados/', datos_emision(self.postulacion), format='json').status_code, 403)
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.get('/api/certificados/').data['count'], 0)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url + 'descargar/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/certificados/').status_code, 401)
        self.client.force_authenticate(self.consulta)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url + 'revocar/', {'motivo': 'Intento'}, format='json').status_code, 403)

    def test_validacion_y_duplicados(self):
        payload = datos_emision(self.postulacion)
        for campo, valor in [('justificacion_manual', ' '), ('postulacion', 99999999), ('tipo', 'inexistente'),
                             ('plantilla_version', '999'), ('resultado', ''), ('autoridad_emisora', '')]:
            response = self.client.post('/api/certificados/', {**payload, campo: valor}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Certificado.objects.exists())
        self.emitir()
        response = self.client.post('/api/certificados/', payload, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Certificado.objects.count(), 1)

    def test_fallo_pdf_no_deja_certificado_ni_historial(self):
        with patch('core.services.certificados.generar_pdf', side_effect=RuntimeError('Error de render')):
            with self.assertRaises(RuntimeError):
                certificados.emitir(datos_emision(self.postulacion), self.admin)
        self.assertFalse(Certificado.objects.filter(postulacion=self.postulacion).exists())

    def test_revocacion_idempotente_bloquea_pdf_y_permite_nueva_emision(self):
        data = self.emitir()
        url = '/api/certificados/' + data['id'] + '/'
        self.assertEqual(self.client.post(url + 'revocar/', {}, format='json').status_code, 400)
        for _ in range(2):
            response = self.client.post(url + 'revocar/', {'motivo': 'Corrección de evidencia'}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertIsNone(response.data['archivo_pdf'])
        self.assertEqual(self.client.get(url + 'descargar/').status_code, 409)
        self.assertEqual(self.client.post(url + 'enviar/', {}, format='json').status_code, 409)
        self.assertEqual(self.client.post(url + 'cancelar/', {'motivo': 'Otro'}, format='json').status_code, 409)
        self.assertEqual(len(self.client.get(url + 'historial/').data), 2)
        self.assertNotEqual(self.emitir()['id'], data['id'])

    def test_cancelacion_y_empresa(self):
        self.client.force_authenticate(self.empresa)
        data = self.emitir()
        response = self.client.post('/api/certificados/' + data['id'] + '/cancelar/', {'motivo': 'Error'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['estado'], 'cancelado')

    def test_envio_reenvio_y_fallo_persistido(self):
        data = self.emitir()
        url = '/api/certificados/' + data['id'] + '/'
        with patch('core.services.certificados.EmailMessage.send', side_effect=RuntimeError('secreto SMTP')):
            response = self.client.post(url + 'enviar/', {}, format='json')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data['estado'], 'fallido')
        self.assertNotIn('secreto', response.data['mensaje_error'])
        self.assertEqual(self.client.get(url).data['estado'], 'emitido')
        with override_settings(EMAIL_REDIRIGIR_A='buzon@example.test'):
            response = self.client.post(url + 'enviar/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(mail.outbox[0].to, ['buzon@example.test'])
        self.assertEqual(response.data['destinatario_email'], self.alumno.email)
        self.assertEqual(len(mail.outbox[0].attachments), 1)
        self.assertEqual(self.client.get(url).data['estado'], 'enviado')
        self.assertEqual(self.client.post(url + 'enviar/', {}, format='json').status_code, 200)
        self.assertEqual(self.client.get(url).data['estado'], 'reenviado')
        self.assertEqual(len(self.client.get(url + 'envios/').data), 3)

    def test_catalogos_versiones_y_filtros(self):
        data = self.emitir()
        response = self.client.get('/api/certificados/', {'search': self.postulacion.folio, 'page_size': 1})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], data['id'])
        for query in ({'estado': 'invalido'}, {'postulacion': 'x'}, {'postulacion': '9' * 40}):
            self.assertEqual(self.client.get('/api/certificados/', query).status_code, 400)
        self.assertEqual(self.client.get('/api/tipos-certificado/').status_code, 200)
        payload = {'id': 'TPL-NUEVA', 'version': '2.0', 'tipo': 'participacion',
                   'nombre': 'Nueva versión', 'texto_institucional': 'Texto revisado', 'activa': True}
        response = self.client.post('/api/plantillas-certificado/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.post('/api/plantillas-certificado/', payload, format='json').status_code, 400)
        url = '/api/plantillas-certificado/TPL-NUEVA/versiones/2.0/'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.patch(url, {'activa': False}, format='json').status_code, 200)
        self.client.force_authenticate(self.alumno)
        self.assertEqual(self.client.patch(url, {'activa': True}, format='json').status_code, 403)

    def test_verificacion_publica_por_codigo_y_por_folio(self):
        data = self.emitir()
        certificado = Certificado.objects.get(pk=data['id'])
        publico = APIClient()

        for clave in (certificado.codigo_verificacion, certificado.folio.lower()):
            response = publico.get('/api/certificados/verificar/' + clave + '/')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['folio'], certificado.folio)
            self.assertTrue(response.data['vigente'])
            self.assertEqual(response.data['titular'], 'María <Prueba> & López')
            self.assertEqual(response.data['tipo_nombre'], 'Participación')
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            # Quien verifica es un tercero con un papel en la mano, no alguien
            # con acceso al padrón: nada del expediente ni de lo interno.
            for campo in ('aspirante_snapshot', 'aspirante', 'observaciones_internas',
                          'justificacion_manual', 'archivo_pdf', 'postulacion'):
                self.assertNotIn(campo, response.data)

        self.assertEqual(publico.get('/api/certificados/verificar/NO-EXISTE/').status_code, 404)

    def test_verificacion_publica_delata_el_certificado_retirado(self):
        data = self.emitir()
        codigo = Certificado.objects.get(pk=data['id']).codigo_verificacion
        url = '/api/certificados/' + data['id'] + '/revocar/'
        self.assertEqual(
            self.client.post(url, {'motivo': 'Evidencia insuficiente'}, format='json').status_code, 200,
        )

        # Darlo por inexistente dejaría pasar por bueno un documento retirado.
        response = APIClient().get('/api/certificados/verificar/' + codigo + '/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['estado'], 'revocado')
        self.assertFalse(response.data['vigente'])
        self.assertIsNotNone(response.data['revocado_en'])
        # El motivo es asunto de la institución y del titular, no del público.
        self.assertNotIn('motivo_revocacion', response.data)

    def test_schema_incluye_certificados(self):
        response = self.client.get('/api/schema.json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('/api/certificados/', response.data['paths'])
        self.assertIn('/api/certificados/verificar/{codigo}/', response.data['paths'])


class CertificadosConcurrenciaTests(TransactionTestCase):
    available_apps = ['core']

    def test_emisiones_simultaneas_generan_un_certificado(self):
        # TransactionTestCase vacía las tablas al terminar; usar los roles sembrados.
        admin = crear_usuario('administrador')
        postulacion = crear_postulacion(crear_usuario('aspirante'))
        barrera = Barrier(2)

        def intento():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                try:
                    certificados.emitir(datos_emision(postulacion), admin)
                    return 'emitido'
                except certificados.ConflictoCertificado:
                    return 'conflicto'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(lambda _: intento(), range(2)))
        self.assertCountEqual(resultados, ['emitido', 'conflicto'])
        self.assertEqual(Certificado.objects.filter(postulacion=postulacion).count(), 1)
