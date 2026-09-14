"""Padron de cuentas: `GET /api/usuarios/`.

Es con lo que un administrador encuentra a una persona concreta de la
plataforma, sin importar si se postulo a algo o no.
"""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import Rol, Usuario, UsuarioRol
from django.utils import timezone


class PadronUsuariosApiTest(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.get(email__iexact="admin@amis.org")
        self.aspirante = Usuario.objects.get(email__iexact="aspirante@amis.org")
        self.reclutador = Usuario.objects.get(
            email__iexact="KuriZd@reclutador.com"
        )
        self.cliente = self.cliente_de(self.admin)

    def cliente_de(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    def url(self):
        return reverse("api:usuario-list")

    def filas(self, respuesta):
        return {fila["email"].lower(): fila for fila in respuesta.data}

    # -- acceso -----------------------------------------------------------

    def test_exige_sesion(self):
        respuesta = APIClient().get(self.url())

        self.assertEqual(respuesta.status_code, 401)

    def test_lo_abre_quien_administra_usuarios(self):
        respuesta = self.cliente.get(self.url())

        self.assertEqual(respuesta.status_code, 200)

    def test_lo_niega_al_aspirante(self):
        respuesta = self.cliente_de(self.aspirante).get(self.url())

        self.assertEqual(respuesta.status_code, 403)

    def test_lo_niega_al_reclutador(self):
        # Tiene `aspirantes:consultar` y `postulaciones:consultar-todas`, que
        # le bastan para su bandeja, pero el padron entero no es asunto suyo.
        respuesta = self.cliente_de(self.reclutador).get(self.url())

        self.assertEqual(respuesta.status_code, 403)

    # -- que cuentas salen ------------------------------------------------

    def test_lista_las_cuentas_de_la_plataforma(self):
        respuesta = self.cliente.get(self.url())

        emails = self.filas(respuesta)
        self.assertIn("admin@amis.org", emails)
        self.assertIn("aspirante@amis.org", emails)
        self.assertIn("kurizd@empresa.com", emails)

    def test_omite_las_cuentas_dadas_de_baja(self):
        # El borrado es logico para no perder la historia de quien hizo que,
        # pero esa historia no se consulta en un buscador de personas.
        Usuario.objects.filter(pk=self.aspirante.pk).update(
            eliminado_en=timezone.now()
        )

        respuesta = self.cliente.get(self.url())

        self.assertNotIn("aspirante@amis.org", self.filas(respuesta))

    def test_ordena_por_nombre(self):
        respuesta = self.cliente.get(self.url())

        nombres = [fila["nombre_completo"] for fila in respuesta.data]
        # Ignorando mayusculas, que es como ordena Postgres con su collation y
        # como lo lee una persona. El `sorted` pelado de Python compara por
        # punto de codigo y mandaria 'kurizd' detras de 'KuriZd Reclutador',
        # que aqui seria una falla inventada por la prueba.
        self.assertEqual(nombres, sorted(nombres, key=str.casefold))

    # -- forma de la respuesta --------------------------------------------

    def test_expone_el_rol_de_cada_cuenta(self):
        respuesta = self.cliente.get(self.url())

        fila = self.filas(respuesta)["aspirante@amis.org"]
        self.assertEqual(
            [rol["clave"] for rol in fila["roles"]], ["aspirante"]
        )

    def test_lista_los_dos_roles_de_una_cuenta_con_dos(self):
        rol_consulta = Rol.objects.get(clave="consulta")
        UsuarioRol.objects.create(
            usuario=self.aspirante,
            rol=rol_consulta,
            asignado_en=timezone.now(),
        )

        respuesta = self.cliente.get(self.url())

        claves = {
            rol["clave"]
            for rol in self.filas(respuesta)["aspirante@amis.org"]["roles"]
        }
        self.assertEqual(claves, {"aspirante", "consulta"})

    def test_dice_si_hay_expediente_o_empresa_detras(self):
        respuesta = self.cliente.get(self.url())
        emails = self.filas(respuesta)

        self.assertTrue(emails["aspirante@amis.org"]["tiene_aspirante"])
        self.assertFalse(emails["aspirante@amis.org"]["tiene_empresa"])
        self.assertTrue(emails["kurizd@empresa.com"]["tiene_empresa"])

    def test_no_manda_la_contrasena_ni_los_permisos_efectivos(self):
        fila = self.cliente.get(self.url()).data[0]

        # El hash nunca sale; los permisos son una consulta por usuario y no
        # ayudan a encontrar a nadie.
        self.assertNotIn("password_hash", fila)
        self.assertNotIn("permisos", fila)

    def test_trae_el_estado_y_el_ultimo_acceso(self):
        fila = self.filas(self.cliente.get(self.url()))["admin@amis.org"]

        self.assertIn("estado", fila)
        self.assertIn("ultimo_acceso_en", fila)

    def test_es_de_solo_lectura(self):
        respuesta = self.cliente.post(self.url(), {}, format="json")

        self.assertEqual(respuesta.status_code, 405)
