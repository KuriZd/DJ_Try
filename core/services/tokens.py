"""Tokens de un solo uso para enlaces que viajan por correo.

Verificar un correo y recuperar una contrasena son el mismo mecanismo con dos
vigencias distintas, asi que comparten tabla, hashing y reglas. Escribir eso
dos veces solo daria dos sitios donde equivocarse.

Tres decisiones que sostienen la seguridad de todo esto:

- **En la base solo vive el hash.** La columna se llama `token_hash` por algo.
  Quien lea la tabla —un respaldo filtrado, un `SELECT` de mas— no obtiene
  nada con lo que entrar a una cuenta.
- **El valor legible existe una sola vez**, el instante que tarda en meterse en
  el enlace del correo. No se guarda, no se registra y no se puede recuperar.
- **Emitir invalida lo anterior.** Pedir un enlace nuevo apaga el viejo, que es
  lo que la gente espera al pulsar "reenviar", y ademas impide acumular
  tokens vivos de una misma cuenta.
"""

import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import PropositoToken, TokenRecuperacion


# 32 bytes en base64url. Mas que suficiente contra fuerza bruta y todavia
# corto para caber en una URL sin partirse en dos lineas del correo.
BYTES_TOKEN = 32

VIGENCIAS_HORAS = {
    PropositoToken.VERIFICACION: "TOKEN_VERIFICACION_HORAS",
    PropositoToken.RECUPERACION: "TOKEN_RECUPERACION_HORAS",
}


class TokenInvalido(Exception):
    """El token no existe, ya se uso, caduco o es de otro proposito.

    Los cuatro casos comparten excepcion a proposito: distinguirlos hacia
    fuera le diria a quien prueba tokens al azar cual de sus intentos estuvo
    mas cerca.
    """


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _vigencia(proposito):
    horas = getattr(settings, VIGENCIAS_HORAS[proposito], 1)
    return timezone.timedelta(hours=horas)


@transaction.atomic
def emitir(usuario, proposito):
    """Emite un token y devuelve su valor legible, que no vuelve a existir.

    Marca como usados los tokens vivos del mismo proposito antes de crear el
    nuevo. Es lo que hace que el indice unico parcial de la tabla no estorbe, y
    de paso lo que convierte "reenviar" en algo seguro.
    """
    ahora = timezone.now()

    TokenRecuperacion.objects.filter(
        usuario=usuario, proposito=proposito, usado_en__isnull=True
    ).update(usado_en=ahora)

    token = secrets.token_urlsafe(BYTES_TOKEN)
    TokenRecuperacion.objects.create(
        id=uuid.uuid4(),
        usuario=usuario,
        proposito=proposito,
        token_hash=_hash(token),
        expira_en=ahora + _vigencia(proposito),
        creado_en=ahora,
    )

    return token


@transaction.atomic
def canjear(token, proposito):
    """Devuelve el usuario del token y lo deja gastado.

    `select_for_update` cierra la ventana entre comprobar y marcar: dos
    peticiones con el mismo enlace —un doble clic, un precargador de enlaces
    del cliente de correo— no pueden canjearlo las dos.
    """
    if not token:
        raise TokenInvalido("El enlace no es valido.")

    fila = (
        TokenRecuperacion.objects.select_for_update()
        .select_related("usuario")
        .filter(token_hash=_hash(token), proposito=proposito)
        .first()
    )

    if fila is None or fila.usado_en is not None:
        raise TokenInvalido("El enlace no es valido o ya se uso.")

    if fila.expira_en <= timezone.now():
        raise TokenInvalido("El enlace caduco.")

    fila.usado_en = timezone.now()
    fila.save(update_fields=["usado_en"])

    return fila.usuario


def construir_enlace(ruta, token):
    """URL del frontend que abre quien recibe el correo.

    Apunta al frontend y no al API: lo que llega al buzon es una pagina, no un
    endpoint. La base sale de `FRONTEND_BASE_URL` y nunca de la peticion, para
    no reabrir una redireccion abierta a traves del correo.
    """
    return f"{settings.FRONTEND_BASE_URL}/{ruta.lstrip('/')}?token={token}"
