"""
Movimientos del proceso de selección y su historial.

Todo cambio de estado, etapa o progreso de una postulación pasa por aquí para
que la línea de tiempo (EventoPostulacion) no se desfase de la tabla: si el
evento se escribiera aparte, bastaría un camino nuevo que lo olvide para que
el aspirante viera un historial incompleto.
"""

from django.db import transaction
from django.utils import timezone

from core.models import EventoPostulacion, Postulacion

CAMPOS_DEL_PROCESO = ("estado", "etapa", "progreso")


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
