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
