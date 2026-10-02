"""Moderacion del muro de Actualiza: rastro de lo que se retira y reportes.

Dos reglas:

- **Se audita lo que alguien quita de otro**, nunca lo que cada quien borra de
  si mismo. Guardar una copia de lo que la persona decidio retirar seria
  justo lo contrario de retirarlo. Lo que borra un moderador, o el autor de
  una publicacion sobre un comentario ajeno, si queda: es una decision sobre
  la palabra de otro y tiene que poder revisarse.
- **Ningun reporte queda colgando.** Si lo reportado desaparece por cualquier
  camino, sus reportes pendientes se cierran como "eliminado". Sin esto, la
  cola de moderacion se llenaria de casos sobre contenido que ya no existe.
"""

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Auditoria, Comentario, EstadoReporte, Publicacion, Reporte


def _copia(contenido):
    datos = {
        'autor_id': str(contenido.autor_id),
        'autor_nombre': contenido.autor.nombre_completo,
        'cuerpo': contenido.cuerpo,
        'fecha_publicacion': contenido.fecha_publicacion.isoformat(),
    }
    if isinstance(contenido, Comentario):
        datos['publicacion_id'] = str(contenido.publicacion_id)
    return datos


def _entidad(contenido):
    return 'publicacion' if isinstance(contenido, Publicacion) else 'comentario'


def auditar(request, accion, entidad, entidad_id, anteriores=None, nuevos=None):
    Auditoria.objects.create(
        usuario=request.user,
        accion=accion,
        entidad=entidad,
        entidad_id=str(entidad_id),
        datos_anteriores=anteriores,
        datos_nuevos=nuevos,
        ip=request.META.get('REMOTE_ADDR'),
        creado_en=timezone.now(),
    )


def _reportes_pendientes_de(contenido):
    if isinstance(contenido, Publicacion):
        # Borrar una publicacion se lleva sus comentarios: sus reportes tambien
        # se quedarian sin objeto.
        filtro = Q(publicacion=contenido) | Q(comentario__publicacion=contenido)
    else:
        filtro = Q(comentario=contenido)
    return Reporte.objects.filter(filtro, estado=EstadoReporte.PENDIENTE)


def cerrar_reportes(contenido, estado, quien):
    return _reportes_pendientes_de(contenido).update(
        estado=estado, resuelto_por=quien, resuelto_en=timezone.now(),
    )


def eliminar(request, contenido, motivo):
    """Borra una publicacion o un comentario dejando el rastro que toque.

    `motivo` explica en la auditoria por que pudo hacerlo quien lo hizo:
    'moderacion', 'autor_publicacion' o 'reporte'. Si es lo propio no se
    audita, pero sus reportes pendientes igual se cierran.
    """
    cerrar_reportes(contenido, EstadoReporte.ELIMINADO, request.user)
    if contenido.autor_id != request.user.pk:
        auditar(
            request, 'eliminar', _entidad(contenido), contenido.pk,
            anteriores={**_copia(contenido), 'motivo': motivo},
        )
    contenido.delete()


def reportar(usuario, contenido, motivo, detalle):
    """Crea el reporte, o devuelve el que la cuenta ya tenia pendiente.

    Dos clics a la vez pasarian los dos la comprobacion; el indice unico
    parcial frena al segundo, que entonces devuelve el del primero.
    """
    campo = _entidad(contenido)
    pendiente = Reporte.objects.filter(
        reportado_por=usuario, estado=EstadoReporte.PENDIENTE, **{campo: contenido},
    )
    if existente := pendiente.first():
        return existente, False
    try:
        with transaction.atomic():
            return Reporte.objects.create(
                reportado_por=usuario,
                motivo=motivo,
                detalle=detalle,
                cuerpo_reportado=contenido.cuerpo,
                autor_reportado_id=contenido.autor_id,
                **{campo: contenido},
            ), True
    except IntegrityError:
        return pendiente.get(), False


def contenido_de(reporte):
    return reporte.publicacion or reporte.comentario


def descartar(request, reporte):
    """Cierra como descartados todos los reportes pendientes del mismo
    contenido: la decision es sobre el contenido, no sobre cada aviso."""
    contenido = contenido_de(reporte)
    if contenido is None:
        cerrados = 0
    else:
        cerrados = cerrar_reportes(contenido, EstadoReporte.DESCARTADO, request.user)
    auditar(
        request, 'descartar_reporte', 'reporte', reporte.pk,
        nuevos={'reportes_cerrados': cerrados, 'motivo': reporte.motivo},
    )
