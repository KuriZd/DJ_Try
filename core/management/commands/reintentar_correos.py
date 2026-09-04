"""Reintenta los correos que quedaron en `fallido`.

Pensado para un cron cada pocos minutos.

El punto delicado esta en como se reconstruye el mensaje. El registro guarda a
quien iba y con que plantilla, pero **no guarda el cuerpo ni el contexto**, y
eso es deliberado: el cuerpo de un correo de verificacion contiene el token en
claro, y almacenarlo anularia el trabajo de guardar solo su hash en
`tokens_recuperacion`.

De ahi la consecuencia, que conviene entender antes de ampliarla: solo se
puede reintentar un correo cuyo contenido se pueda **volver a derivar de la
base**. Por eso existe el registro `RECONSTRUCTORES`, y por eso los correos con
token no estan en el —ni deben estarlo—:

- Un enlace de verificacion o de recuperacion que no llego se resuelve
  pidiendo uno nuevo desde la aplicacion, que ademas emite un token fresco.
  Reenviar el viejo alargaria la vida de un secreto que ya deberia estar
  muerto.
- Un comprobante de pago no tiene secretos y se reconstruye entero desde la
  orden, asi que si es reintentable. Se registra en la fase 3.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import EnvioCorreo, EstadoEnvio
from core.services import avisos, correo


# clave de plantilla -> funcion(registro) que devuelve
# (PlantillaCorreo, destinatario, contexto) o None si ya no se puede rehacer.
#
# Lo define `avisos`, que es donde vive cada correo concreto y por tanto quien
# sabe cuales se pueden rehacer sin resucitar un secreto.
RECONSTRUCTORES = avisos.RECONSTRUCTORES


class Command(BaseCommand):
    help = "Reintenta los correos en estado fallido que aun tengan intentos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite",
            type=int,
            default=50,
            help="Cuantos reintentar como maximo en esta pasada.",
        )
        parser.add_argument(
            "--simular",
            action="store_true",
            help="Enumera lo que haria sin enviar nada.",
        )

    def handle(self, *args, **opciones):
        fallidos_todos = EnvioCorreo.objects.filter(estado=EstadoEnvio.FALLIDO)

        # Solo se listan las plantillas que se pueden rehacer. Las demas no
        # cambian de estado al omitirse, asi que aparecerian en cada pasada
        # para siempre: en un cron cada pocos minutos eso entierra un fallo de
        # verdad bajo el mismo ruido de ayer. Se cuentan al final y ya.
        reconstruibles = list(RECONSTRUCTORES)
        pendientes = (
            fallidos_todos.filter(
                plantilla__in=reconstruibles,
                numero_intento__lt=settings.EMAIL_MAX_INTENTOS,
            )
            .order_by("creado_en")[: opciones["limite"]]
        )

        sin_reintento = fallidos_todos.exclude(
            plantilla__in=reconstruibles
        ).count()
        agotados = fallidos_todos.filter(
            plantilla__in=reconstruibles,
            numero_intento__gte=settings.EMAIL_MAX_INTENTOS,
        ).count()

        if not pendientes:
            self.stdout.write("No hay correos fallidos por reintentar.")
            self._resumen_de_lo_no_reintentable(sin_reintento, agotados)
            return

        reenviados = fallidos = omitidos = 0

        for registro in pendientes:
            reconstruir = RECONSTRUCTORES[registro.plantilla]
            piezas = reconstruir(registro)
            if piezas is None:
                omitidos += 1
                self.stdout.write(
                    f"  omitido  {registro.plantilla:22} "
                    f"{registro.destinatario_email:32} "
                    "(la entidad de origen ya no existe)"
                )
                continue

            if opciones["simular"]:
                self.stdout.write(
                    f"  simulado {registro.plantilla:22} "
                    f"{registro.destinatario_email}"
                )
                continue

            plantilla, destinatario, contexto = piezas
            resultado = correo.reintentar(
                registro, plantilla, destinatario, contexto
            )

            if resultado.estado == EstadoEnvio.ENVIADO:
                reenviados += 1
                marca = "enviado "
            else:
                fallidos += 1
                marca = "fallido "

            self.stdout.write(
                f"  {marca} {registro.plantilla:22} "
                f"{registro.destinatario_email:32} "
                f"intento {resultado.numero_intento}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{timezone.now():%Y-%m-%d %H:%M} · "
                f"{reenviados} enviados, {fallidos} fallidos, "
                f"{omitidos} omitidos."
            )
        )
        self._resumen_de_lo_no_reintentable(sin_reintento, agotados)

    def _resumen_de_lo_no_reintentable(self, sin_reintento, agotados):
        """Lo que no se toca, en una linea y sin enumerarlo.

        Que no se pueda reintentar no significa que no importe: si el numero
        crece, algo se esta rompiendo en el envio y conviene verlo. Pero
        listarlo entero en cada pasada convierte la salida del cron en ruido.
        """
        if sin_reintento:
            self.stdout.write(
                f"  {sin_reintento} fallidos no reintentables "
                "(con token o de prueba): se piden de nuevo desde la app."
            )
        if agotados:
            self.stdout.write(
                self.style.WARNING(
                    f"  {agotados} agotaron sus intentos y ya no se reintentan."
                )
            )
