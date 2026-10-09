from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    EstadoPostulacion,
    EventoPostulacion,
    ModalidadVacante,
    Postulacion,
    Vacante,
)
from core.services.postulaciones import registrar_evento
from tests.test_core import UsuariosDePruebaMixin


class PostulacionHistorialTest(UsuariosDePruebaMixin, TestCase):
    """
    Mover el proceso (`PATCH /api/postulaciones/{id}/`) y leer su línea de
    tiempo (`GET /api/postulaciones/{id}/historial/`).
    """

    @classmethod
    def setUpTestData(cls):
        ahora = timezone.now()

        cls.vacante = Vacante.objects.create(
            titulo="Consultor SAP Customer Checkout",
            modalidad=ModalidadVacante.HIBRIDO,
            creado_en=ahora,
            actualizado_en=ahora,
        )

        cls.usuario_aspirante, cls.aspirante = cls._crear_aspirante(
            "ana@ene8.com.mx", "TEST-HIS-001", "Ana Gómez", "aspirante", ahora
        )
        cls.usuario_otro, cls.otro_aspirante = cls._crear_aspirante(
            "luis@ene8.com.mx", "TEST-HIS-002", "Luis Pérez", "aspirante", ahora
        )

        cls.reclutador = cls._crear_usuario(
            "reclutador@ene8.com.mx", "Rita Ruiz", "reclutador", ahora
        )
        cls.solo_consulta = cls._crear_usuario(
            "consulta@ene8.com.mx", "Coni Consulta", "consulta", ahora
        )

        cls.registrada_en = ahora - timedelta(days=3)
        cls.postulacion = Postulacion.objects.create(
            aspirante=cls.aspirante,
            vacante=cls.vacante,
            estado=EstadoPostulacion.NUEVO,
            registrada_en=cls.registrada_en,
            ultima_actividad_en=cls.registrada_en,
        )
        registrar_evento(
            cls.postulacion,
            usuario=cls.usuario_aspirante,
            ocurrido_en=cls.registrada_en,
        )

        cls.ajena = Postulacion.objects.create(
            aspirante=cls.otro_aspirante,
            vacante=cls.vacante,
            estado=EstadoPostulacion.NUEVO,
            registrada_en=ahora,
            ultima_actividad_en=ahora,
        )

    def detalle(self, postulacion=None):
        return reverse(
            "api:postulacion-detail", args=[(postulacion or self.postulacion).id]
        )

    def historial(self, postulacion=None):
        return reverse(
            "api:postulacion-historial",
            args=[(postulacion or self.postulacion).id],
        )

    def mover(self, usuario=None, postulacion=None, **cuerpo):
        return self.cliente_de(usuario or self.reclutador).patch(
            self.detalle(postulacion), cuerpo, format="json"
        )

    # --- Mover el proceso -------------------------------------------------

    def test_el_reclutador_mueve_el_proceso(self):
        respuesta = self.mover(
            estado="revision", etapa="Entrevista técnica", progreso=40
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["estado"], "revision")
        self.assertEqual(respuesta.data["etapa"], "Entrevista técnica")
        self.assertEqual(respuesta.data["progreso"], 40)

        self.postulacion.refresh_from_db()
        self.assertEqual(self.postulacion.etapa, "Entrevista técnica")
        self.assertGreater(
            self.postulacion.ultima_actividad_en, self.registrada_en
        )

    def test_cada_movimiento_deja_un_evento(self):
        self.mover(estado="revision", etapa="Revisión de CV", progreso=20)
        self.mover(etapa="Entrevista técnica", progreso=50)

        eventos = list(self.postulacion.eventos.all())
        self.assertEqual(
            [(e.estado, e.etapa, e.progreso) for e in eventos],
            [
                ("nuevo", "Postulación recibida", 0),
                ("revision", "Revisión de CV", 20),
                ("revision", "Entrevista técnica", 50),
            ],
        )
        self.assertEqual(eventos[-1].registrado_por_id, self.reclutador.id)

    def test_un_patch_sin_cambios_no_ensucia_el_historial(self):
        respuesta = self.mover(estado="nuevo", progreso=0)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.postulacion.eventos.count(), 1)
        self.postulacion.refresh_from_db()
        self.assertEqual(self.postulacion.ultima_actividad_en, self.registrada_en)

    def test_rechaza_un_cuerpo_vacio(self):
        self.assertEqual(self.mover().status_code, 400)

    def test_rechaza_valores_fuera_de_rango(self):
        casos = (
            {"progreso": 101},
            {"progreso": -1},
            {"estado": "en_pausa"},
            {"etapa": "   "},
            {"etapa": "x" * 121},
        )
        for cuerpo in casos:
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.mover(**cuerpo).status_code, 400)

        self.assertEqual(self.postulacion.eventos.count(), 1)

    def test_ignora_campos_que_no_son_del_proceso(self):
        """Lo declarado por el aspirante no lo corrige el reclutador."""
        self.mover(etapa="Entrevista", ultimo_empleo="Otro", experiencia_meses=1)

        self.postulacion.refresh_from_db()
        self.assertEqual(self.postulacion.etapa, "Entrevista")
        self.assertIsNone(self.postulacion.ultimo_empleo)

    # --- Quién puede mover ------------------------------------------------

    def test_el_aspirante_no_mueve_su_propia_postulacion(self):
        respuesta = self.mover(usuario=self.usuario_aspirante, estado="contratado")

        self.assertEqual(respuesta.status_code, 403)
        self.postulacion.refresh_from_db()
        self.assertEqual(self.postulacion.estado, EstadoPostulacion.NUEVO)

    def test_el_rol_de_consulta_no_mueve_el_proceso(self):
        respuesta = self.mover(usuario=self.solo_consulta, estado="revision")

        self.assertEqual(respuesta.status_code, 403)

    # --- Historial --------------------------------------------------------

    def test_el_aspirante_lee_su_historial_en_orden(self):
        self.mover(estado="revision", etapa="Entrevista técnica", progreso=40)

        respuesta = self.cliente_de(self.usuario_aspirante).get(self.historial())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            [paso["etapa"] for paso in respuesta.data],
            ["Postulación recibida", "Entrevista técnica"],
        )

    def test_el_historial_no_dice_quien_movio_el_proceso(self):
        respuesta = self.cliente_de(self.usuario_aspirante).get(self.historial())

        self.assertEqual(
            set(respuesta.data[0]),
            {"id", "estado", "etapa", "progreso", "ocurrido_en"},
        )

    def test_el_historial_ajeno_responde_404(self):
        respuesta = self.cliente_de(self.usuario_aspirante).get(
            self.historial(self.ajena)
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_el_equipo_lee_el_historial_de_cualquiera(self):
        respuesta = self.cliente_de(self.solo_consulta).get(self.historial())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.data), 1)

    def test_borrar_la_postulacion_se_lleva_su_historial(self):
        postulacion_id = self.postulacion.id
        self.postulacion.delete()

        self.assertFalse(
            EventoPostulacion.objects.filter(postulacion_id=postulacion_id).exists()
        )


class PostulacionRetiroTest(UsuariosDePruebaMixin, TestCase):
    """El aspirante cancela su postulación: `POST /api/postulaciones/{id}/retirar/`."""

    @classmethod
    def setUpTestData(cls):
        ahora = timezone.now()

        cls.vacante = Vacante.objects.create(
            titulo="Consultor SAP Customer Checkout",
            modalidad=ModalidadVacante.HIBRIDO,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        cls.usuario_aspirante, cls.aspirante = cls._crear_aspirante(
            "ana@ene8.com.mx", "TEST-RET-001", "Ana Gómez", "aspirante", ahora
        )
        cls.usuario_otro, _ = cls._crear_aspirante(
            "luis@ene8.com.mx", "TEST-RET-002", "Luis Pérez", "aspirante", ahora
        )
        cls.reclutador = cls._crear_usuario(
            "reclutador@ene8.com.mx", "Rita Ruiz", "reclutador", ahora
        )

    def setUp(self):
        ahora = timezone.now()
        self.postulacion = Postulacion.objects.create(
            aspirante=self.aspirante,
            vacante=self.vacante,
            estado=EstadoPostulacion.REVISION,
            etapa="Entrevista técnica",
            progreso=40,
            registrada_en=ahora,
            ultima_actividad_en=ahora,
        )

    def retirar(self, usuario=None):
        return self.cliente_de(usuario or self.usuario_aspirante).post(
            reverse("api:postulacion-retirar", args=[self.postulacion.id])
        )

    def test_el_aspirante_retira_su_postulacion(self):
        respuesta = self.retirar()

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["estado"], "retirada")

        self.postulacion.refresh_from_db()
        self.assertEqual(self.postulacion.estado, EstadoPostulacion.RETIRADA)
        # El avance se conserva: dice hasta dónde llegó el proceso.
        self.assertEqual(self.postulacion.progreso, 40)

    def test_el_retiro_queda_en_el_historial(self):
        self.retirar()

        ultimo = self.postulacion.eventos.last()
        self.assertEqual(ultimo.estado, EstadoPostulacion.RETIRADA)
        self.assertEqual(ultimo.registrado_por_id, self.usuario_aspirante.id)

    def test_no_se_retira_dos_veces(self):
        self.retirar()

        respuesta = self.retirar()

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(self.postulacion.eventos.count(), 1)

    def test_no_se_retira_un_proceso_terminado(self):
        for final in (EstadoPostulacion.RECHAZADO, EstadoPostulacion.CONTRATADO):
            with self.subTest(estado=final):
                Postulacion.objects.filter(pk=self.postulacion.pk).update(estado=final)
                self.assertEqual(self.retirar().status_code, 400)

    def test_otro_aspirante_no_la_encuentra(self):
        self.assertEqual(self.retirar(self.usuario_otro).status_code, 404)

    def test_el_reclutador_no_retira_por_el_aspirante(self):
        """Él sí la encuentra (consultar-todas), pero no es su decisión."""
        self.assertEqual(self.retirar(self.reclutador).status_code, 403)
        self.postulacion.refresh_from_db()
        self.assertEqual(self.postulacion.estado, EstadoPostulacion.REVISION)

    def test_el_reclutador_no_mueve_una_retirada(self):
        self.retirar()

        respuesta = self.cliente_de(self.reclutador).patch(
            reverse("api:postulacion-detail", args=[self.postulacion.id]),
            {"estado": "revision"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)
        self.postulacion.refresh_from_db()
        self.assertEqual(self.postulacion.estado, EstadoPostulacion.RETIRADA)

    def test_el_reclutador_no_asigna_retirada(self):
        respuesta = self.cliente_de(self.reclutador).patch(
            reverse("api:postulacion-detail", args=[self.postulacion.id]),
            {"estado": "retirada"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)
