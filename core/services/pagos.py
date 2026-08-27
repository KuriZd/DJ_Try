import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import (
    EstadoPagoPaypal,
    EstadoReportePsicometrico,
    EventoPagoPaypal,
    OrdenPagoPaypal,
    OrigenEventoPago,
    OrigenReportePsicometrico,
    ReportePsicometrico,
)

from .paypal import PaypalError, paypal_client


ESTADOS_ORDEN_ACTIVA = (
    EstadoPagoPaypal.PENDING,
    EstadoPagoPaypal.CREATED,
    EstadoPagoPaypal.APPROVED,
    EstadoPagoPaypal.COMPLETED,
)


class PagoNoDisponible(Exception):
    pass


class IdempotenciaEnConflicto(Exception):
    pass


def _referencia_interna():
    fecha = timezone.now().strftime("%Y%m%d")
    return f"PAY-{fecha}-{uuid.uuid4().hex[:12].upper()}"


def _registrar_evento(
    *, orden, tipo_evento, estado_anterior, estado_nuevo, payload, procesado=True,
    mensaje_error=None,
):
    ahora = timezone.now()
    EventoPagoPaypal.objects.create(
        id=uuid.uuid4(),
        orden=orden,
        tipo_evento=tipo_evento,
        origen=OrigenEventoPago.API,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        payload=payload,
        procesado=procesado,
        mensaje_error=mensaje_error,
        recibido_en=ahora,
        procesado_en=ahora if procesado else None,
        creado_en=ahora,
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

        misma_clave = None
        if clave_idempotencia:
            misma_clave = OrdenPagoPaypal.objects.select_for_update().filter(
                comprador=comprador,
                clave_idempotencia=clave_idempotencia,
            ).first()
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
            if activa.estado == EstadoPagoPaypal.COMPLETED:
                return activa, False, False
            if activa.estado == EstadoPagoPaypal.PENDING:
                if activa.actualizado_en >= limite_pending:
                    return activa, False, False
                # Recuperar una llamada interrumpida con el MISMO request id.
                # PayPal devolverá la orden original en vez de duplicarla.
                activa.actualizado_en = ahora
                activa.mensaje_error = None
                activa.save(update_fields=["actualizado_en", "mensaje_error"])
                return activa, True, False
            elif activa.expira_en is None or activa.expira_en > ahora:
                return activa, False, False
            else:
                if misma_clave:
                    raise IdempotenciaEnConflicto(
                        "La orden expiró; usa una nueva clave de idempotencia."
                    )
                activa.estado = EstadoPagoPaypal.CANCELLED
            activa.actualizado_en = ahora
            activa.mensaje_error = "Orden local expirada antes de completarse."
            activa.save(
                update_fields=["estado", "actualizado_en", "mensaje_error"]
            )

        request_id = str(uuid.uuid4())
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
            paypal_request_id=request_id,
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

    try:
        resultado = paypal_client.crear_orden(
            referencia=orden.referencia_interna,
            request_id=orden.paypal_request_id,
            monto=orden.monto,
            moneda=orden.moneda,
            descripcion=f"Reporte psicométrico {orden.referencia_evaluacion_externa or orden.reporte_id}",
        )
    except PaypalError as error:
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
    return orden, nueva_local
