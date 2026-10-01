import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Publicacion, Rol, Usuario, UsuarioRol


def crear_usuario(rol):
    user = Usuario.objects.create(
        id=uuid.uuid4(), nombre_completo='Persona ' + rol,
        email=f'{uuid.uuid4()}@example.test', password_hash='!', estado='activo',
        creado_en=timezone.now(), actualizado_en=timezone.now(),
    )
    UsuarioRol.objects.create(usuario=user, rol=Rol.objects.get(clave=rol), asignado_en=timezone.now())
    return user


class PublicacionesAPITests(TestCase):
    URL = '/api/publicaciones/'

    @classmethod
    def setUpTestData(cls):
        cls.autor = crear_usuario('aspirante')
        cls.otro = crear_usuario('aspirante')
        cls.admin = crear_usuario('administrador')
        cls.publicacion = Publicacion.objects.create(autor=cls.autor, cuerpo='Hola, muro')

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def detalle(self, publicacion=None):
        return f'{self.URL}{(publicacion or self.publicacion).pk}/'

    def test_leer_es_publico_y_no_expone_el_correo(self):
        respuesta = self.client.get(self.URL)
        self.assertEqual(respuesta.status_code, 200)
        primera = respuesta.data['results'][0]
        self.assertEqual(primera['cuerpo'], 'Hola, muro')
        self.assertEqual(set(primera['autor']), {'id', 'nombre_completo'})
        self.assertEqual(self.client.get(self.detalle()).status_code, 200)

    def test_publicar_pide_sesion(self):
        self.assertEqual(self.client.post(self.URL, {'cuerpo': 'x'}, format='json').status_code, 401)

    def test_cualquier_cuenta_publica_como_si_misma(self):
        self.client.force_authenticate(self.otro)
        respuesta = self.client.post(
            self.URL, {'cuerpo': '  Linea 1\r\nLinea 2  ', 'autor': str(self.admin.pk)}, format='json',
        )
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data['cuerpo'], 'Linea 1\nLinea 2')
        self.assertEqual(respuesta.data['autor']['id'], str(self.otro.pk))
        self.assertIsNone(respuesta.data['fecha_edicion'])

    def test_valida_cuerpo_vacio_y_largo(self):
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.post(self.URL, {'cuerpo': '   '}, format='json').status_code, 400)
        largo = 'a' * (Publicacion.LIMITE_CUERPO + 1)
        self.assertEqual(self.client.post(self.URL, {'cuerpo': largo}, format='json').status_code, 400)

    def test_el_muro_va_de_la_mas_reciente_y_pagina_por_cursor(self):
        # Fechas explicitas: altas seguidas pueden caer en el mismo instante.
        base = timezone.now()
        for i in range(12):
            nueva = Publicacion.objects.create(autor=self.otro, cuerpo=f'n{i}')
            Publicacion.objects.filter(pk=nueva.pk).update(
                fecha_publicacion=base + timedelta(minutes=i + 1),
            )
        primera = self.client.get(self.URL).data
        self.assertEqual(len(primera['results']), 10)
        self.assertEqual(primera['results'][0]['cuerpo'], 'n11')
        segunda = self.client.get(primera['next']).data
        self.assertEqual([p['cuerpo'] for p in segunda['results']], ['n1', 'n0', 'Hola, muro'])
        self.assertIsNone(segunda['next'])

    def test_solo_el_autor_edita_y_queda_marcada(self):
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.patch(self.detalle(), {'cuerpo': 'no'}, format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.patch(self.detalle(), {'cuerpo': 'no'}, format='json').status_code, 403)
        self.client.force_authenticate(self.autor)
        respuesta = self.client.patch(self.detalle(), {'cuerpo': 'Corregida'}, format='json')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['cuerpo'], 'Corregida')
        self.assertIsNotNone(respuesta.data['fecha_edicion'])

    def test_put_no_esta_permitido(self):
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.put(self.detalle(), {'cuerpo': 'x'}, format='json').status_code, 405)

    def test_eliminar_es_del_autor_o_de_quien_modera(self):
        ajena = Publicacion.objects.create(autor=self.autor, cuerpo='otra')
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.delete(self.detalle()).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(self.detalle(ajena)).status_code, 204)
        self.client.force_authenticate(self.autor)
        self.assertEqual(self.client.delete(self.detalle()).status_code, 204)
        self.assertFalse(Publicacion.objects.exists())

    def test_limite_de_altas_por_cuenta(self):
        self.client.force_authenticate(self.autor)
        codigos = [self.client.post(self.URL, {'cuerpo': f'p{i}'}, format='json').status_code for i in range(11)]
        self.assertEqual(codigos[:10], [201] * 10)
        self.assertEqual(codigos[10], 429)
        # El limite es por cuenta: otra sigue publicando.
        self.client.force_authenticate(self.otro)
        self.assertEqual(self.client.post(self.URL, {'cuerpo': 'ok'}, format='json').status_code, 201)
