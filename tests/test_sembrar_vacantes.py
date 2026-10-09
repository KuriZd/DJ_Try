from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.management.commands.sembrar_vacantes import VACANTES, vacantes_de_ejemplo
from core.models import (
    EstadoPostulacion,
    EstadoVacante,
    ModalidadVacante,
    Postulacion,
    Vacante,
)
from tests.test_core import UsuariosDePruebaMixin


def sembrar(*args):
    salida = StringIO()
    call_command('sembrar_vacantes', *args, stdout=salida)
    return salida.getvalue()


def titulos_publicos():
    datos = APIClient().get('/api/vacantes/').data
    filas = datos['results'] if isinstance(datos, dict) else datos
    return {fila['titulo'] for fila in filas}


@override_settings(DEBUG=True)
class SembrarVacantesTests(UsuariosDePruebaMixin, TestCase):
    def test_crea_todas_las_vacantes_de_la_lista(self):
        self.assertIn(f'creadas: {len(VACANTES)}', sembrar())
        self.assertEqual(vacantes_de_ejemplo().count(), len(VACANTES))

    def test_el_listado_publico_solo_ofrece_las_abiertas(self):
        sembrar()
        publicas = titulos_publicos()

        self.assertIn('Ingeniero(a) de Datos', publicas)
        self.assertIn('Diseñador(a) UX/UI', publicas)
        for oculta in (
            'Ingeniero(a) DevOps',  # borrador
            'Analista de Inteligencia de Negocios',  # pausada
            'Técnico(a) de Campo',  # cerrada
            'Consultor(a) de Ciberseguridad',  # publicada pero vencida
        ):
            with self.subTest(oculta=oculta):
                self.assertNotIn(oculta, publicas)

    def test_cubre_los_casos_de_la_tarjeta(self):
        sembrar()
        ejemplo = vacantes_de_ejemplo()

        self.assertTrue(ejemplo.filter(email_contacto__isnull=True).exists())
        self.assertTrue(
            ejemplo.filter(
                duracion_min_semanas__isnull=True, duracion_max_semanas__isnull=True
            ).exists()
        )
        self.assertTrue(any(len(v.etiquetas) > 4 for v in ejemplo))
        self.assertTrue(
            ejemplo.filter(estado=EstadoVacante.PUBLICADA, cierra_en__gt=timezone.now()).exists()
        )

    def test_repetirlo_no_duplica(self):
        sembrar()
        self.assertIn('creadas: 0', sembrar())
        self.assertEqual(vacantes_de_ejemplo().count(), len(VACANTES))

    def test_limpiar_no_toca_vacantes_reales(self):
        ahora = timezone.now()
        real = Vacante.objects.create(
            titulo='Ingeniero(a) de Datos',  # mismo título, otra empresa
            empresa='Empresa real',
            modalidad=ModalidadVacante.REMOTO,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        sembrar()

        sembrar('--limpiar')

        self.assertFalse(vacantes_de_ejemplo().exists())
        self.assertTrue(Vacante.objects.filter(pk=real.pk).exists())

    def test_conserva_las_que_tienen_postulaciones(self):
        sembrar()
        ahora = timezone.now()
        _, aspirante = self._crear_aspirante(
            'ana@ene8.com.mx', 'TEST-SEM-001', 'Ana Gómez', 'aspirante', ahora
        )
        con_postulacion = vacantes_de_ejemplo().get(titulo='Ingeniero(a) de Datos')
        Postulacion.objects.create(
            aspirante=aspirante,
            vacante=con_postulacion,
            estado=EstadoPostulacion.NUEVO,
            registrada_en=ahora,
            ultima_actividad_en=ahora,
        )

        salida = sembrar('--reiniciar')

        self.assertIn('Se conservaron', salida)
        self.assertIn(f'creadas: {len(VACANTES) - 1}', salida)
        self.assertEqual(vacantes_de_ejemplo().count(), len(VACANTES))
        self.assertTrue(Vacante.objects.filter(pk=con_postulacion.pk).exists())

    @override_settings(DEBUG=False)
    def test_se_niega_fuera_de_desarrollo(self):
        with self.assertRaisesMessage(CommandError, 'DEBUG'):
            sembrar()
        self.assertFalse(vacantes_de_ejemplo().exists())
