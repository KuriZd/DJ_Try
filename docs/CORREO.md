# Correo transaccional — comandos

Referencia rápida de la capa de correo: cómo configurarla, cómo probarla y
cómo averiguar qué pasó cuando un mensaje no llega.

Los bloques son de PowerShell. Las variables viven solo en la ventana donde se
definen: si abres una terminal nueva, hay que volver a ponerlas.

---

## 1. Variables de entorno

### Desarrollo sin enviar nada (por defecto)

Con `DEBUG=True` y sin configurar nada, el backend es el de **consola**: el
mensaje se imprime en la terminal donde corre `runserver` y **no sale de la
máquina**. Sirve para ver el contenido, no para probar la entrega.

### Enviar de verdad con Resend

```powershell
$env:POSTGRES_PASSWORD = "..."                                   # tu contraseña local de Postgres
$env:EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
$env:EMAIL_HOST = "smtp.resend.com"
$env:EMAIL_PORT = "587"
$env:EMAIL_HOST_USER = "resend"
$env:EMAIL_HOST_PASSWORD = "re_..."                              # la API key
$env:DEFAULT_FROM_EMAIL = "onboarding@resend.dev"
$env:EMAIL_REDIRIGIR_A = "kurizd@protonmail.com"                 # todo en minúsculas
```

Sin dominio verificado, Resend **solo acepta como destinatario la dirección con
la que te registraste**, y compara distinguiendo mayúsculas.

### Todas las variables disponibles

| Variable | Por defecto | Para qué |
|---|---|---|
| `EMAIL_BACKEND` | consola si `DEBUG`, si no smtp | Por dónde sale |
| `EMAIL_HOST` / `EMAIL_PORT` | `localhost` / `587` | Servidor SMTP |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | vacías | Credenciales |
| `EMAIL_USE_TLS` | `True` | Cifrado |
| `EMAIL_TIMEOUT` | `5` | Segundos. Corto a propósito: el envío ocurre dentro de la petición |
| `DEFAULT_FROM_EMAIL` | `AMIS <no-reply@amis.org>` | Remitente |
| `EMAIL_REDIRIGIR_A` | vacía | **Jaula de pruebas**: desvía todo a esa dirección |
| `EMAIL_MAX_INTENTOS` | `3` | Cuántas veces reintenta el comando |
| `FRONTEND_BASE_URL` | `http://localhost:5173` | Base de los enlaces del correo |
| `ZONA_HORARIA_VISUAL` | `America/Mexico_City` | Zona con la que se escriben las fechas |
| `TOKEN_VERIFICACION_HORAS` | `48` | Vigencia del enlace de verificación |
| `TOKEN_RECUPERACION_HORAS` | `1` | Vigencia del enlace de recuperación |

---

## 2. Comandos

### Probar la configuración

```powershell
.\.venv\Scripts\python.exe manage.py probar_correo kurizd@protonmail.com
```

Enseña la configuración efectiva, **a qué dirección va de verdad**, manda un
correo de prueba y dice por dónde salió. Si algo falla, imprime el error con
las causas frecuentes.

Solo mirar la configuración, sin enviar:

```powershell
.\.venv\Scripts\python.exe manage.py probar_correo x@y.com --solo-config
```

### Reintentar los correos fallidos

```powershell
.\.venv\Scripts\python.exe manage.py reintentar_correos
.\.venv\Scripts\python.exe manage.py reintentar_correos --simular
.\.venv\Scripts\python.exe manage.py reintentar_correos --limite 10
```

Solo reintenta lo que se puede **reconstruir desde la base**. Hoy eso es el
comprobante de pago. Los correos con token (verificación, recuperación) se
omiten a propósito: su cuerpo no se guarda —contiene el token en claro— y
reenviar uno viejo alargaría la vida de un secreto que debería estar muerto.
Si uno de esos no llega, se pide otro desde la aplicación.

---

## 3. Consultar el registro

Cada intento deja una fila en `envios_correo`. Es la fuente de verdad sobre si
un correo salió; la impresión de que funcionó no lo es.

### Últimos envíos

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from core.models import EnvioCorreo; [print(f'{str(e.creado_en)[:19]} | {e.plantilla:18} | {e.estado:8} | canal={e.proveedor_id} | {e.destinatario_email}') for e in EnvioCorreo.objects.order_by('-creado_en')[:10]]"
```

### Los que fallaron, con su error

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from core.models import EnvioCorreo; [print(f'{str(e.creado_en)[:19]} | {e.plantilla} | {e.mensaje_error}') for e in EnvioCorreo.objects.filter(estado='fallido').order_by('-creado_en')[:5]]"
```

### Forzar el reenvío de un comprobante ya entregado

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from core.models import EnvioCorreo; EnvioCorreo.objects.filter(entidad='orden_pago').update(estado='fallido')"
.\.venv\Scripts\python.exe manage.py reintentar_correos
```

### Qué significa cada columna

| Columna | Qué dice |
|---|---|
| `estado` | `pendiente`, `enviado`, `fallido` |
| `proveedor_id` | **El canal**: `smtp`, `console`, `locmem` |
| `destinatario_email` | A quién iba dirigido — **no** dónde se entregó si la jaula estaba activa |
| `entidad` / `entidad_id` | Contra qué se envió, p. ej. `orden_pago` + folio |
| `numero_intento` | Cuántas veces se ha probado |
| `mensaje_error` | El error con su tipo, cuando falló |

`estado=enviado` con `canal=console` significa **impreso en una terminal**, no
entregado. Mira siempre las dos columnas juntas.

---

## 4. Base de datos y pruebas

```powershell
$env:POSTGRES_PASSWORD = "..."

.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py test core.test_correo_capa
.\.venv\Scripts\python.exe manage.py test core.test_correo_comprobante
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py check --deploy
```

Las pruebas usan el backend `locmem` y no tocan ningún SMTP.

---

## 5. Lo que muerde

Cinco cosas que ya costaron tiempo, en el orden en que suelen aparecer.

**El backend de consola miente.** Con `DEBUG=True` el correo se imprime y se
marca `enviado` sin salir de la máquina. Revisa `canal` en el registro, o
corre `probar_correo`, que lo avisa en amarillo antes de intentar nada.

**Las mayúsculas del destinatario cuentan.** Resend rechaza
`KuriZd@protonmail.com` si registraste `kurizd@protonmail.com`. El comando
imprime `To: (efectivo)` justo para poder compararlo carácter por carácter con
lo que diga el error.

**El remitente tiene que estar autorizado por el proveedor.** Autenticar con
Gmail o Resend y decir que escribes desde `no-reply@amis.org` da un `550` o un
`553`. Mientras no haya dominio verificado, `DEFAULT_FROM_EMAIL` debe ser una
dirección del proveedor.

**Gmail necesita contraseña de aplicación.** La contraseña normal de la cuenta
da `535` desde que Google retiró el acceso de apps menos seguras. Requiere
verificación en dos pasos activa.

**Las variables no sobreviven a la terminal.** Si abres una ventana nueva,
`probar_correo` usará los valores por defecto. Si te aparece una configuración
que no esperabas, casi siempre es eso —o variables viejas que siguen vivas de
una prueba anterior.

---

## 6. Lo que falta

El correo sale hoy desde el dominio de pruebas del proveedor. Para enviarlo
desde `amis.org` hacen falta tres registros DNS —SPF, DKIM y DMARC— que no
existen: el dominio no tiene ninguna autenticación de correo. Sin eso, los
mensajes desde `@amis.org` caerán en spam.

El DNS está alojado en Wix (`ns14.wixdns.net`), no en GoDaddy, aunque el
correo actual de la asociación salga por ahí.
