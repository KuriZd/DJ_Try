import calendar
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    CompraPaquetePsicometrico,
    EstadoCompraPaquete,
    EstadoPagoPaypal,
    EstadoReportePsicometrico,
    EventoPagoPaypal,
    OrdenPagoPaypal,
    OrigenEventoPago,
    OrigenReportePsicometrico,
    ReportePsicometrico,
    TipoTransaccionPaypal,
    TransaccionPagoPaypal,
)

from .paypal import PaypalError, paypal_client


ESTADOS_ORDEN_ACTIVA = (
    EstadoPagoPaypal.PENDING,
    EstadoPagoPaypal.CREATED,
    EstadoPagoPaypal.APPROVED,
    EstadoPagoPaypal.COMPLETED,
)

# Estados en que PayPal ya tiene la orden y el pagador puede aprobarla, que es
# lo unico que se puede cobrar.
ESTADOS_ORDEN_COBRABLE = (
    EstadoPagoPaypal.CREATED,
    EstadoPagoPaypal.APPROVED,
)


class PagoNoDisponible(Exception):
    pass


class IdempotenciaEnConflicto(Exception):
    pass


class CapturaNoAplicable(Exception):
    """La orden no esta en un estado desde el que se pueda cobrar."""


class MontoCapturadoDistinto(Exception):
    """PayPal cobro un importe que no es el que la orden reservo.

    No se entrega nada y la orden queda marcada para revision: entregar por un
    monto que no cuadra es peor que dejar el caso abierto.
    """


def _referencia_interna():
    fecha = timezone.now().strftime("%Y%m%d")
    return f"PAY-{fecha}-{uuid.uuid4().hex[:12].upper()}"


def _sumar_meses(fecha, meses):
    """La misma fecha `meses` despues, sin dependencias extra.

    Se recorta al ultimo dia del mes destino: el 31 de enero mas un mes es el
    28 (o 29) de febrero, no un 31 que no existe.
    """
    indice = fecha.month - 1 + meses
    anio = fecha.year + indice // 12
    mes = indice % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


def _a_decimal(valor):
    """Decimal, o None si PayPal no mando el campo o mando algo ilegible."""
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _registrar_evento(
    *, orden, tipo_evento, estado_anterior, estado_nuevo, payload, procesado=True,
    mensaje_error=None, transaccion=None, origen=OrigenEventoPago.API,
    paypal_event_id=None,
):
    ahora = timezone.now()
    EventoPagoPaypal.objects.create(
        id=uuid.uuid4(),
        orden=orden,
        transaccion=transaccion,
        paypal_event_id=paypal_event_id,
        tipo_evento=tipo_evento,
        origen=origen,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        payload=payload,
        procesado=procesado,
        mensaje_error=mensaje_error,
        recibido_en=ahora,
        procesado_en=ahora if procesado else None,
        creado_en=ahora,
    )


def _decidir_sobre_orden_activa(activa, *, misma_clave, ahora, limite_pending):
    """Que hacer con una orden que ya existe para esta misma compra.

    Devuelve `(orden, procesar_en_paypal, nueva_local)` cuando la orden sirve,
    o `None` cuando hay que crear una nueva. En ese ultimo caso deja la vieja
    cancelada: es lo unico que libera el indice de orden activa.
    """
    if activa.estado == EstadoPagoPaypal.COMPLETED:
        return activa, False, False

    if activa.estado == EstadoPagoPaypal.PENDING:
        if activa.actualizado_en >= limite_pending:
            return activa, False, False
        # Recuperar una llamada interrumpida con el MISMO request id.
        # PayPal devolvera la orden original en vez de duplicarla.
        activa.actualizado_en = ahora
        activa.mensaje_error = None
        activa.save(update_fields=["actualizado_en", "mensaje_error"])
        return activa, True, False

    if activa.expira_en is None or activa.expira_en > ahora:
        return activa, False, False

    if misma_clave:
        raise IdempotenciaEnConflicto(
            "La orden expiró; usa una nueva clave de idempotencia."
        )

    activa.estado = EstadoPagoPaypal.CANCELLED
    activa.actualizado_en = ahora
    activa.mensaje_error = "Orden local expirada antes de completarse."
    activa.save(update_fields=["estado", "actualizado_en", "mensaje_error"])
    return None


def _orden_con_misma_clave(*, comprador, clave_idempotencia):
    if not clave_idempotencia:
        return None
    return (
        OrdenPagoPaypal.objects.select_for_update()
        .filter(comprador=comprador, clave_idempotencia=clave_idempotencia)
        .first()
    )


def _reservar_orden(*, reporte, comprador, clave_idempotencia, ip, user_agent):
    ahora = timezone.now()
    limite_pending = ahora - timedelta(
        minutes=settings.PAYPAL_PENDING_TIMEOUT_MINUTES
    )

    with transaction.atomic():
        reporte = (
            ReportePsicometrico.objects.select_for_update()
            .select_related("aspirante")
            .get(pk=reporte.pk)
        )
        if (
            reporte.aspirante.usuario_id != comprador.id
            or reporte.estado != EstadoReportePsicometrico.DISPONIBLE
            or reporte.origen != OrigenReportePsicometrico.PLATAFORMA
            or not reporte.disponible_para_compra
            or reporte.precio <= 0
        ):
            raise PagoNoDisponible("El reporte no puede adquirirse.")

        misma_clave = _orden_con_misma_clave(
            comprador=comprador, clave_idempotencia=clave_idempotencia
        )
        if misma_clave:
            if misma_clave.reporte_id != reporte.id:
                raise IdempotenciaEnConflicto(
                    "La clave de idempotencia ya se usó para otro reporte."
                )
            if misma_clave.estado not in ESTADOS_ORDEN_ACTIVA:
                raise IdempotenciaEnConflicto(
                    "La solicitud anterior terminó; usa una nueva clave de idempotencia."
                )

        activa = misma_clave or (
            OrdenPagoPaypal.objects.select_for_update()
            .filter(
                comprador=comprador,
                reporte=reporte,
                estado__in=ESTADOS_ORDEN_ACTIVA,
            )
            .order_by("-creado_en")
            .first()
        )
        if activa:
            decision = _decidir_sobre_orden_activa(
                activa,
                misma_clave=misma_clave,
                ahora=ahora,
                limite_pending=limite_pending,
            )
            if decision:
                return decision

        orden = OrdenPagoPaypal.objects.create(
            id=uuid.uuid4(),
            referencia_interna=_referencia_interna(),
            comprador=comprador,
            reporte=reporte,
            referencia_evaluacion_externa=(
                reporte.referencia_evaluacion_externa
            ),
            monto=reporte.precio,
            moneda=reporte.moneda.upper(),
            estado=EstadoPagoPaypal.PENDING,
            clave_idempotencia=clave_idempotencia,
            paypal_request_id=str(uuid.uuid4()),
            ip=ip,
            user_agent=(user_agent or "")[:2000],
            expira_en=ahora + timedelta(hours=settings.PAYPAL_ORDER_TTL_HOURS),
            creado_en=ahora,
            actualizado_en=ahora,
        )
        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.RESERVED",
            estado_anterior=None,
            estado_nuevo=EstadoPagoPaypal.PENDING,
            payload={"reporte_id": str(reporte.id)},
        )
        return orden, True, True


def _reservar_orden_paquete(
    *, paquete, cantidad, comprador, clave_idempotencia, ip, user_agent
):
    """Crea la compra en PENDIENTE y la orden que va a cobrarla.

    A diferencia de un reporte, un paquete se puede comprar muchas veces, asi
    que no hay una orden "activa" que reutilizar por omision: solo se recupera
    la que quedo en PENDING sin llegar a PayPal, o la que senale la clave de
    idempotencia. Sin esa clave, dos peticiones son dos compras.
    """
    ahora = timezone.now()
    limite_pending = ahora - timedelta(
        minutes=settings.PAYPAL_PENDING_TIMEOUT_MINUTES
    )

    if not paquete.activo:
        raise PagoNoDisponible("Ese paquete ya no está a la venta.")

    try:
        cantidad_pruebas, monto = paquete.cotizar(cantidad)
    except ValueError as error:
        raise PagoNoDisponible(str(error)) from error

    with transaction.atomic():
        misma_clave = _orden_con_misma_clave(
            comprador=comprador, clave_idempotencia=clave_idempotencia
        )
        if misma_clave:
            compra_previa = misma_clave.compra
            if (
                compra_previa is None
                or compra_previa.paquete_id != paquete.clave
                or compra_previa.cantidad_pruebas != cantidad_pruebas
            ):
                raise IdempotenciaEnConflicto(
                    "La clave de idempotencia ya se usó para otra compra."
                )
            if misma_clave.estado not in ESTADOS_ORDEN_ACTIVA:
                raise IdempotenciaEnConflicto(
                    "La solicitud anterior terminó; usa una nueva clave de idempotencia."
                )
            activa = misma_clave
        else:
            # Una orden en PENDING nunca llego a PayPal: es un intento
            # interrumpido, no una segunda compra.
            compras_pendientes = list(
                CompraPaquetePsicometrico.objects.filter(
                    comprador=comprador,
                    paquete=paquete,
                    cantidad_pruebas=cantidad_pruebas,
                    estado=EstadoCompraPaquete.PENDIENTE,
                ).values_list("id", flat=True)
            )
            activa = (
                OrdenPagoPaypal.objects.select_for_update()
                .filter(
                    comprador=comprador,
                    compra_id__in=compras_pendientes,
                    estado=EstadoPagoPaypal.PENDING,
                )
                .order_by("-creado_en")
                .first()
                if compras_pendientes
                else None
            )

        if activa:
            decision = _decidir_sobre_orden_activa(
                activa,
                misma_clave=misma_clave,
                ahora=ahora,
                limite_pending=limite_pending,
            )
            if decision:
                return decision
            # La orden vieja quedo cancelada; su compra no puede seguir viva.
            CompraPaquetePsicometrico.objects.filter(
                pk=activa.compra_id, estado=EstadoCompraPaquete.PENDIENTE
            ).update(
                estado=EstadoCompraPaquete.CANCELADA, actualizado_en=ahora
            )

        compra = CompraPaquetePsicometrico.objects.create(
            id=uuid.uuid4(),
            comprador=comprador,
            paquete=paquete,
            paquete_nombre=paquete.nombre,
            cantidad_pruebas=cantidad_pruebas,
            precio_unitario=paquete.precio_unitario,
            monto=monto,
            moneda=paquete.moneda.upper(),
            estado=EstadoCompraPaquete.PENDIENTE,
            creditos_totales=cantidad_pruebas,
            creditos_consumidos=0,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        orden = OrdenPagoPaypal.objects.create(
            id=uuid.uuid4(),
            referencia_interna=_referencia_interna(),
            comprador=comprador,
            compra=compra,
            monto=compra.monto,
            moneda=compra.moneda,
            estado=EstadoPagoPaypal.PENDING,
            clave_idempotencia=clave_idempotencia,
            paypal_request_id=str(uuid.uuid4()),
            ip=ip,
            user_agent=(user_agent or "")[:2000],
            expira_en=ahora + timedelta(hours=settings.PAYPAL_ORDER_TTL_HOURS),
            creado_en=ahora,
            actualizado_en=ahora,
        )
        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.RESERVED",
            estado_anterior=None,
            estado_nuevo=EstadoPagoPaypal.PENDING,
            payload={
                "compra_id": str(compra.id),
                "paquete": paquete.clave,
                "cantidad_pruebas": cantidad_pruebas,
            },
        )
        return orden, True, True


def _guardar_fallo_al_crear(orden, error):
    ahora = timezone.now()
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        anterior = orden.estado
        # Un timeout no demuestra que PayPal no haya creado la orden. Se
        # conserva PENDING para reintentar luego con el mismo request id.
        estado_error = (
            EstadoPagoPaypal.PENDING
            if error.code
            in {"PAYPAL_CONNECTION_ERROR", "PAYPAL_APPROVAL_URL_MISSING"}
            else EstadoPagoPaypal.FAILED
        )
        orden.estado = estado_error
        orden.codigo_error = error.code
        orden.mensaje_error = str(error)
        orden.respuesta_proveedor = error.payload
        orden.actualizado_en = ahora
        orden.save(
            update_fields=[
                "estado",
                "codigo_error",
                "mensaje_error",
                "respuesta_proveedor",
                "actualizado_en",
            ]
        )
        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.CREATE_FAILED",
            estado_anterior=anterior,
            estado_nuevo=estado_error,
            payload=error.payload,
            mensaje_error=str(error),
        )


def _crear_en_paypal(orden, *, descripcion):
    """Manda a PayPal una orden ya reservada y guarda lo que responda."""
    try:
        resultado = paypal_client.crear_orden(
            referencia=orden.referencia_interna,
            request_id=orden.paypal_request_id,
            monto=orden.monto,
            moneda=orden.moneda,
            descripcion=descripcion,
        )
    except PaypalError as error:
        _guardar_fallo_al_crear(orden, error)
        raise

    ahora = timezone.now()
    estado_paypal = resultado["estado"]
    estado = (
        estado_paypal
        if estado_paypal in {EstadoPagoPaypal.CREATED, EstadoPagoPaypal.APPROVED}
        else EstadoPagoPaypal.CREATED
    )
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        anterior = orden.estado
        orden.paypal_order_id = resultado["paypal_order_id"]
        orden.approval_url = resultado["approval_url"]
        orden.estado = estado
        orden.respuesta_proveedor = resultado["respuesta"]
        orden.actualizado_en = ahora
        orden.save(
            update_fields=[
                "paypal_order_id",
                "approval_url",
                "estado",
                "respuesta_proveedor",
                "actualizado_en",
            ]
        )
        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.CREATED",
            estado_anterior=anterior,
            estado_nuevo=estado,
            payload=resultado["respuesta"],
        )
    return orden


def iniciar_pago_paypal(
    *, reporte, comprador, clave_idempotencia=None, ip=None, user_agent=None
):
    orden, procesar_en_paypal, nueva_local = _reservar_orden(
        reporte=reporte,
        comprador=comprador,
        clave_idempotencia=clave_idempotencia,
        ip=ip,
        user_agent=user_agent,
    )
    if not procesar_en_paypal:
        return orden, False

    referencia = orden.referencia_evaluacion_externa or orden.reporte_id
    orden = _crear_en_paypal(
        orden, descripcion=f"Reporte psicométrico {referencia}"
    )
    return orden, nueva_local


def iniciar_pago_paquete(
    *,
    paquete,
    comprador,
    cantidad=None,
    clave_idempotencia=None,
    ip=None,
    user_agent=None,
):
    orden, procesar_en_paypal, nueva_local = _reservar_orden_paquete(
        paquete=paquete,
        cantidad=cantidad,
        comprador=comprador,
        clave_idempotencia=clave_idempotencia,
        ip=ip,
        user_agent=user_agent,
    )
    if not procesar_en_paypal:
        return orden, False

    compra = orden.compra
    unidad = "prueba" if compra.cantidad_pruebas == 1 else "pruebas"
    orden = _crear_en_paypal(
        orden,
        descripcion=(
            f"{compra.paquete_nombre}: {compra.cantidad_pruebas} {unidad} "
            "psicométricas"
        ),
    )
    return orden, nueva_local


# ---------------------------------------------------------------------------
# Cobro
# ---------------------------------------------------------------------------


def _guardar_fallo_al_capturar(orden, error):
    ahora = timezone.now()
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        anterior = orden.estado
        # Un corte de red no dice si PayPal cobro o no. La orden se queda como
        # estaba para que un reintento —con el mismo request id— lo averigue;
        # pasarla a FAILED aqui borraria un cobro que quiza si ocurrio.
        estado = (
            anterior
            if error.code == "PAYPAL_CONNECTION_ERROR"
            else EstadoPagoPaypal.FAILED
        )
        orden.estado = estado
        orden.codigo_error = error.code
        orden.mensaje_error = str(error)
        orden.actualizado_en = ahora
        orden.save(
            update_fields=[
                "estado",
                "codigo_error",
                "mensaje_error",
                "actualizado_en",
            ]
        )
        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.CAPTURE_FAILED",
            estado_anterior=anterior,
            estado_nuevo=estado,
            payload=error.payload,
            mensaje_error=str(error),
        )


def _marcar_desajuste_de_monto(orden, resultado, desajuste, estado_anterior):
    """Deja la orden senalada para revision, sin entregar y sin cancelar.

    El estado no se toca: hay dinero de por medio y quien lo revise necesita
    ver la orden tal como quedo, con el motivo escrito al lado.
    """
    ahora = timezone.now()
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        orden.codigo_error = "MONTO_CAPTURADO_DISTINTO"
        orden.mensaje_error = str(desajuste)
        orden.respuesta_proveedor = resultado.get("respuesta") or {}
        orden.actualizado_en = ahora
        orden.save(
            update_fields=[
                "codigo_error",
                "mensaje_error",
                "respuesta_proveedor",
                "actualizado_en",
            ]
        )
        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.CAPTURE_MISMATCH",
            estado_anterior=estado_anterior,
            estado_nuevo=orden.estado,
            payload=resultado.get("respuesta") or {},
            mensaje_error=str(desajuste),
            procesado=False,
        )


def _entregar_compra(orden, ahora):
    """Convierte la compra pagada en creditos utilizables.

    Un reporte suelto no necesita nada: que este pagado se sabe porque existe
    una orden COMPLETED suya, y esa es la orden que acaba de completarse.
    """
    if orden.compra_id is None:
        return

    # Sin `select_related`: un FOR UPDATE con join tambien bloquearia la fila
    # del catalogo, que la comparten todas las compras de ese paquete.
    compra = CompraPaquetePsicometrico.objects.select_for_update().get(
        pk=orden.compra_id
    )
    if compra.estado == EstadoCompraPaquete.PAGADA:
        return

    compra.estado = EstadoCompraPaquete.PAGADA
    compra.pagada_en = ahora
    if compra.paquete.vigencia_meses:
        compra.vigente_hasta = _sumar_meses(ahora, compra.paquete.vigencia_meses)
    compra.actualizado_en = ahora
    compra.save(
        update_fields=["estado", "pagada_en", "vigente_hasta", "actualizado_en"]
    )


def _registrar_transaccion(orden, resultado, ahora, estado):
    """Guarda la captura, o devuelve la que ya estuviera guardada.

    El indice unico sobre `paypal_capture_id` es la ultima defensa contra
    apuntar dos veces el mismo cobro: si dos peticiones capturan a la vez,
    PayPal devuelve la misma captura a las dos y aqui solo entra una.
    """
    capture_id = resultado.get("paypal_capture_id")
    if capture_id:
        existente = TransaccionPagoPaypal.objects.filter(
            tipo=TipoTransaccionPaypal.CAPTURE, paypal_capture_id=capture_id
        ).first()
        if existente:
            # Un cobro que PayPal habia retenido llega aqui otra vez cuando lo
            # libera. No es una captura nueva, es la misma que cambio de
            # estado: se actualiza en su sitio, y con ella la comision y el
            # neto, que solo aparecen cuando el dinero se liquida.
            if existente.estado != estado:
                existente.estado = estado
                existente.comision = _a_decimal(resultado.get("comision"))
                existente.monto_neto = _a_decimal(resultado.get("monto_neto"))
                existente.respuesta_proveedor = resultado.get("respuesta") or {}
                existente.procesada_en = ahora
                existente.actualizado_en = ahora
                existente.save(
                    update_fields=[
                        "estado",
                        "comision",
                        "monto_neto",
                        "respuesta_proveedor",
                        "procesada_en",
                        "actualizado_en",
                    ]
                )
            return existente

    return TransaccionPagoPaypal.objects.create(
        id=uuid.uuid4(),
        orden=orden,
        tipo=TipoTransaccionPaypal.CAPTURE,
        paypal_capture_id=capture_id,
        monto=_a_decimal(resultado.get("monto")) or orden.monto,
        moneda=(resultado.get("moneda") or orden.moneda).upper(),
        estado=estado,
        comision=_a_decimal(resultado.get("comision")),
        monto_neto=_a_decimal(resultado.get("monto_neto")),
        respuesta_proveedor=resultado.get("respuesta") or {},
        procesada_en=ahora,
        creado_en=ahora,
        actualizado_en=ahora,
    )


def _verificar_monto(orden, resultado):
    """PayPal tiene que haber cobrado exactamente lo que la orden reservo.

    El importe se manda desde el backend, pero se comprueba de vuelta: entre
    la creacion y el cobro pasa por el navegador de una persona y por PayPal,
    y entregar mercancia por un monto que no cuadra no tiene reverso.
    """
    cobrado = _a_decimal(resultado.get("monto"))
    moneda = (resultado.get("moneda") or "").upper()
    if cobrado is None and not moneda:
        # PayPal no desglosó el importe; no hay nada que contradecir.
        return
    if cobrado != orden.monto or moneda != orden.moneda.upper():
        raise MontoCapturadoDistinto(
            f"PayPal cobró {cobrado} {moneda or '?'} y la orden es por "
            f"{orden.monto} {orden.moneda}."
        )


def aplicar_resultado_de_captura(
    orden,
    resultado,
    *,
    estado_anterior,
    origen=OrigenEventoPago.API,
    paypal_event_id=None,
):
    """Lleva la orden al estado que dicta lo que PayPal contesto.

    Es el unico camino por el que una orden llega a COMPLETED y se entrega lo
    comprado, venga la noticia del navegador o de un webhook. Tratar el dinero
    distinto segun por donde entra el aviso seria la forma mas facil de
    entregar dos veces, o ninguna.

    Devuelve `(orden, cobrada_ahora)`.
    """
    estado_captura = resultado.get("estado_captura")
    ahora = timezone.now()

    if estado_captura == "PENDING":
        # PayPal retuvo el cobro para revisarlo. No hay dinero todavia, asi
        # que no se entrega nada: lo destraba despues el webhook.
        with transaction.atomic():
            orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
            transaccion_pendiente = _registrar_transaccion(
                orden, resultado, ahora, EstadoPagoPaypal.PENDING
            )
            orden.estado = EstadoPagoPaypal.APPROVED
            orden.respuesta_proveedor = resultado.get("respuesta") or {}
            orden.actualizado_en = ahora
            orden.save(
                update_fields=["estado", "respuesta_proveedor", "actualizado_en"]
            )
            _registrar_evento(
                orden=orden,
                tipo_evento="ORDER.CAPTURE_PENDING",
                estado_anterior=estado_anterior,
                estado_nuevo=EstadoPagoPaypal.APPROVED,
                payload=resultado.get("respuesta") or {},
                transaccion=transaccion_pendiente,
                procesado=False,
                origen=origen,
                paypal_event_id=paypal_event_id,
            )
        return orden, False

    if estado_captura != "COMPLETED":
        error = PaypalError(
            "PayPal no completó el cobro.",
            code=estado_captura or "PAYPAL_CAPTURE_NOT_COMPLETED",
            payload=resultado.get("respuesta") or {},
        )
        _guardar_fallo_al_capturar(orden, error)
        raise error

    # El importe se comprueba antes de abrir la transaccion que entrega: si se
    # lanzara desde dentro, el rollback se llevaria por delante la marca de
    # revision que se acaba de escribir.
    try:
        _verificar_monto(orden, resultado)
    except MontoCapturadoDistinto as desajuste:
        _marcar_desajuste_de_monto(orden, resultado, desajuste, estado_anterior)
        raise

    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        if orden.estado == EstadoPagoPaypal.COMPLETED:
            return orden, False

        transaccion_captura = _registrar_transaccion(
            orden, resultado, ahora, EstadoPagoPaypal.COMPLETED
        )
        orden.estado = EstadoPagoPaypal.COMPLETED
        orden.pagado_en = ahora
        orden.codigo_error = None
        orden.mensaje_error = None
        orden.respuesta_proveedor = resultado.get("respuesta") or {}
        orden.actualizado_en = ahora
        orden.save(
            update_fields=[
                "estado",
                "pagado_en",
                "codigo_error",
                "mensaje_error",
                "respuesta_proveedor",
                "actualizado_en",
            ]
        )
        _entregar_compra(orden, ahora)
        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.CAPTURED",
            estado_anterior=estado_anterior,
            estado_nuevo=EstadoPagoPaypal.COMPLETED,
            payload=resultado.get("respuesta") or {},
            transaccion=transaccion_captura,
            origen=origen,
            paypal_event_id=paypal_event_id,
        )

    return orden, True


def capturar_pago_paypal(*, orden):
    """Cobra una orden aprobada y entrega lo comprado.

    Devuelve `(orden, cobrada_ahora)`. `cobrada_ahora` en False significa que
    ya estaba cobrada: volver de PayPal dos veces, o recargar la pagina de
    retorno, no debe verse como un error ni cobrar de nuevo.
    """
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        if orden.estado == EstadoPagoPaypal.COMPLETED:
            return orden, False
        if orden.estado not in ESTADOS_ORDEN_COBRABLE:
            raise CapturaNoAplicable(
                "Esta orden no está en condiciones de cobrarse."
            )
        if not orden.paypal_order_id:
            raise CapturaNoAplicable("La orden todavía no existe en PayPal.")
        estado_anterior = orden.estado

    # La llamada a PayPal va fuera de la transaccion: sostener un lock de fila
    # durante una peticion HTTP bloquea la tabla el tiempo que tarde la red.
    try:
        resultado = paypal_client.capturar_orden(
            paypal_order_id=orden.paypal_order_id,
            request_id=orden.paypal_request_id or str(orden.id),
        )
    except PaypalError as error:
        if error.code != "ORDER_ALREADY_CAPTURED":
            _guardar_fallo_al_capturar(orden, error)
            raise
        # Alguien mas ya cobro esta orden —otra pestaña, el webhook—; la
        # verdad esta en PayPal, asi que se lee de ahi.
        resultado = paypal_client.obtener_orden(
            paypal_order_id=orden.paypal_order_id
        )

    return aplicar_resultado_de_captura(
        orden, resultado, estado_anterior=estado_anterior
    )


def cancelar_pago_paypal(*, orden):
    """Cierra una orden que la persona abandono en PayPal.

    Devuelve `(orden, cancelada_ahora)`. Una orden ya cobrada no se toca: que
    el navegador pase por la URL de cancelacion despues de pagar es un caso
    real, y no puede deshacer el cobro.
    """
    ahora = timezone.now()
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        if orden.estado == EstadoPagoPaypal.COMPLETED:
            return orden, False
        if orden.estado == EstadoPagoPaypal.CANCELLED:
            return orden, False
        if orden.estado not in ESTADOS_ORDEN_ACTIVA:
            return orden, False

        anterior = orden.estado
        orden.estado = EstadoPagoPaypal.CANCELLED
        orden.mensaje_error = "Cancelada por la persona compradora."
        orden.actualizado_en = ahora
        orden.save(update_fields=["estado", "mensaje_error", "actualizado_en"])

        if orden.compra_id:
            CompraPaquetePsicometrico.objects.filter(
                pk=orden.compra_id, estado=EstadoCompraPaquete.PENDIENTE
            ).update(
                estado=EstadoCompraPaquete.CANCELADA, actualizado_en=ahora
            )

        _registrar_evento(
            orden=orden,
            tipo_evento="ORDER.CANCELLED",
            estado_anterior=anterior,
            estado_nuevo=EstadoPagoPaypal.CANCELLED,
            payload={},
        )

    return orden, True


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class FirmaWebhookInvalida(Exception):
    """El evento no viene firmado por PayPal, o la firma no cuadra."""


# Lo que este endpoint sabe atender. Todo lo demas se acusa de recibido y se
# archiva: contestar un error a un evento que no nos interesa solo consigue
# que PayPal lo reintente durante dias.
EVENTOS_ATENDIDOS = {
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
}

EVENTOS_DE_COBRO = {
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.PENDING",
}

EVENTOS_DE_DEVOLUCION = {
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
}


def _orden_del_evento(evento):
    """La orden local a la que se refiere el aviso.

    PayPal identifica la orden de tres maneras segun el tipo de evento, y no
    todas vienen siempre. Se prueban en orden de fiabilidad y se acaba en
    `custom_id`, que es la referencia interna que nosotros mismos pusimos al
    crear la orden: es la unica que no depende de que PayPal adjunte sus ids
    relacionados.
    """
    recurso = evento.get("resource") or {}
    tipo = evento.get("event_type") or ""

    if tipo.startswith("CHECKOUT.ORDER."):
        paypal_order_id = recurso.get("id")
        unidades = recurso.get("purchase_units") or [{}]
        referencia = unidades[0].get("custom_id")
    else:
        relacionados = (recurso.get("supplementary_data") or {}).get(
            "related_ids"
        ) or {}
        paypal_order_id = relacionados.get("order_id")
        referencia = recurso.get("custom_id") or recurso.get("invoice_id")

    if paypal_order_id:
        orden = OrdenPagoPaypal.objects.filter(
            paypal_order_id=paypal_order_id
        ).first()
        if orden:
            return orden

    if referencia:
        return OrdenPagoPaypal.objects.filter(
            referencia_interna=referencia
        ).first()

    return None


def _reclamar_evento(evento):
    """Aparta el evento antes de tocar nada. `None` si ya estaba apartado.

    El indice unico sobre `paypal_event_id` es lo que hace idempotente al
    endpoint: PayPal reintenta un aviso hasta que le contestemos 2xx, y dos
    entregas del mismo cobro no pueden entregar dos veces la mercancia.
    """
    ahora = timezone.now()
    try:
        with transaction.atomic():
            return EventoPagoPaypal.objects.create(
                id=uuid.uuid4(),
                paypal_event_id=evento.get("id"),
                tipo_evento=evento.get("event_type") or "DESCONOCIDO",
                origen=OrigenEventoPago.WEBHOOK,
                payload=evento,
                procesado=False,
                recibido_en=ahora,
                creado_en=ahora,
            )
    except IntegrityError:
        return None


def _cerrar_evento(
    fila,
    *,
    orden=None,
    estado_anterior=None,
    estado_nuevo=None,
    procesado=True,
    mensaje_error=None,
    transaccion=None,
):
    ahora = timezone.now()
    fila.orden = orden
    fila.transaccion = transaccion
    fila.estado_anterior = estado_anterior
    fila.estado_nuevo = estado_nuevo
    fila.procesado = procesado
    fila.mensaje_error = mensaje_error
    fila.procesado_en = ahora if procesado else None
    fila.save(
        update_fields=[
            "orden",
            "transaccion",
            "estado_anterior",
            "estado_nuevo",
            "procesado",
            "mensaje_error",
            "procesado_en",
        ]
    )


def _denegar_orden(orden, ahora):
    """PayPal rechazo el cobro: no hay dinero y no hay nada que entregar."""
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        orden.estado = EstadoPagoPaypal.FAILED
        orden.codigo_error = "PAYPAL_CAPTURE_DENIED"
        orden.mensaje_error = "PayPal rechazó el cobro."
        orden.actualizado_en = ahora
        orden.save(
            update_fields=[
                "estado",
                "codigo_error",
                "mensaje_error",
                "actualizado_en",
            ]
        )
        if orden.compra_id:
            CompraPaquetePsicometrico.objects.filter(
                pk=orden.compra_id, estado=EstadoCompraPaquete.PENDIENTE
            ).update(
                estado=EstadoCompraPaquete.CANCELADA, actualizado_en=ahora
            )
    return orden


def _reembolsar_orden(orden, evento, ahora):
    """Anota la devolucion y retira lo que la compra habia entregado.

    La compra pasa a REEMBOLSADA y no se le tocan los creditos consumidos:
    son historia de lo que la persona ya uso. Quien reparta creditos tiene que
    mirar el estado, no el saldo, y por eso el indice de saldo disponible sólo
    cuenta las compras PAGADAS.

    Devuelve `(orden, transaccion)`; la transaccion es `None` si la devolucion
    ya estaba anotada.
    """
    recurso = evento.get("resource") or {}
    refund_id = recurso.get("id")

    existente = (
        TransaccionPagoPaypal.objects.filter(paypal_refund_id=refund_id).first()
        if refund_id
        else None
    )
    if existente:
        return orden, None

    # La base exige que un REFUND diga de que captura sale.
    captura = (
        TransaccionPagoPaypal.objects.filter(
            orden=orden, tipo=TipoTransaccionPaypal.CAPTURE
        )
        .exclude(paypal_capture_id=None)
        .order_by("-creado_en")
        .first()
    )
    if captura is None:
        raise CapturaNoAplicable(
            "Llegó una devolución de una orden sin captura registrada."
        )

    monto = recurso.get("amount") or {}
    with transaction.atomic():
        orden = OrdenPagoPaypal.objects.select_for_update().get(pk=orden.pk)
        devolucion = TransaccionPagoPaypal.objects.create(
            id=uuid.uuid4(),
            orden=orden,
            tipo=TipoTransaccionPaypal.REFUND,
            paypal_capture_id=captura.paypal_capture_id,
            paypal_refund_id=refund_id,
            monto=_a_decimal(monto.get("value")) or captura.monto,
            moneda=(monto.get("currency_code") or captura.moneda).upper(),
            estado=EstadoPagoPaypal.REFUNDED,
            respuesta_proveedor=recurso,
            procesada_en=ahora,
            creado_en=ahora,
            actualizado_en=ahora,
        )
        orden.estado = EstadoPagoPaypal.REFUNDED
        orden.actualizado_en = ahora
        orden.save(update_fields=["estado", "actualizado_en"])

        if orden.compra_id:
            CompraPaquetePsicometrico.objects.filter(
                pk=orden.compra_id
            ).exclude(estado=EstadoCompraPaquete.REEMBOLSADA).update(
                estado=EstadoCompraPaquete.REEMBOLSADA, actualizado_en=ahora
            )

    return orden, devolucion


def procesar_evento_paypal(*, evento, cabeceras):
    """Atiende un aviso de PayPal y devuelve en una palabra que se hizo.

    Sale por `FirmaWebhookInvalida` cuando el aviso no viene de PayPal, y deja
    que los fallos de red suban: son los unicos casos en que conviene que
    PayPal reintente. Todo lo demas —un evento que no atendemos, uno cuya
    orden no existe— se archiva y se acusa de recibido, porque reintentarlo
    daria siempre el mismo resultado.
    """
    if not paypal_client.verificar_firma_webhook(
        cabeceras=cabeceras, evento=evento
    ):
        raise FirmaWebhookInvalida("La firma del evento no es de PayPal.")

    fila = _reclamar_evento(evento)
    if fila is None:
        return "duplicado"

    tipo = evento.get("event_type") or ""
    if tipo not in EVENTOS_ATENDIDOS:
        _cerrar_evento(fila)
        return "ignorado"

    orden = _orden_del_evento(evento)
    if orden is None:
        _cerrar_evento(
            fila,
            procesado=False,
            mensaje_error="No se encontró la orden de este evento.",
        )
        return "sin-orden"

    estado_anterior = orden.estado
    ahora = timezone.now()

    try:
        if tipo == "CHECKOUT.ORDER.APPROVED":
            # Red de seguridad: si el navegador murio entre la aprobacion y el
            # cobro, la persona cree que pago y el dinero nunca se tomo. Aqui
            # se cobra. Si el front ya lo hizo, `capturar_pago_paypal` lo ve
            # COMPLETED y no vuelve a cobrar.
            orden, _ = capturar_pago_paypal(orden=orden)

        elif tipo in EVENTOS_DE_COBRO:
            # La verdad del cobro esta en PayPal, no en el cuerpo del aviso:
            # se relee la orden y se aplica el mismo camino que usa el front.
            resultado = paypal_client.obtener_orden(
                paypal_order_id=orden.paypal_order_id
            )
            orden, _ = aplicar_resultado_de_captura(
                orden,
                resultado,
                estado_anterior=estado_anterior,
                origen=OrigenEventoPago.WEBHOOK,
            )

        elif tipo == "PAYMENT.CAPTURE.DENIED":
            orden = _denegar_orden(orden, ahora)

        elif tipo in EVENTOS_DE_DEVOLUCION:
            orden, devolucion = _reembolsar_orden(orden, evento, ahora)
            _cerrar_evento(
                fila,
                orden=orden,
                estado_anterior=estado_anterior,
                estado_nuevo=orden.estado,
                transaccion=devolucion,
            )
            return "procesado"

    except (CapturaNoAplicable, MontoCapturadoDistinto, PaypalError) as error:
        _cerrar_evento(
            fila,
            orden=orden,
            estado_anterior=estado_anterior,
            estado_nuevo=orden.estado,
            procesado=False,
            mensaje_error=str(error),
        )
        # Un corte de red si merece reintento; lo demas ya quedo anotado y
        # volver a intentarlo daria lo mismo.
        if isinstance(error, PaypalError) and error.code == "PAYPAL_CONNECTION_ERROR":
            raise
        return "sin-efecto"

    _cerrar_evento(
        fila,
        orden=orden,
        estado_anterior=estado_anterior,
        estado_nuevo=orden.estado,
    )
    return "procesado"
