"""
Movimientos del proceso de selección y su historial.

Todo cambio de estado, etapa o progreso de una postulación pasa por aquí para
que la línea de tiempo (EventoPostulacion) no se desfase de la tabla: si el
evento se escribiera aparte, bastaría un camino nuevo que lo olvide para que
el aspirante viera un historial incompleto.
"""

from django.db import transaction
from django.utils import timezone

from core.models import (
    ESTADOS_RETIRABLES,
    EstadoPostulacion,
    EventoPostulacion,
    Postulacion,
)

CAMPOS_DEL_PROCESO = ("estado", "etapa", "progreso")


class PostulacionRetirada(Exception):
    """El aspirante ya la retiró: el proceso no se mueve más."""


def registrar_evento(postulacion, usuario=None, ocurrido_en=None):
    """Foto del estado vigente de la postulación como un paso del historial."""
    return EventoPostulacion.objects.create(
        postulacion=postulacion,
        estado=postulacion.estado,
        etapa=postulacion.etapa,
        progreso=postulacion.progreso,
        registrado_por=usuario,
        ocurrido_en=ocurrido_en or timezone.now(),
    )


def avanzar_postulacion(postulacion, cambios, usuario):
    """
    Aplica `cambios` (subconjunto de estado/etapa/progreso) y deja el evento.

    Si nada cambia de verdad no se escribe nada: un PATCH repetido no debe
    llenar el historial de pasos idénticos ni mover `ultima_actividad_en`.

    La fila se bloquea mientras dura la escritura para que dos reclutadores
    moviendo la misma postulación no dejen eventos con fotos cruzadas.
    """
    with transaction.atomic():
        vigente = Postulacion.objects.select_for_update().get(pk=postulacion.pk)

        # Retirarse es decisión del aspirante: el reclutador no la deshace
        # moviéndola de nuevo a un estado del proceso.
        if vigente.estado == EstadoPostulacion.RETIRADA:
            raise PostulacionRetirada(
                "El aspirante retiró esta postulación; ya no se puede mover."
            )

        reales = {
            campo: valor
            for campo, valor in cambios.items()
            if campo in CAMPOS_DEL_PROCESO and getattr(vigente, campo) != valor
        }
        if not reales:
            return vigente

        ahora = timezone.now()
        for campo, valor in reales.items():
            setattr(vigente, campo, valor)
        vigente.ultima_actividad_en = ahora
        vigente.save(update_fields=[*reales, "ultima_actividad_en"])

        registrar_evento(vigente, usuario=usuario, ocurrido_en=ahora)

    return vigente


ETAPA_RETIRADA = "Postulación retirada por el aspirante"


class PostulacionNoRetirable(Exception):
    """La postulación ya llegó a un final y no hay nada que retirar."""


def retirar_postulacion(postulacion, usuario):
    """
    El aspirante cancela su postulación: pasa a `retirada` y queda en el
    historial. No se borra —el reclutador necesita ver que se retiró, y el
    historial es justo lo que el aspirante consulta—, y el progreso se
    conserva tal cual: dice hasta dónde llegó el proceso.

    Se comprueba el estado dentro del bloqueo para que un reclutador que la
    rechaza al mismo tiempo no termine con una rechazada "retirada".
    """
    with transaction.atomic():
        vigente = Postulacion.objects.select_for_update().get(pk=postulacion.pk)

        if vigente.estado not in ESTADOS_RETIRABLES:
            raise PostulacionNoRetirable(
                "Esta postulación ya no se puede retirar: el proceso terminó."
            )

        return avanzar_postulacion(
            vigente,
            {"estado": EstadoPostulacion.RETIRADA, "etapa": ETAPA_RETIRADA},
            usuario,
        )
