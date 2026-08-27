# DJ Try API

Backend REST desarrollado con Django y Django REST Framework para gestionar
usuarios, aspirantes, perfiles profesionales, postulaciones y certificados.

## Requisitos

- Python 3.12+
- PostgreSQL 15+
- Git

## Inicio rápido

### 1. Clonar el proyecto

```powershell
git clone https://github.com/KuriZd/DJ_Try.git
cd DJ_Try
```

### 2. Crear el entorno virtual

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Si utilizas `uv`:

```powershell
$env:UV_CACHE_DIR=".uv-cache"
uv venv --python 3.12 .venv
.\.venv\Scripts\Activate.ps1
```

Si PowerShell bloquea la activación:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 3. Instalar las dependencias

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Crear y configurar PostgreSQL

Crea la base de datos desde PostgreSQL:

```sql
CREATE DATABASE "DJ_try"
  WITH OWNER = CURRENT_USER
  ENCODING = 'UTF8'
  TEMPLATE = template0;
```

Configura las credenciales en la misma terminal donde ejecutarás Django:

```powershell
$env:POSTGRES_DB="DJ_try"
$env:POSTGRES_USER="postgres"
$env:POSTGRES_PASSWORD="TU_CONTRASEÑA"
$env:POSTGRES_HOST="localhost"
$env:POSTGRES_PORT="5432"
```

### 5. Aplicar las migraciones

```powershell
python manage.py migrate
python manage.py check
```

### 6. Ejecutar el servidor

```powershell
python manage.py runserver
```

La API estará disponible en `http://127.0.0.1:8000/api/`. Para comprobar la
API y la conexión con PostgreSQL visita:

```text
GET http://127.0.0.1:8000/api/health/
```

### Reportes psicométricos externos

Mientras se integra el sistema externo de evaluaciones, el reporte PDF se
archiva con `POST /api/reportes-psicometricos/`. Hay dos flujos, y el permiso
decide cuál se aplica:

| Permiso | Puede archivar | `origen` | A la venta |
|---|---|---|---|
| `reportes-psicometricos:administrar` | El expediente de cualquier aspirante | `plataforma` | Sí |
| `reportes-psicometricos:subir-propio` | Únicamente el expediente propio | `propia` | No |

El origen no lo declara el cliente: sale del permiso con el que entra. Por eso
un aspirante no puede hacer pasar su documento por uno aplicado por la
plataforma, ni ponerlo a la venta.

El expediente es **histórico**: conserva un reporte por evaluación aplicada.
Volver a subir la *misma* `referencia_evaluacion_externa` sí reemplaza al
anterior —eso es una corrección, no un documento nuevo— y deja constancia en
`historial_reportes_psicometricos`. Sin referencia externa no hay reemplazo.

Junto al archivo se guardan los metadatos con los que la interfaz arma el
archivero: `area_clave`, `aplicada_en`, `vigente_hasta`, `puntaje`, `nivel`,
`paginas` y `escalas`. Las escalas son una lista de `{"nombre", "puntaje"}` y,
como la carga viaja en `multipart/form-data`, se mandan como cadena JSON en un
solo campo.

En `GET`, el aspirante ve todo su historial —menos lo deshabilitado— y el
personal con permiso de administración ve todo, con `?aspirante=ASP-001` para
acotar. Los archivos se almacenan de forma privada y su ruta física no se
expone en la API.

Configuración disponible por variables de entorno:

```powershell
$env:PRIVATE_MEDIA_ROOT="C:\ruta\privada\reportes"
$env:REPORTE_PSICOMETRICO_PRECIO="499.00"
$env:REPORTE_PSICOMETRICO_MONEDA="MXN"
$env:REPORTE_PSICOMETRICO_MAX_MB="10"
```

La descarga se habilitará únicamente después de que PayPal confirme el pago;
esta primera etapa no publica una ruta directa al PDF.

### Crear una orden de pago PayPal

El aspirante autenticado inicia el pago de un reporte comercializable con:

```http
POST /api/pagos/paypal/ordenes/
Authorization: Bearer <token>
Idempotency-Key: <clave-opcional-de-hasta-128-caracteres>
Content-Type: application/json
```

```json
{
  "reporte_id": "UUID_DEL_REPORTE"
}
```

El monto y la moneda se leen del reporte almacenado por el backend; cualquier
precio enviado por el cliente se ignora. Si ya existe una orden vigente para
el comprador y reporte, la API la reutiliza en lugar de crear otra en PayPal.

Credenciales y URLs requeridas para Sandbox:

```powershell
$env:PAYPAL_MODE="sandbox"
$env:PAYPAL_CLIENT_ID="CLIENT_ID_SANDBOX"
$env:PAYPAL_CLIENT_SECRET="CLIENT_SECRET_SANDBOX"
$env:PAYPAL_RETURN_URL="http://localhost:5173/pagos/paypal/return"
$env:PAYPAL_CANCEL_URL="http://localhost:5173/pagos/paypal/cancel"
```

`PAYPAL_CLIENT_SECRET` es exclusivo del backend y nunca debe enviarse al
frontend ni guardarse en el repositorio.

## Documentación interactiva de la API

El proyecto utiliza `drf-yasg` para generar y consultar la documentación
OpenAPI de forma interactiva. Con el servidor en ejecución, las interfaces
están disponibles en:

- Swagger UI: `http://127.0.0.1:8000/api/docs/`
- ReDoc: `http://127.0.0.1:8000/api/redoc/`

Desde Swagger UI también es posible autorizar solicitudes protegidas con un
token JWT mediante el botón **Authorize** y el formato `Bearer <token>`.

## Pruebas

```powershell
python manage.py test
```

## Documentación

La configuración avanzada, autenticación, ejemplos de la API, endpoints,
roles, permisos y estructura de datos se encuentran en la
[guía completa del proyecto](docs/GUIA_COMPLETA.md).
