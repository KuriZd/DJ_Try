import uuid
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Aspirante,
    Empresa,
    EstadoPostulacion,
    EstadoUsuario,
    EstadoVacante,
    ModalidadVacante,
    PerfilProfesional,
    Postulacion,
    Rol,
    Usuario,
    UsuarioRol,
    Vacante,
)


class EmpresaPerfilTest(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.get(email__iexact="KuriZd@empresa.com")
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.usuario)

    def test_mi_perfil_incluye_la_empresa(self):
        respuesta = self.cliente.get(reverse("api:usuario-actual"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.data["empresa"]["razon_social"],
            "Empresa Demo, S.A. de C.V.",
        )
        self.assertIsNone(respuesta.data["aspirante"])

    def test_la_empresa_actualiza_su_informacion(self):
        respuesta = self.cliente.patch(
            reverse("api:usuario-actual"),
            {
                "nombre_comercial": "KuriZd Talento",
                "rfc": "ABC010203AB1",
                "telefono": "5555551234",
                "sector": "Tecnología",
            },
            format="json",
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["empresa"]["rfc"], "ABC010203AB1")
        empresa = Empresa.objects.get(usuario=self.usuario)
        self.assertEqual(empresa.nombre_comercial, "KuriZd Talento")
        self.assertEqual(empresa.telefono, "5555551234")


class ApiRoutesTest(SimpleTestCase):
    def test_rutas_conservadas(self):
        nombres = (
            "api:root",
            "api:health",
            "api:login",
            "api:refresh",
            "api:logout",
            "api:usuario-actual",
            "api:cambiar-password",
            "api:aspirante-list",
            "api:postulacion-list",
            "api:vacante-list",
            "api:vacante-admin-list",
        )

        for nombre in nombres:
            with self.subTest(nombre=nombre):
                self.assertTrue(reverse(nombre).startswith("/api/"))

    def test_rutas_retiradas(self):
        for ruta in (
            "/api/convocatorias/",
            "/api/certificados/",
        ):
            with self.subTest(ruta=ruta):
                with self.assertRaises(Resolver404):
                    resolve(ruta)


class VacantePublicaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        ahora = timezone.now()
        comunes = {
            "modalidad": ModalidadVacante.HIBRIDO,
            "creado_en": ahora,
            "actualizado_en": ahora,
        }
        cls.publicada = Vacante.objects.create(
            titulo="Vacante pública de prueba",
            empresa="Ene8",
            departamento="Consultoría SAP",
            descripcion="Resumen público",
            estado=EstadoVacante.PUBLICADA,
            publicada_en=ahora,
            contratacion="Por proyecto",
            duracion_min_semanas=6,
            duracion_max_semanas=10,
            email_contacto="recursoshumanos@ene8.com.mx",
            etiquetas=["SAP CCO"],
            requisitos=["Experiencia en SAP CCO"],
            **comunes,
        )
        cls.borrador = Vacante.objects.create(
            titulo="Vacante privada de prueba",
            estado=EstadoVacante.BORRADOR,
            **comunes,
        )
        cls.vencida = Vacante.objects.create(
            titulo="Vacante vencida de prueba",
            estado=EstadoVacante.PUBLICADA,
            publicada_en=ahora - timedelta(days=2),
            cierra_en=ahora - timedelta(days=1),
            **comunes,
        )

    def test_lista_es_publica_y_filtra_vacantes_no_visibles(self):
        respuesta = APIClient().get(reverse("api:vacante-list"))

        self.assertEqual(respuesta.status_code, 200)
        ids = {fila["id"] for fila in respuesta.data}
        self.assertIn(self.publicada.id, ids)
        self.assertNotIn(self.borrador.id, ids)
        self.assertNotIn(self.vencida.id, ids)

    def test_card_tiene_la_forma_esperada(self):
        respuesta = APIClient().get(
            reverse("api:vacante-detail", args=[self.publicada.id])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["area"], "Consultoría SAP")
        self.assertEqual(respuesta.data["resumen"], "Resumen público")
        self.assertEqual(respuesta.data["modalidad"], "Híbrida")
        self.assertEqual(
            respuesta.data["duracion_semanas"], {"min": 6, "max": 10}
        )

    def test_detalle_no_publicado_responde_404(self):
        respuesta = APIClient().get(
            reverse("api:vacante-detail", args=[self.borrador.id])
        )

        self.assertEqual(respuesta.status_code, 404)


class VacanteAdminTest(TestCase):
    """
    Alta y edición de vacantes desde el panel interno.

    Los roles y permisos vienen de database/seed.sql, que la migración inicial
    carga también en la base de pruebas.
    """

    @classmethod
    def setUpTestData(cls):
        ahora = timezone.now()
        cls.reclutador = cls._crear_usuario(
            "rec@ene8.com.mx", "Rita Ruiz", "reclutador", ahora
        )
        cls.aspirante = cls._crear_usuario(
            "asp@ene8.com.mx", "Ana Gómez", "aspirante", ahora
        )
        cls.borrador = Vacante.objects.create(
            titulo="Borrador de prueba",
            modalidad=ModalidadVacante.HIBRIDO,
            estado=EstadoVacante.BORRADOR,
            creado_en=ahora,
            actualizado_en=ahora,
        )

    @classmethod
    def _crear_usuario(cls, email, nombre, rol_clave, ahora):
        usuario = Usuario.objects.create(
            id=uuid.uuid4(),
            nombre_completo=nombre,
            email=email,
            password_hash="!",
            estado=EstadoUsuario.ACTIVO,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        UsuarioRol.objects.create(
            usuario=usuario,
            rol=Rol.objects.get(clave=rol_clave),
            asignado_en=ahora,
        )
        return usuario

    def cliente_de(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    NUEVA = {
        "titulo": "Consultor SAP Customer Checkout",
        "departamento": "Consultoría SAP",
        "modalidad": "hibrido",
        "contratacion": "Por proyecto",
        "duracion_min_semanas": 6,
        "duracion_max_semanas": 10,
        "email_contacto": "recursoshumanos@ene8.com.mx",
        "etiquetas": ["SAP CCO"],
        "requisitos": ["Experiencia en SAP CCO."],
    }

    def ids_publicos(self):
        respuesta = APIClient().get(reverse("api:vacante-list"))
        return {fila["id"] for fila in respuesta.data}

    # --- Puerta del módulo ---------------------------------------------

    def test_sin_sesion_responde_401(self):
        respuesta = APIClient().post(
            reverse("api:vacante-admin-list"), self.NUEVA, format="json"
        )

        self.assertEqual(respuesta.status_code, 401)

    def test_el_aspirante_no_puede_publicar(self):
        respuesta = self.cliente_de(self.aspirante).post(
            reverse("api:vacante-admin-list"), self.NUEVA, format="json"
        )

        self.assertEqual(respuesta.status_code, 403)

    def test_el_aspirante_no_ve_el_catalogo_interno(self):
        respuesta = self.cliente_de(self.aspirante).get(
            reverse("api:vacante-admin-list")
        )

        self.assertEqual(respuesta.status_code, 403)

    # --- Alta -----------------------------------------------------------

    def test_el_reclutador_publica_una_vacante(self):
        respuesta = self.cliente_de(self.reclutador).post(
            reverse("api:vacante-admin-list"), self.NUEVA, format="json"
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data["estado"], EstadoVacante.PUBLICADA)

    def test_la_vacante_creada_aparece_en_el_listado_publico(self):
        """
        Cubre la trampa de `publicada_en`: sin fecharla, la consulta pública la
        filtraría aunque su estado dijera "publicada".
        """
        respuesta = self.cliente_de(self.reclutador).post(
            reverse("api:vacante-admin-list"), self.NUEVA, format="json"
        )

        self.assertIsNotNone(respuesta.data["publicada_en"])
        self.assertIn(respuesta.data["id"], self.ids_publicos())

    def test_registra_quien_la_creo(self):
        respuesta = self.cliente_de(self.reclutador).post(
            reverse("api:vacante-admin-list"), self.NUEVA, format="json"
        )

        creada = Vacante.objects.get(pk=respuesta.data["id"])
        self.assertEqual(creada.creado_por_id, self.reclutador.id)

    def test_puede_guardarse_como_borrador_sin_salir_al_publico(self):
        respuesta = self.cliente_de(self.reclutador).post(
            reverse("api:vacante-admin-list"),
            {**self.NUEVA, "estado": "borrador"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertIsNone(respuesta.data["publicada_en"])
        self.assertNotIn(respuesta.data["id"], self.ids_publicos())

    def test_rechaza_una_duracion_maxima_menor_que_la_minima(self):
        respuesta = self.cliente_de(self.reclutador).post(
            reverse("api:vacante-admin-list"),
            {**self.NUEVA, "duracion_min_semanas": 10, "duracion_max_semanas": 6},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("duracion_max_semanas", respuesta.data)

    def test_acepta_una_vacante_sin_duracion(self):
        sin_duracion = {
            campo: valor
            for campo, valor in self.NUEVA.items()
            if not campo.startswith("duracion_")
        }

        respuesta = self.cliente_de(self.reclutador).post(
            reverse("api:vacante-admin-list"), sin_duracion, format="json"
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertIsNone(respuesta.data["duracion_min_semanas"])

    # --- Catálogo interno y edición -------------------------------------

    def test_el_catalogo_interno_incluye_los_borradores(self):
        respuesta = self.cliente_de(self.reclutador).get(
            reverse("api:vacante-admin-list")
        )

        ids = {fila["id"] for fila in respuesta.data}
        self.assertIn(self.borrador.id, ids)
        self.assertNotIn(self.borrador.id, self.ids_publicos())

    def test_publicar_un_borrador_lo_fecha_y_lo_saca_al_publico(self):
        respuesta = self.cliente_de(self.reclutador).patch(
            reverse("api:vacante-admin-detail", args=[self.borrador.id]),
            {"estado": "publicada"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNotNone(respuesta.data["publicada_en"])
        self.assertIn(self.borrador.id, self.ids_publicos())

    def test_cerrar_una_vacante_la_retira_del_publico(self):
        cliente = self.cliente_de(self.reclutador)
        creada = cliente.post(
            reverse("api:vacante-admin-list"), self.NUEVA, format="json"
        ).data

        cliente.patch(
            reverse("api:vacante-admin-detail", args=[creada["id"]]),
            {"estado": "cerrada"},
            format="json",
        )

        self.assertNotIn(creada["id"], self.ids_publicos())

    def test_no_expone_el_borrado(self):
        """
        `postulaciones.vacante_id` es PROTECT: retirar una vacante es cerrarla,
        no borrarla.
        """
        respuesta = self.cliente_de(self.reclutador).delete(
            reverse("api:vacante-admin-detail", args=[self.borrador.id])
        )

        self.assertEqual(respuesta.status_code, 405)


class UsuariosDePruebaMixin:
    """
    Altas mínimas de usuario y expediente para los tests de postulaciones.

    Los roles y permisos ya vienen de database/seed.sql, que la migración
    inicial carga también en la base de pruebas, así que aquí sólo se asignan.
    """

    @classmethod
    def _crear_usuario(cls, email, nombre, rol_clave, ahora):
        usuario = Usuario.objects.create(
            id=uuid.uuid4(),
            nombre_completo=nombre,
            email=email,
            password_hash="!",
            estado=EstadoUsuario.ACTIVO,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        UsuarioRol.objects.create(
            usuario=usuario,
            rol=Rol.objects.get(clave=rol_clave),
            asignado_en=ahora,
        )
        return usuario

    @classmethod
    def _crear_aspirante(
        cls, email, aspirante_id, nombre, rol_clave, ahora, experiencia_meses=None
    ):
        usuario = cls._crear_usuario(email, nombre, rol_clave, ahora)
        aspirante = Aspirante.objects.create(
            id=aspirante_id,
            usuario=usuario,
            matricula=aspirante_id,
            nombre_completo=nombre,
            email=email,
            registrado_en=ahora,
            actualizado_en=ahora,
        )
        PerfilProfesional.objects.create(
            aspirante=aspirante,
            habilidades_tecnicas=["SAP CCO", "SAP Business One"],
            experiencia_meses=experiencia_meses,
            actualizado_en=ahora,
        )
        return usuario, aspirante

    def cliente_de(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente


class PostulacionAccesoTest(UsuariosDePruebaMixin, TestCase):
    """
    Matriz de acceso de /api/postulaciones/.

    Los roles y permisos ya vienen de database/seed.sql, que la migración
    inicial carga también en la base de pruebas, así que aquí sólo se asignan.

    Ese mismo seed crea expedientes ASP-001..ASP-005, de ahí el prefijo TEST-
    en los ids de aquí. Hoy no siembra postulaciones, pero las aserciones de
    "ve todas" comprueban inclusión y no igualdad, para no romperse si algún
    día las siembra.
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
            "ana@ene8.com.mx", "TEST-ASP-001", "Ana Gómez", "aspirante", ahora
        )
        cls.usuario_otro, cls.otro_aspirante = cls._crear_aspirante(
            "luis@ene8.com.mx", "TEST-ASP-002", "Luis Pérez", "aspirante", ahora
        )

        cls.postulacion_propia = Postulacion.objects.create(
            aspirante=cls.aspirante,
            vacante=cls.vacante,
            estado=EstadoPostulacion.NUEVO,
            registrada_en=ahora,
            ultima_actividad_en=ahora,
        )
        cls.postulacion_ajena = Postulacion.objects.create(
            aspirante=cls.otro_aspirante,
            vacante=cls.vacante,
            estado=EstadoPostulacion.REVISION,
            registrada_en=ahora,
            ultima_actividad_en=ahora,
        )

        cls.reclutador = cls._crear_usuario(
            "reclutador@ene8.com.mx", "Rita Ruiz", "reclutador", ahora
        )
        cls.administrador = cls._crear_usuario(
            "admin@ene8.com.mx", "Ada Admin", "administrador", ahora
        )
        cls.empresa = cls._crear_usuario(
            "empresa@ene8.com.mx", "Empresa Demo", "empresa", ahora
        )
        cls.solo_consulta = cls._crear_usuario(
            "consulta@ene8.com.mx", "Coni Consulta", "consulta", ahora
        )

    def ids_de(self, respuesta):
        datos = respuesta.data
        resultados = datos["results"] if isinstance(datos, dict) else datos
        return {fila["id"] for fila in resultados}

    # --- Alcance de la lista -------------------------------------------

    def test_administrador_ve_todas_las_postulaciones(self):
        respuesta = self.cliente_de(self.administrador).get(
            reverse("api:postulacion-list")
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(self.postulacion_ajena.id, self.ids_de(respuesta))
        self.assertIn(self.postulacion_propia.id, self.ids_de(respuesta))

    def test_reclutador_ve_todas_las_postulaciones(self):
        respuesta = self.cliente_de(self.reclutador).get(
            reverse("api:postulacion-list")
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(self.postulacion_ajena.id, self.ids_de(respuesta))
        self.assertIn(self.postulacion_propia.id, self.ids_de(respuesta))

    def test_solo_consulta_ve_todas_sin_poder_administrar(self):
        """
        El caso que motivó `postulaciones:consultar-todas`: este rol lee el
        proceso completo pero no tiene `postulaciones:administrar`.
        """
        cliente = self.cliente_de(self.solo_consulta)

        respuesta = cliente.get(reverse("api:postulacion-list"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(self.postulacion_propia.id, self.ids_de(respuesta))
        self.assertIn(self.postulacion_ajena.id, self.ids_de(respuesta))

        detalle = reverse(
            "api:postulacion-detail", args=[self.postulacion_ajena.id]
        )
        self.assertEqual(
            cliente.patch(detalle, {"estado": "contratado"}, format="json").status_code,
            405,
        )

    def test_aspirante_solo_ve_las_suyas(self):
        respuesta = self.cliente_de(self.usuario_aspirante).get(
            reverse("api:postulacion-list")
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.ids_de(respuesta), {self.postulacion_propia.id})
        # La propiedad que importa: la ajena no se filtra.
        self.assertNotIn(self.postulacion_ajena.id, self.ids_de(respuesta))

    # --- Alcance del detalle -------------------------------------------

    def test_aspirante_recibe_404_en_la_postulacion_ajena(self):
        """404 y no 403: un 403 confirmaría que el registro existe."""
        respuesta = self.cliente_de(self.usuario_aspirante).get(
            reverse(
                "api:postulacion-detail",
                args=[self.postulacion_ajena.id],
            )
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_aspirante_accede_al_detalle_de_la_suya(self):
        respuesta = self.cliente_de(self.usuario_aspirante).get(
            reverse(
                "api:postulacion-detail",
                args=[self.postulacion_propia.id],
            )
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["id"], self.postulacion_propia.id)

    def test_reclutador_accede_al_detalle_de_cualquiera(self):
        respuesta = self.cliente_de(self.reclutador).get(
            reverse(
                "api:postulacion-detail",
                args=[self.postulacion_ajena.id],
            )
        )

        self.assertEqual(respuesta.status_code, 200)

    # --- Puerta del módulo ---------------------------------------------

    def test_empresa_ve_todas_las_postulaciones(self):
        """Temporalmente Empresa comparte los permisos del administrador."""
        respuesta = self.cliente_de(self.empresa).get(
            reverse("api:postulacion-list")
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.data), 2)

    def test_sin_sesion_responde_401(self):
        respuesta = APIClient().get(reverse("api:postulacion-list"))

        self.assertEqual(respuesta.status_code, 401)

    def test_usuario_sin_roles_no_entra(self):
        ahora = timezone.now()
        huerfano = Usuario.objects.create(
            id=uuid.uuid4(),
            nombre_completo="Sin Roles",
            email="sinroles@ene8.com.mx",
            password_hash="!",
            estado=EstadoUsuario.ACTIVO,
            creado_en=ahora,
            actualizado_en=ahora,
        )

        respuesta = self.cliente_de(huerfano).get(
            reverse("api:postulacion-list")
        )

        self.assertEqual(respuesta.status_code, 403)

    # --- Escrituras permitidas y prohibidas -------------------------------

    def test_una_postulacion_existente_no_se_edita_ni_se_borra(self):
        """
        El alta se abrió para la bolsa de trabajo, pero mover el proceso —o
        deshacerlo— sigue sin tener endpoint: eso se hace por fuera del API.
        """
        cliente = self.cliente_de(self.administrador)
        detalle = reverse(
            "api:postulacion-detail", args=[self.postulacion_propia.id]
        )

        self.assertEqual(
            cliente.patch(detalle, {"estado": "contratado"}, format="json").status_code,
            405,
        )
        self.assertEqual(cliente.delete(detalle).status_code, 405)

    # --- Forma de la respuesta ------------------------------------------

    def test_la_postulacion_incluye_el_resumen_del_aspirante(self):
        respuesta = self.cliente_de(self.reclutador).get(
            reverse(
                "api:postulacion-detail",
                args=[self.postulacion_propia.id],
            )
        )

        aspirante = respuesta.data["aspirante"]
        self.assertEqual(aspirante["nombre_completo"], "Ana Gómez")
        self.assertEqual(
            aspirante["habilidades_tecnicas"], ["SAP CCO", "SAP Business One"]
        )
        self.assertEqual(
            respuesta.data["vacante_titulo"],
            "Consultor SAP Customer Checkout",
        )

    def test_el_resumen_no_expone_el_expediente_completo(self):
        """
        El panel no necesita dirección ni folio, así que no viajan. Si mañana
        se agregan, que sea una decisión y no un descuido.
        """
        respuesta = self.cliente_de(self.reclutador).get(
            reverse(
                "api:postulacion-detail",
                args=[self.postulacion_propia.id],
            )
        )

        for campo in (
            "direccion",
            "folio_aplicacion",
            "cedula_profesional",
            "fecha_nacimiento",
        ):
            with self.subTest(campo=campo):
                self.assertNotIn(campo, respuesta.data["aspirante"])

    def test_el_aspirante_sin_perfil_no_rompe_la_serializacion(self):
        ahora = timezone.now()
        usuario = self._crear_usuario(
            "nuevo@ene8.com.mx", "Nuevo Sin Perfil", "aspirante", ahora
        )
        aspirante = Aspirante.objects.create(
            id="TEST-ASP-003",
            usuario=usuario,
            matricula="TEST-ASP-003",
            nombre_completo="Nuevo Sin Perfil",
            email="nuevo@ene8.com.mx",
            registrado_en=ahora,
            actualizado_en=ahora,
        )
        Postulacion.objects.create(
            aspirante=aspirante,
            vacante=self.vacante,
            estado=EstadoPostulacion.NUEVO,
            registrada_en=ahora,
            ultima_actividad_en=ahora,
        )

        respuesta = self.cliente_de(usuario).get(reverse("api:postulacion-list"))

        self.assertEqual(respuesta.status_code, 200)
        resultados = (
            respuesta.data["results"]
            if isinstance(respuesta.data, dict)
            else respuesta.data
        )
        self.assertEqual(resultados[0]["aspirante"]["habilidades_tecnicas"], [])


class PostulacionAltaTest(UsuariosDePruebaMixin, TestCase):
    """
    Alta de postulaciones desde la bolsa de trabajo.

    `POST /api/postulaciones/` es lo que dispara el botón "Enviar mi CV": el
    aspirante sale de la sesión y sólo se aceptan los datos que él declara,
    nunca el estado ni la etapa del proceso.
    """

    @classmethod
    def setUpTestData(cls):
        ahora = timezone.now()
        cls.ahora = ahora

        cls.vacante = cls._crear_vacante(
            "Consultor SAP Customer Checkout",
            ahora,
            estado=EstadoVacante.PUBLICADA,
            publicada_en=ahora - timedelta(days=1),
        )
        cls.borrador = cls._crear_vacante(
            "Vacante en borrador", ahora, estado=EstadoVacante.BORRADOR
        )
        cls.cerrada = cls._crear_vacante(
            "Convocatoria vencida",
            ahora,
            estado=EstadoVacante.PUBLICADA,
            publicada_en=ahora - timedelta(days=30),
            cierra_en=ahora - timedelta(days=1),
        )

        cls.usuario_aspirante, cls.aspirante = cls._crear_aspirante(
            "ana@ene8.com.mx",
            "TEST-ALTA-001",
            "Ana Gómez",
            "aspirante",
            ahora,
            experiencia_meses=60,
        )
        cls.reclutador = cls._crear_usuario(
            "reclutador@ene8.com.mx", "Rita Ruiz", "reclutador", ahora
        )

    @classmethod
    def _crear_vacante(cls, titulo, ahora, **extra):
        return Vacante.objects.create(
            titulo=titulo,
            modalidad=ModalidadVacante.HIBRIDO,
            creado_en=ahora,
            actualizado_en=ahora,
            **extra,
        )

    def postularse(self, usuario=None, **cuerpo):
        cuerpo.setdefault("vacante", self.vacante.id)
        return self.cliente_de(usuario or self.usuario_aspirante).post(
            reverse("api:postulacion-list"), cuerpo, format="json"
        )

    # --- Camino feliz -----------------------------------------------------

    def test_el_aspirante_se_postula_y_queda_registrada(self):
        respuesta = self.postularse(
            ultimo_empleo="Analista Sr. @ GNP Seguros",
            experiencia_meses=48,
            expectativas_salariales="48000.00",
            horas_deseadas=40,
            disponibilidad=["Lunes a viernes"],
        )

        self.assertEqual(respuesta.status_code, 201)

        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.aspirante_id, self.aspirante.id)
        self.assertEqual(postulacion.vacante_id, self.vacante.id)
        self.assertEqual(postulacion.ultimo_empleo, "Analista Sr. @ GNP Seguros")
        self.assertEqual(postulacion.experiencia_meses, 48)
        self.assertEqual(postulacion.horas_deseadas, 40)
        self.assertEqual(postulacion.disponibilidad, ["Lunes a viernes"])

    def test_nace_en_nuevo_sin_avanzar_el_proceso(self):
        respuesta = self.postularse()

        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.estado, EstadoPostulacion.NUEVO)
        self.assertEqual(postulacion.etapa, "Postulación recibida")
        self.assertEqual(postulacion.progreso, 0)

    def test_responde_con_la_forma_que_usa_el_listado(self):
        respuesta = self.postularse()

        self.assertEqual(
            respuesta.data["aspirante"]["nombre_completo"], "Ana Gómez"
        )
        self.assertEqual(
            respuesta.data["vacante_titulo"], "Consultor SAP Customer Checkout"
        )
        self.assertEqual(respuesta.data["estado"], EstadoPostulacion.NUEVO)

    def test_la_postulacion_creada_aparece_en_su_listado(self):
        creada = self.postularse().data["id"]

        respuesta = self.cliente_de(self.usuario_aspirante).get(
            reverse("api:postulacion-list")
        )

        resultados = (
            respuesta.data["results"]
            if isinstance(respuesta.data, dict)
            else respuesta.data
        )
        self.assertIn(creada, {fila["id"] for fila in resultados})

    def test_solo_pide_la_vacante(self):
        """Postularse con un clic tiene que bastar: el resto es opcional."""
        respuesta = self.postularse()

        self.assertEqual(respuesta.status_code, 201)
        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.disponibilidad, [])
        self.assertIsNone(postulacion.ultimo_empleo)

    # --- Herencia del expediente -----------------------------------------

    def test_hereda_la_experiencia_del_perfil_cuando_no_se_declara(self):
        respuesta = self.postularse()

        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.experiencia_meses, 60)

    def test_lo_declarado_gana_sobre_el_perfil(self):
        respuesta = self.postularse(experiencia_meses=12)

        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.experiencia_meses, 12)

    # --- Quién puede postularse ------------------------------------------

    def test_sin_sesion_responde_401(self):
        respuesta = APIClient().post(
            reverse("api:postulacion-list"),
            {"vacante": self.vacante.id},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 401)

    def test_el_reclutador_no_se_postula(self):
        """No tiene expediente: gestiona el proceso, no participa en él."""
        respuesta = self.postularse(usuario=self.reclutador)

        self.assertEqual(respuesta.status_code, 403)

    def test_no_se_puede_postular_a_nombre_de_otro(self):
        otro_usuario, otro = self._crear_aspirante(
            "luis@ene8.com.mx", "TEST-ALTA-002", "Luis Pérez", "aspirante", self.ahora
        )

        respuesta = self.postularse(aspirante=otro.id)

        self.assertEqual(respuesta.status_code, 201)
        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.aspirante_id, self.aspirante.id)
        self.assertFalse(otro.postulaciones.exists())
        self.assertFalse(
            self.cliente_de(otro_usuario)
            .get(reverse("api:postulacion-list"))
            .data
        )

    def test_el_estado_del_proceso_no_se_acepta_del_cliente(self):
        respuesta = self.postularse(estado=EstadoPostulacion.CONTRATADO, progreso=100)

        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.estado, EstadoPostulacion.NUEVO)
        self.assertEqual(postulacion.progreso, 0)

    # --- Vacantes que no admiten postulación ------------------------------

    def test_no_se_postula_dos_veces_a_la_misma_vacante(self):
        self.postularse()

        respuesta = self.postularse()

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("Ya te postulaste", str(respuesta.data["vacante"][0]))
        self.assertEqual(
            Postulacion.objects.filter(
                aspirante=self.aspirante, vacante=self.vacante
            ).count(),
            1,
        )

    def test_rechaza_una_vacante_en_borrador(self):
        respuesta = self.postularse(vacante=self.borrador.id)

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(self.borrador.postulaciones.exists())

    def test_rechaza_una_convocatoria_ya_cerrada(self):
        respuesta = self.postularse(vacante=self.cerrada.id)

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(self.cerrada.postulaciones.exists())

    def test_rechaza_una_vacante_inexistente(self):
        respuesta = self.postularse(vacante=999999)

        self.assertEqual(respuesta.status_code, 400)

    # --- Validación de los datos declarados -------------------------------

    def test_rechaza_horas_deseadas_no_positivas(self):
        respuesta = self.postularse(horas_deseadas=0)

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("horas_deseadas", respuesta.data)

    def test_rechaza_experiencia_negativa(self):
        respuesta = self.postularse(experiencia_meses=-1)

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("experiencia_meses", respuesta.data)

    def test_rechaza_una_disponibilidad_que_no_sea_lista_de_textos(self):
        respuesta = self.postularse(disponibilidad=[{"dia": "lunes"}])

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("disponibilidad", respuesta.data)

    def test_limpia_los_espacios_de_la_disponibilidad(self):
        respuesta = self.postularse(disponibilidad=["  Lunes a viernes  ", "  "])

        postulacion = Postulacion.objects.get(id=respuesta.data["id"])
        self.assertEqual(postulacion.disponibilidad, ["Lunes a viernes"])
