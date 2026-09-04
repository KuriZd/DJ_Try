"""Envio de correo transaccional, con constancia de cada intento.

La regla que ordena todo este modulo: **un fallo de correo nunca tumba la
operacion que lo origino.** Si el SMTP no responde, el alta se completa igual y
el cobro se acredita igual; lo que queda es una fila en `envios_correo` con
estado `fallido` que el comando `reintentar_correos` recoge despues. Perder un
correo es molesto; perder un cobro por culpa de un correo seria indefendible.

De ahi que `enviar` no propague nunca una excepcion y devuelva el registro en
vez de un booleano: quien llama casi siempre lo ignora, y quien necesita saber
que paso lo tiene todo en la fila.
"""

import uuid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import EnvioCorreo, EstadoEnvio


class PlantillaCorreo:
    """Las tres piezas de un correo, juntas para que no se desparejen.

    El asunto vive aqui y no en la plantilla porque tiene que quedar en el
    registro, y leerlo del cuerpo renderizado seria dar un rodeo.
    """

    def __init__(self, clave, asunto, entidad=None):
        self.clave = clave
        self.asunto = asunto
        self.entidad = entidad


def _destinatario_real(destinatario):
    """A donde se entrega de verdad, que no siempre es a quien va dirigido.

    Con `EMAIL_REDIRIGIR_A` puesto, todo el correo aterriza en la cuenta de
    pruebas. El registro sigue guardando el destinatario original: lo que se
    anota es lo que la aplicacion decidio, no lo que hizo la jaula.
    """
    return settings.EMAIL_REDIRIGIR_A or destinatario


def _asunto_final(asunto, destinatario):
    """En la jaula, el asunto dice a quien iba el mensaje.

    Sin esto, veinte correos de prueba en la misma bandeja son
    indistinguibles entre si.
    """
    if not settings.EMAIL_REDIRIGIR_A:
        return asunto
    return f"[prueba -> {destinatario}] {asunto}"


def _construir(plantilla, destinatario, contexto):
    cuerpo_texto = render_to_string(f"correo/{plantilla.clave}.txt", contexto)
    mensaje = EmailMultiAlternatives(
        subject=_asunto_final(plantilla.asunto, destinatario),
        body=cuerpo_texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[_destinatario_real(destinatario)],
    )

    # La version HTML es opcional: un correo con solo texto se entrega igual, y
    # obligar a mantener las dos versiones de cada plantilla desde el principio
    # multiplicaria el trabajo sin necesidad.
    try:
        mensaje.attach_alternative(
            render_to_string(f"correo/{plantilla.clave}.html", contexto),
            "text/html",
        )
    except Exception:
        pass

    if settings.EMAIL_REDIRIGIR_A:
        mensaje.extra_headers["X-Destinatario-Real"] = destinatario

    return mensaje


def enviar(plantilla, destinatario, contexto, *, entidad_id=None):
    """Envia el correo y deja constancia. Nunca lanza.

    Devuelve la fila de `envios_correo`, ya en `enviado` o en `fallido`.
    """
    ahora = timezone.now()
    registro = EnvioCorreo.objects.create(
        id=uuid.uuid4(),
        plantilla=plantilla.clave,
        destinatario_email=destinatario,
        asunto=plantilla.asunto,
        entidad=plantilla.entidad,
        entidad_id=str(entidad_id) if entidad_id is not None else None,
        estado=EstadoEnvio.PENDIENTE,
        numero_intento=1,
        creado_en=ahora,
    )

    return _intentar(registro, plantilla, destinatario, contexto)


def _canal():
    """Por donde salio el mensaje, en corto: `smtp`, `console`, `locmem`...

    Se guarda porque `enviado` por si solo miente. El backend de consola acepta
    cualquier mensaje y devuelve exito sin contactar a nadie: sin esta marca,
    una fila en `enviado` se lee como "llego" cuando en realidad significa
    "se imprimio en una terminal". Es exactamente la confusion que costo una
    tarde la primera vez.
    """
    return settings.EMAIL_BACKEND.rsplit(".", 2)[-2]


def _intentar(registro, plantilla, destinatario, contexto):
    try:
        mensaje = _construir(plantilla, destinatario, contexto)
        mensaje.send(fail_silently=False)
    except Exception as error:
        registro.estado = EstadoEnvio.FALLIDO
        # `repr` y no `str`: un timeout de socket tiene mensaje vacio y sin el
        # tipo la fila no diria nada de lo que paso.
        registro.mensaje_error = repr(error)[:2000]
        registro.proveedor_id = _canal()
        registro.save(
            update_fields=["estado", "mensaje_error", "proveedor_id"]
        )
        return registro

    registro.estado = EstadoEnvio.ENVIADO
    registro.enviado_en = timezone.now()
    registro.mensaje_error = None
    registro.proveedor_id = _canal()
    registro.save(
        update_fields=["estado", "enviado_en", "mensaje_error", "proveedor_id"]
    )
    return registro


def reintentar(registro, plantilla, destinatario, contexto):
    """Vuelve a intentar un envio fallido, sumando uno al contador."""
    registro.numero_intento += 1
    registro.save(update_fields=["numero_intento"])
    return _intentar(registro, plantilla, destinatario, contexto)


def ya_se_envio(entidad, entidad_id):
    """Si ya salio un correo entregado para esa entidad.

    Segunda red de la idempotencia. La primera vive en quien llama —el cobro
    sabe si fue real por `cobrada_ahora`—, pero esto responde la pregunta sin
    depender de que quien llame se acuerde de hacersela.
    """
    return EnvioCorreo.objects.filter(
        entidad=entidad,
        entidad_id=str(entidad_id),
        estado=EstadoEnvio.ENVIADO,
    ).exists()
