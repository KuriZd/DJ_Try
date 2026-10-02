from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core.management.commands.sembrar_muro import DOMINIO, PUBLICACIONES
from core.models import Comentario, EstadoReporte, Publicacion, Reaccion, Reporte, Usuario
from tests.test_publicaciones import crear_usuario


def sembrar(*args):
    salida = StringIO()
    call_command('sembrar_muro', *args, stdout=salida)
    return salida.getvalue()


@override_settings(DEBUG=True)
class SembrarMuroTests(TestCase):
    def test_siembra_siete_publicaciones_visibles_en_el_muro(self):
        self.assertIn('7 publicaciones', sembrar())
        self.assertEqual(len(PUBLICACIONES), 7)

        muro = APIClient().get('/api/publicaciones/').data['results']
        self.assertEqual(len(muro), 7)
        # De la más reciente a la más antigua, y con interacciones.
        self.assertTrue(muro[0]['cuerpo'].startswith('Hoy cerramos el taller'))
        self.assertEqual((muro[0]['total_reacciones'], muro[0]['total_comentarios']), (4, 2))
        self.assertTrue(any(p['fecha_edicion'] for p in muro))

    def test_las_cuentas_tienen_correo_verificado_y_no_sirven_para_entrar(self):
        sembrar()
        for usuario in Usuario.objects.filter(email__endswith=DOMINIO):
            self.assertIsNotNone(usuario.email_verificado_en)
            self.assertTrue(usuario.password_hash.startswith('!'))

    def test_deja_un_caso_en_la_cola_de_moderacion(self):
        sembrar()
        self.assertEqual(Reporte.objects.filter(estado=EstadoReporte.PENDIENTE).count(), 2)
        self.assertEqual(Reporte.objects.values('publicacion').distinct().count(), 1)

    def test_no_siembra_dos_veces_sin_reiniciar(self):
        sembrar()
        with self.assertRaisesMessage(CommandError, '--reiniciar'):
            sembrar()
        sembrar('--reiniciar')
        self.assertEqual(Publicacion.objects.count(), 7)

    def test_limpiar_no_toca_lo_de_cuentas_reales(self):
        real = crear_usuario('aspirante')
        propia = Publicacion.objects.create(autor=real, cuerpo='hola')
        sembrar()
        Comentario.objects.create(publicacion=propia, autor=Usuario.objects.get(email=f'jorge{DOMINIO}'), cuerpo='x')

        self.assertIn('7 publicaciones', sembrar('--limpiar'))

        self.assertEqual(list(Publicacion.objects.values_list('cuerpo', flat=True)), ['hola'])
        self.assertFalse(Usuario.objects.filter(email__endswith=DOMINIO).exists())
        self.assertFalse(Comentario.objects.exists())
        self.assertFalse(Reaccion.objects.exists())
        self.assertFalse(Reporte.objects.exists())

    @override_settings(DEBUG=False)
    def test_se_niega_fuera_de_desarrollo(self):
        with self.assertRaisesMessage(CommandError, 'DEBUG'):
            sembrar()
        self.assertFalse(Publicacion.objects.exists())
