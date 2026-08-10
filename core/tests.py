import uuid

from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Aspirante,
    EstadoPostulacion,
    EstadoUsuario,
    ModalidadVacante,
    PerfilProfesional,
    Postulacion,
    Rol,
    Usuario,
    UsuarioRol,
    Vacante,
)


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
        )

        for nombre in nombres:
            with self.subTest(nombre=nombre):
                self.assertTrue(reverse(nombre).startswith("/api/"))

    def test_rutas_retiradas(self):
        for ruta in (
            "/api/convocatorias/",
            "/api/vacantes/",
            "/api/certificados/",
        ):
            with self.subTest(ruta=ruta):
                with self.assertRaises(Resolver404):
                    resolve(ruta)


class PostulacionAccesoTest(TestCase):
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
        cls.certificador = cls._crear_usuario(
            "cert@ene8.com.mx", "Ceci Cert", "certificador", ahora
        )
        cls.solo_consulta = cls._crear_usuario(
            "consulta@ene8.com.mx", "Coni Consulta", "consulta", ahora
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

    @classmethod
    def _crear_aspirante(cls, email, aspirante_id, nombre, rol_clave, ahora):
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
            actualizado_en=ahora,
        )
        return usuario, aspirante

    def cliente_de(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

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

    def test_certificador_no_entra_al_modulo(self):
        """No tiene ningún permiso `postulaciones:*`."""
        respuesta = self.cliente_de(self.certificador).get(
            reverse("api:postulacion-list")
        )

        self.assertEqual(respuesta.status_code, 403)

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

    # --- Sólo lectura ---------------------------------------------------

    def test_el_modulo_es_de_solo_lectura(self):
        cliente = self.cliente_de(self.administrador)
        lista = reverse("api:postulacion-list")
        detalle = reverse(
            "api:postulacion-detail", args=[self.postulacion_propia.id]
        )

        self.assertEqual(cliente.post(lista, {}, format="json").status_code, 405)
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
