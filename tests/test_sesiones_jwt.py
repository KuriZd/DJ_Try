"""Revocacion de access y refresh mediante sesiones persistidas."""
import uuid
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from core.api.views import abrir_sesion, token_hash
from core.models import PropositoToken, Sesion, TokenRecuperacion, Usuario
from core.services import tokens


class SesionesJWTTest(TestCase):
    password = 'QA-Original-Long-924!'
    replacement = 'QA-Replacement-Long-835!'

    def setUp(self):
        cache.clear()
        self.user = Usuario.objects.get(email='aspirante@amis.org')
        self.user.password_hash = make_password(self.password)
        self.user.save(update_fields=['password_hash'])

    def login(self, password=None):
        response = APIClient().post('/api/auth/login/', {
            'email': self.user.email, 'password': password or self.password,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def client_for(self, access):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        return client

    def refresh(self, refresh):
        return APIClient().post('/api/auth/refresh/', {'refresh': refresh}, format='json')

    def reset(self, token=None):
        token = token or tokens.emitir(self.user, PropositoToken.RECUPERACION)
        return APIClient().post('/api/auth/restablecer/', {
            'token': token, 'password_nueva': self.replacement,
        }, format='json')

    def test_recuperacion_invalida_dos_sesiones_y_access_renovado(self):
        first, second = self.login(), self.login()
        renewed = self.refresh(first['refresh'])
        self.assertEqual(renewed.status_code, 200)
        other = Usuario.objects.get(email='admin@amis.org')
        other_session = abrir_sesion(other, APIRequestFactory().post('/'))
        self.assertEqual(self.reset().status_code, 204)
        for access in (first['access'], second['access'], renewed.data['access']):
            client = self.client_for(access)
            self.assertEqual(client.get('/api/auth/me/').status_code, 401)
            self.assertEqual(client.patch('/api/auth/me/', {
                'nombre_completo': 'No debe cambiar',
            }, format='json').status_code, 401)
        for session in (first, second):
            self.assertEqual(self.refresh(session['refresh']).status_code, 401)
        self.assertEqual(self.client_for(other_session['access']).get('/api/auth/me/').status_code, 200)
        fresh = self.login(self.replacement)
        self.assertEqual(self.client_for(fresh['access']).get('/api/auth/me/').status_code, 200)

    def test_logout_revoca_solo_la_sesion_indicada(self):
        first, second = self.login(), self.login()
        client = self.client_for(first['access'])
        self.assertEqual(client.post('/api/auth/logout/', {'refresh': first['refresh']}, format='json').status_code, 200)
        self.assertEqual(client.get('/api/auth/me/').status_code, 401)
        self.assertEqual(self.refresh(first['refresh']).status_code, 401)
        self.assertEqual(self.client_for(second['access']).get('/api/auth/me/').status_code, 200)
        self.assertEqual(self.refresh(second['refresh']).status_code, 200)

    def test_cambio_password_conserva_sesion_solicitada(self):
        first, second = self.login(), self.login()
        client = self.client_for(first['access'])
        response = client.post('/api/auth/password/', {
            'password_actual': self.password, 'password_nueva': self.replacement,
            'refresh': first['refresh'],
        }, format='json')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(client.get('/api/auth/me/').status_code, 200)
        self.assertEqual(self.refresh(first['refresh']).status_code, 200)
        self.assertEqual(self.client_for(second['access']).get('/api/auth/me/').status_code, 401)
        self.assertEqual(self.refresh(second['refresh']).status_code, 401)

    def test_cambio_password_sin_refresh_revoca_todo(self):
        session = self.login()
        client = self.client_for(session['access'])
        self.assertEqual(client.post('/api/auth/password/', {
            'password_actual': self.password, 'password_nueva': self.replacement,
        }, format='json').status_code, 204)
        self.assertEqual(client.get('/api/auth/me/').status_code, 401)
        self.assertEqual(self.refresh(session['refresh']).status_code, 401)

    def test_tokens_anteriores_sin_sid_exigen_login(self):
        session = self.login()
        refresh = RefreshToken(session['refresh'])
        del refresh['sid']
        # Incluso un refresh legacy cuyo hash aun figura en BD se rechaza.
        Sesion.objects.filter(usuario=self.user).update(refresh_token_hash=token_hash(str(refresh)))
        self.assertEqual(self.refresh(str(refresh)).status_code, 401)
        self.assertEqual(self.client_for(str(refresh.access_token)).get('/api/auth/me/').status_code, 401)

    def test_sid_invalido_ajeno_inexistente_y_sesion_expirada(self):
        session = self.login()
        other = Usuario.objects.get(email='admin@amis.org')
        foreign = abrir_sesion(other, APIRequestFactory().post('/'))
        for sid in ('invalid', str(uuid.uuid4()), RefreshToken(foreign['refresh'])['sid']):
            with self.subTest(sid=sid):
                access = AccessToken(session['access'])
                access['sid'] = sid
                self.assertEqual(self.client_for(str(access)).get('/api/auth/me/').status_code, 401)
        Sesion.objects.filter(usuario=self.user).update(expira_en=timezone.now() - timezone.timedelta(seconds=1))
        self.assertEqual(self.client_for(session['access']).get('/api/auth/me/').status_code, 401)
        self.assertEqual(self.refresh(session['refresh']).status_code, 401)

    def test_fallo_al_revocar_revierte_password_y_canje(self):
        session = self.login()
        token = tokens.emitir(self.user, PropositoToken.RECUPERACION)
        previous = self.user.password_hash
        with patch('core.api.views.Sesion.objects.filter', side_effect=RuntimeError('QA revocation failure')):
            with self.assertRaises(RuntimeError):
                self.reset(token)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password_hash, previous)
        self.assertIsNone(TokenRecuperacion.objects.get(usuario=self.user, proposito=PropositoToken.RECUPERACION).usado_en)
        self.assertEqual(self.client_for(session['access']).get('/api/auth/me/').status_code, 200)
        self.assertEqual(self.reset(token).status_code, 204)

    def test_login_validado_antes_de_recuperacion_no_abre_sesion_despues(self):
        self.assertEqual(self.reset().status_code, 204)
        # Instancia con el hash anterior, como la que validaba LoginSerializer
        # antes de que otra peticion recuperara la cuenta.
        with self.assertRaises(AuthenticationFailed):
            abrir_sesion(self.user, APIRequestFactory().post('/'))
        self.assertFalse(Sesion.objects.filter(usuario=self.user, revocada_en__isnull=True).exists())
