"""Los correos concretos que manda la aplicacion.

`correo.py` sabe enviar y dejar constancia; este modulo sabe *que* se dice y
*cuando*. Separarlos evita que la capa generica acabe conociendo el dominio de
pagos, y que `pagos.py` acabe armando plantillas.

Aqui viven tambien los reconstructores que usa `reintentar_correos`: solo los
correos sin secretos y derivables de la base pueden reintentarse.
"""

import zoneinfo
from decimal import Decimal, InvalidOperation

from django.conf import settings

from core.models import OrdenPagoPaypal
from core.services import correo, tokens


# En minuscula, que es como se escriben en espanol. El filtro `date` de Django
# los devuelve capitalizados incluso con LANGUAGE_CODE en es-mx, y el
# comprobante en pantalla —que usa Intl con es-MX— los escribe en minuscula:
# dos formas distintas de la misma fecha en el mismo comprobante.
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _fecha_larga(momento):
    """Fecha como la lee una persona, o None si no hay nada que escribir.

    Se traslada a la zona visual antes de formatear. Guardar en UTC es lo
    correcto; escribir en UTC haria que un cobro de las 20:00 en Mexico
    apareciera fechado al dia siguiente.
    """
    if momento is None:
        return None

    local = momento.astimezone(
        zoneinfo.ZoneInfo(settings.ZONA_HORARIA_VISUAL)
    )
    return f"{local.day} de {MESES[local.month - 1]} de {local.year}"


RECUPERACION = correo.PlantillaCorreo(
    clave="recuperacion",
    asunto="Recupera el acceso a tu cuenta",
    entidad="usuario",
)

COMPROBANTE_PAGO = correo.PlantillaCorreo(
    clave="comprobante_pago",
    asunto="Comprobante de tu compra",
    entidad="orden_pago",
)


def _importe(monto, moneda):
    """Importe legible, o None si no hay nada que mostrar.

    La guarda de nulos va antes y aparte: `Decimal(None)` revienta y
    `float(None)` tambien, pero `0` seria peor que ambos —afirmaria que la
    persona no pago nada— asi que la ausencia se propaga como ausencia.
    """
    if monto is None or monto == "":
        return None

    try:
        cantidad = Decimal(str(monto))
    except (InvalidOperation, ValueError):
        return None

    if moneda and moneda != "MXN":
        return f"{cantidad:,.2f} {moneda}"
    return f"${cantidad:,.2f} MXN"


def contexto_comprobante(orden):
    """Lo que el correo necesita saber de una orden ya cobrada.

    Es el mismo contenido que el comprobante en pantalla: quien recibe el
    correo y quien mira la aplicacion tienen que ver lo mismo, o el folio deja
    de servir para reclamar.
    """
    compra = orden.compra
    pruebas = compra.cantidad_pruebas if compra else 0

    return {
        "nombre": orden.comprador.nombre_completo,
        "folio": orden.referencia_interna,
        "paquete": compra.paquete_nombre if compra else None,
        "pruebas": pruebas,
        "importe": _importe(
            compra.monto if compra else orden.monto,
            compra.moneda if compra else orden.moneda,
        ),
        "pagado_en": _fecha_larga(orden.pagado_en),
        "vigente_hasta": _fecha_larga(compra.vigente_hasta) if compra else None,
        "enlace": f"{settings.FRONTEND_BASE_URL}/psicometricos/mis-pruebas",
    }


def enviar_comprobante(orden):
    """Manda el comprobante de una orden cobrada. Nunca lanza.

    Se llama desde `transaction.on_commit`, de modo que si la transaccion que
    acredito la compra se deshace, este correo no llega a existir. Avisar de un
    cobro que despues no cuajo seria peor que no avisar.

    La guarda de `ya_se_envio` es la segunda red. La primera es `cobrada_ahora`
    en quien llama, que ya distingue el cobro real de la repeticion; esta cubre
    el caso de que alguien anada mas adelante otro camino que olvide mirarlo.
    """
    if correo.ya_se_envio(COMPROBANTE_PAGO.entidad, orden.referencia_interna):
        return None

    return correo.enviar(
        COMPROBANTE_PAGO,
        orden.comprador.email,
        contexto_comprobante(orden),
        entidad_id=orden.referencia_interna,
    )


def enviar_recuperacion(usuario, token):
    """Manda el enlace para elegir una contrasena nueva. Nunca lanza.

    Recibe el token ya emitido en vez de emitirlo: quien llama necesita saber
    si hubo cuenta o no, y este modulo solo se ocupa de redactar.
    """
    return correo.enviar(
        RECUPERACION,
        usuario.email,
        {
            "nombre": usuario.nombre_completo,
            "enlace": tokens.construir_enlace("/restablecer", token),
            "horas": settings.TOKEN_RECUPERACION_HORAS,
        },
        entidad_id=usuario.id,
    )


def reconstruir_comprobante(registro):
    """Rehace el comprobante desde la orden, para `reintentar_correos`.

    Es reintentable justamente porque no lleva secretos: todo sale de la orden,
    que sigue en la base. Los correos con token no pueden decir lo mismo y por
    eso no tienen reconstructor.
    """
    orden = (
        OrdenPagoPaypal.objects.select_related("compra", "comprador")
        .filter(referencia_interna=registro.entidad_id)
        .first()
    )
    if orden is None:
        return None

    return (
        COMPROBANTE_PAGO,
        orden.comprador.email,
        contexto_comprobante(orden),
    )


# Registro que consulta el comando de reintentos. Solo entra lo que se puede
# volver a derivar de la base sin resucitar un secreto.
RECONSTRUCTORES = {
    COMPROBANTE_PAGO.clave: reconstruir_comprobante,
}
