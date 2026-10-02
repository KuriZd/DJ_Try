"""Moderacion del muro: reportes, cola de moderacion y rastro en auditoria."""

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Auditoria, Comentario, EstadoReporte, Publicacion, Reporte
from tests.test_publicaciones import crear_usuario


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.autor = crear_usuario('aspirante')
        cls.lector = crear_usuario('aspirante')
        cls.otro = crear_usuario('aspirante')
        cls.admin = crear_usuario('administrador')

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.publicacion = Publicacion.objects.create(autor=self.autor, cuerpo='Compra seguidores aquí')
        self.comentario = Comentario.objects.create(
            publicacion=self.publicacion, autor=self.otro, cuerpo='Ofensa',
        )
        self.url = f'/api/publicaciones/{self.publicacion.pk}/'
        self.url_comentario = f'/api/comentarios/{self.comentario.pk}/'

    def reportar(self, user, url=None, motivo='spam', detalle=''):
        self.client.force_authenticate(user)
        return self.client.post((url or self.url) + 'reportar/',
                                {'motivo': motivo, 'detalle': detalle}, format='json')

    def resolver(self, reporte, accion, user=None):
        self.client.force_authenticate(user or self.admin)
        return self.client.post(f'/api/reportes/{reporte.pk}/resolver/', {'accion': accion}, format='json')


class ReportarTests(Base):
    def test_reportar_pide_sesion_pero_no_correo_verificado(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url + 'reportar/', {'motivo': 'spam'}).status_code, 401)
        sin_verificar = crear_usuario('aspirante', verificado=False)
        self.assertEqual(self.reportar(sin_verificar).status_code, 201)

    def test_guarda_una_copia_de_lo_reportado(self):
        respuesta = self.reportar(self.lector, detalle='Es publicidad')
        self.assertEqual(respuesta.status_code, 201)
        self.assertNotIn('total', str(respuesta.data))
        reporte = Reporte.objects.get()
        self.assertEqual((reporte.cuerpo_reportado, reporte.autor_reportado_id), (
            'Compra seguidores aquí', self.autor.pk))
        self.assertEqual(reporte.detalle, 'Es publicidad')

    def test_reportar_dos_veces_no_duplica(self):
        self.assertEqual(self.reportar(self.lector).status_code, 201)
        self.assertEqual(self.reportar(self.lector).status_code, 200)
        self.assertEqual(Reporte.objects.count(), 1)

    def test_no_se_reporta_lo_propio(self):
        self.assertEqual(self.reportar(self.autor).status_code, 400)

    def test_valida_motivo_y_detalle(self):
        self.assertEqual(self.reportar(self.lector, motivo='aburrido').status_code, 400)
        self.assertEqual(self.reportar(self.lector, detalle='x' * 501).status_code, 400)

    def test_se_reporta_un_comentario(self):
        self.assertEqual(self.reportar(self.lector, url=self.url_comentario).status_code, 201)
        self.assertEqual(Reporte.objects.get().comentario_id, self.comentario.pk)

    def test_limite_de_reportes_por_cuenta(self):
        publicaciones = [Publicacion.objects.create(autor=self.autor, cuerpo=f'p{i}') for i in range(21)]
        codigos = [self.reportar(self.lector, url=f'/api/publicaciones/{p.pk}/').status_code
                   for p in publicaciones]
        self.assertEqual(codigos[:20], [201] * 20)
        self.assertEqual(codigos[20], 429)


class ColaModeracionTests(Base):
    def test_solo_moderacion_ve_la_cola(self):
        self.reportar(self.lector)
        self.client.force_authenticate(self.lector)
        self.assertEqual(self.client.get('/api/reportes/').status_code, 403)
        self.client.force_authenticate(self.admin)
        cola = self.client.get('/api/reportes/').data['results']
        self.assertEqual(len(cola), 1)
        self.assertEqual(cola[0]['motivo'], {'clave': 'spam', 'nombre': 'Spam o publicidad'})
        self.assertEqual(cola[0]['tipo'], 'publicacion')
        self.assertEqual(cola[0]['reportado_por']['id'], str(self.lector.pk))

    def test_el_reporte_de_un_comentario_lleva_a_su_publicacion(self):
        self.reportar(self.lector, url=self.url_comentario)
        self.client.force_authenticate(self.admin)
        caso = self.client.get('/api/reportes/').data['results'][0]
        self.assertEqual((caso['tipo'], caso['publicacion_id']), ('comentario', str(self.publicacion.pk)))

    def test_filtra_por_estado_y_valida(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/reportes/?estado=raro').status_code, 400)
        self.assertEqual(self.client.get('/api/reportes/?estado=descartado').data['results'], [])

    def test_eliminar_borra_cierra_todos_y_audita(self):
        self.reportar(self.lector)
        self.reportar(self.otro, motivo='suplantacion')
        primero = Reporte.objects.order_by('creado_en').first()

        respuesta = self.resolver(primero, 'eliminar')
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data['estado'], 'eliminado')
        self.assertFalse(Publicacion.objects.filter(pk=self.publicacion.pk).exists())
        self.assertEqual(
            set(Reporte.objects.values_list('estado', 'resuelto_por')),
            {('eliminado', self.admin.pk)},
        )
        # El caso sigue legible: la copia sobrevive al contenido.
        self.assertEqual(Reporte.objects.first().cuerpo_reportado, 'Compra seguidores aquí')

        registro = Auditoria.objects.get(accion='eliminar')
        self.assertEqual((registro.entidad, registro.entidad_id, registro.usuario_id),
                         ('publicacion', str(self.publicacion.pk), self.admin.pk))
        self.assertEqual(registro.datos_anteriores['cuerpo'], 'Compra seguidores aquí')
        self.assertEqual(registro.datos_anteriores['motivo'], 'reporte')

    def test_descartar_deja_el_contenido_y_audita(self):
        self.reportar(self.lector)
        self.reportar(self.otro)
        respuesta = self.resolver(Reporte.objects.first(), 'descartar')

        self.assertEqual(respuesta.data['estado'], 'descartado')
        self.assertTrue(Publicacion.objects.filter(pk=self.publicacion.pk).exists())
        self.assertFalse(Reporte.objects.filter(estado=EstadoReporte.PENDIENTE).exists())
        registro = Auditoria.objects.get(accion='descartar_reporte')
        self.assertEqual(registro.datos_nuevos['reportes_cerrados'], 2)

    def test_un_reporte_resuelto_no_se_vuelve_a_resolver(self):
        self.reportar(self.lector)
        reporte = Reporte.objects.get()
        self.resolver(reporte, 'descartar')
        self.assertEqual(self.resolver(reporte, 'eliminar').status_code, 409)

    def test_resolver_pide_moderacion(self):
        self.reportar(self.lector)
        self.assertEqual(self.resolver(Reporte.objects.get(), 'descartar', user=self.lector).status_code, 403)


class RastroTests(Base):
    def test_lo_que_cada_quien_borra_de_si_mismo_no_se_audita(self):
        self.reportar(self.lector)
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.delete(self.url).status_code, 204)

        self.assertFalse(Auditoria.objects.exists())
        # Pero sus reportes no quedan colgando, tampoco los de sus comentarios.
        self.assertEqual(Reporte.objects.get().estado, EstadoReporte.ELIMINADO)

    def test_borrar_una_publicacion_cierra_los_reportes_de_sus_comentarios(self):
        self.reportar(self.lector, url=self.url_comentario)
        self.client.force_authenticate(self.autor)
        self.client.delete(self.url)
        self.assertEqual(Reporte.objects.get().estado, EstadoReporte.ELIMINADO)

    def test_moderar_una_publicacion_ajena_queda_auditado(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        registro = Auditoria.objects.get()
        self.assertEqual(registro.datos_anteriores['motivo'], 'moderacion')
        self.assertEqual(registro.datos_anteriores['autor_id'], str(self.autor.pk))

    def test_el_autor_de_la_publicacion_borrando_un_comentario_ajeno_queda_auditado(self):
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.delete(self.url_comentario).status_code, 204)
        registro = Auditoria.objects.get()
        self.assertEqual((registro.entidad, registro.datos_anteriores['motivo']),
                         ('comentario', 'autor_publicacion'))
        self.assertEqual(registro.datos_anteriores['publicacion_id'], str(self.publicacion.pk))

    def test_borrar_el_comentario_propio_no_se_audita(self):
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.delete(self.url_comentario).status_code, 204)
        self.assertFalse(Auditoria.objects.exists())
