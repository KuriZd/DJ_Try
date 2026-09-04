"""Comprueba la configuracion de correo sin tener que hacer una compra.

Existe porque la primera prueba real costo una tarde: el correo se marco como
`enviado` y nunca salio de la maquina, porque el backend era el de consola.
Este comando responde esa pregunta en un segundo y dice exactamente por donde
salio el mensaje y a que direccion.

    manage.py probar_correo alguien@ejemplo.com

Nunca imprime la contrasena, solo si esta puesta.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import EstadoEnvio
from core.services import correo


PRUEBA = correo.PlantillaCorreo(
    clave="prueba",
    asunto="Prueba de configuracion de correo",
    entidad="prueba",
)

PISTAS = """
Causas frecuentes:
  535      la contrasena no es la que espera el proveedor. En Gmail tiene que
           ser una de aplicacion; en Resend, la API key completa.
  550      el remitente no esta verificado, o el destinatario no es el que el
           proveedor admite en modo de pruebas. Compara caracter por caracter
           el 'To: (efectivo)' de arriba con la direccion que nombra el error:
           las mayusculas cuentan.
  timeout  el puerto esta bloqueado, o el host no es el que indica el panel
           del proveedor.
"""


class Command(BaseCommand):
    help = "Manda un correo de prueba y dice por donde salio."

    def add_arguments(self, parser):
        parser.add_argument("destinatario", help="A quien mandarlo.")
        parser.add_argument(
            "--solo-config",
            action="store_true",
            help="Ensena la configuracion sin enviar nada.",
        )

    def _mostrar_config(self):
        backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2]
        filas = [
            ("DEBUG", settings.DEBUG),
            ("EMAIL_BACKEND", backend),
            ("EMAIL_HOST", f"{settings.EMAIL_HOST}:{settings.EMAIL_PORT}"),
            ("EMAIL_HOST_USER", settings.EMAIL_HOST_USER or "(vacio)"),
            # La contrasena no se imprime nunca; solo si hay una.
            (
                "EMAIL_HOST_PASSWORD",
                "(puesta)" if settings.EMAIL_HOST_PASSWORD else "(vacia)",
            ),
            ("EMAIL_USE_TLS", settings.EMAIL_USE_TLS),
            ("EMAIL_TIMEOUT", f"{settings.EMAIL_TIMEOUT}s"),
            ("DEFAULT_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL),
            ("EMAIL_REDIRIGIR_A", settings.EMAIL_REDIRIGIR_A or "(vacio)"),
        ]
        self.stdout.write("Configuracion efectiva")
        for etiqueta, valor in filas:
            self.stdout.write(f"  {etiqueta:22} {valor}")
        return backend

    def _avisar_consola(self, backend):
        if backend != "console":
            return
        self.stdout.write(
            self.style.WARNING(
                "Aviso: el backend es la consola. El mensaje se imprimira "
                "aqui abajo y NO saldra de esta maquina.\n"
                "Para enviar de verdad, define EMAIL_BACKEND con el de smtp "
                "antes de arrancar.\n"
            )
        )

    def _contar_exito(self, registro, efectivo):
        self.stdout.write(
            self.style.SUCCESS(
                f"Aceptado por '{registro.proveedor_id}' para {efectivo}."
            )
        )
        if registro.proveedor_id == "console":
            self.stdout.write(
                "Recuerda: 'console' significa impreso aqui, no entregado."
            )
        elif settings.EMAIL_REDIRIGIR_A:
            self.stdout.write(
                f"La jaula lo desvio desde {registro.destinatario_email}."
            )
        self.stdout.write(
            "Que el SMTP lo acepte no garantiza que llegue a la bandeja: "
            "revisa el buzon, y el spam."
        )

    def handle(self, *args, **opciones):
        backend = self._mostrar_config()

        if opciones["solo_config"]:
            self.stdout.write("")
            self._avisar_consola(backend)
            return

        destinatario = opciones["destinatario"]
        if "@" not in destinatario:
            raise CommandError(f"'{destinatario}' no parece un correo.")

        # Lo que de verdad ira en la cabecera `To:`. Con la jaula activa no es
        # el argumento del comando, y esa diferencia es justo la que impide
        # diagnosticar un rechazo del proveedor mirando solo el registro: alli
        # se guarda a quien iba dirigido, no a donde se entrego.
        efectivo = settings.EMAIL_REDIRIGIR_A or destinatario
        self.stdout.write(f"  {'To: (efectivo)':22} {efectivo}")
        if efectivo != destinatario:
            self.stdout.write(
                f"  {'':22} (la jaula desvio desde {destinatario})"
            )
        self.stdout.write("")
        self._avisar_consola(backend)

        registro = correo.enviar(
            PRUEBA,
            destinatario,
            {
                "momento": timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
                "host": settings.EMAIL_HOST,
                "puerto": settings.EMAIL_PORT,
                "remitente": settings.DEFAULT_FROM_EMAIL,
            },
            entidad_id=timezone.now().isoformat(),
        )

        if registro.estado == EstadoEnvio.ENVIADO:
            self._contar_exito(registro, efectivo)
        else:
            self.stdout.write(
                self.style.ERROR(f"No salio. Error: {registro.mensaje_error}")
            )
            self.stdout.write(PISTAS)
