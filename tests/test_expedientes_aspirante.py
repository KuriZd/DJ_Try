"""Toda cuenta con rol 'aspirante' tiene que poder postularse.

Es la regresión de un fallo mudo: sin fila en `aspirantes`, `expediente_de`
devuelve None, `auth/me` manda `aspirante: null` y el frontend deja de ofrecer
el formulario —cae al `mailto:` de la vacante—. Quien pulsa "Enviar mi CV" no
ve error ninguno y la postulación simplemente nunca existe.

En el seed pasaba con `KuriZd@aspirante.com`: se crea la cuenta y se le asigna
el rol, pero nunca se le crea el expediente. La migración
0021_expedientes_de_cuentas_aspirante lo repara; esto lo deja clavado.
"""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import Aspirante, Rol, Usuario, UsuarioRol


def cuentas_con_rol_aspirante():
    return Usuario.objects.filter(
        eliminado_en__isnull=True,
        usuarios_roles__rol__clave="aspirante",
    ).distinct()


class ExpedientesDeCuentasAspiranteTest(TestCase):
    def test_el_seed_deja_al_menos_dos_cuentas_aspirante(self):
        # Si esto baja de dos, el seed cambió y el resto de la clase deja de
        # cubrir lo que cree cubrir.
        self.assertGreaterEqual(cuentas_con_rol_aspirante().count(), 2)

    def test_toda_cuenta_aspirante_tiene_expediente(self):
        sin_expediente = [
            usuario.email
            for usuario in cuentas_con_rol_aspirante()
            if not Aspirante.objects.filter(
                usuario=usuario, eliminado_en__isnull=True
            ).exists()
        ]

        self.assertEqual(
            sin_expediente,
            [],
            "Estas cuentas tienen rol de aspirante pero no pueden postularse: "
            f"{sin_expediente}",
        )

    def test_la_cuenta_amis_sigue_ligada(self):
        usuario = Usuario.objects.get(email__iexact="aspirante@amis.org")

        expediente = Aspirante.objects.filter(usuario=usuario).first()

        self.assertIsNotNone(expediente)
        self.assertEqual(expediente.id, "ASP-AMIS-001")

    def test_la_cuenta_demo_quedo_reparada(self):
        # Es la que el seed dejaba a medias.
        usuario = Usuario.objects.get(email__iexact="KuriZd@aspirante.com")

        self.assertIsNotNone(Aspirante.objects.filter(usuario=usuario).first())

    def test_ninguna_matricula_ni_clave_se_repite(self):
        # La migración calcula los consecutivos por separado porque hoy no
        # coinciden: ASP-AMIS-001 lleva la matrícula AM2026-0007.
        expedientes = Aspirante.objects.all()
        claves = [e.id for e in expedientes]
        matriculas = [e.matricula for e in expedientes]

        self.assertEqual(len(claves), len(set(claves)))
        self.assertEqual(len(matriculas), len(set(matriculas)))


class PostularseConCadaCuentaTest(TestCase):
    """La prueba de verdad: que el POST pase con cualquiera de esas cuentas."""

    def setUp(self):
        self.vacante = self._vacante_publicada()

    @staticmethod
    def _vacante_publicada():
        from django.utils import timezone

        from core.models import EstadoVacante, Vacante

        ahora = timezone.now()
        return (
            Vacante.objects.filter(
                estado=EstadoVacante.PUBLICADA, publicada_en__lte=ahora
            )
            .filter(cierra_en__isnull=True)
            .first()
        )

    def test_hay_una_vacante_abierta_con_la_que_probar(self):
        self.assertIsNotNone(
            self.vacante, "El seed no dejó ninguna vacante publicada."
        )

    def test_cada_cuenta_aspirante_puede_enviar_su_cv(self):
        for usuario in cuentas_con_rol_aspirante():
            with self.subTest(cuenta=usuario.email):
                cliente = APIClient()
                cliente.force_authenticate(user=usuario)

                respuesta = cliente.post(
                    reverse("api:postulacion-list"),
                    {"vacante": self.vacante.id},
                    format="json",
                )

                # 201 al enviarla; 400 si esa cuenta ya se postuló a esta
                # vacante en otra prueba. Lo que no puede salir es el 403 de
                # "necesitas un expediente", que es el fallo que se repara.
                self.assertIn(
                    respuesta.status_code,
                    (201, 400),
                    f"{usuario.email} no pudo postularse: {respuesta.data}",
                )

    def test_la_postulacion_enviada_aparece_en_el_listado_propio(self):
        usuario = Usuario.objects.get(email__iexact="KuriZd@aspirante.com")
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)

        alta = cliente.post(
            reverse("api:postulacion-list"),
            {"vacante": self.vacante.id},
            format="json",
        )
        self.assertEqual(alta.status_code, 201)

        listado = cliente.get(reverse("api:postulacion-list"))

        # Es lo que la pantalla "Mis postulaciones" lee, y lo que el panel de
        # reclutamiento verá con `consultar-todas`.
        self.assertEqual(listado.status_code, 200)
        self.assertEqual(len(listado.data), 1)
        fila = listado.data[0]
        self.assertEqual(fila["vacante"], self.vacante.id)
        self.assertEqual(fila["aspirante"]["email"], usuario.email)

    def test_el_reclutador_ve_esa_misma_postulacion(self):
        aspirante = Usuario.objects.get(email__iexact="KuriZd@aspirante.com")
        cliente = APIClient()
        cliente.force_authenticate(user=aspirante)
        cliente.post(
            reverse("api:postulacion-list"),
            {"vacante": self.vacante.id},
            format="json",
        )

        admin = Usuario.objects.get(email__iexact="admin@amis.org")
        panel = APIClient()
        panel.force_authenticate(user=admin)

        respuesta = panel.get(reverse("api:postulacion-list"))

        correos = [fila["aspirante"]["email"] for fila in respuesta.data]
        self.assertIn(aspirante.email, correos)


class CuentaSinExpedienteTest(TestCase):
    """El 403 sigue ahí para quien de verdad no tiene expediente."""

    def test_una_cuenta_nueva_sin_expediente_no_se_postula(self):
        import uuid

        from django.utils import timezone

        ahora = timezone.now()
        suelta = Usuario.objects.create(
            id=uuid.uuid4(),
            nombre_completo="Sin Expediente",
            email="sin.expediente@amis.org",
            password_hash="x",
            estado="activo",
            creado_en=ahora,
            actualizado_en=ahora,
        )
        UsuarioRol.objects.create(
            usuario=suelta,
            rol=Rol.objects.get(clave="aspirante"),
            asignado_en=ahora,
        )

        cliente = APIClient()
        cliente.force_authenticate(user=suelta)
        respuesta = cliente.post(
            reverse("api:postulacion-list"), {"vacante": 1}, format="json"
        )

        # La reparación es de datos, no de reglas: quien no tiene expediente
        # sigue sin poder postularse, y ahora con un mensaje que lo dice.
        self.assertEqual(respuesta.status_code, 403)
