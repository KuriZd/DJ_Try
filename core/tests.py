from django.test import SimpleTestCase
from django.urls import Resolver404, resolve, reverse


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
