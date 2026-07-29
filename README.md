# DJ Try API

Backend REST desarrollado con Django y Django REST Framework. Utiliza
PostgreSQL para almacenar convocatorias, vacantes, aspirantes, postulaciones,
certificados y usuarios.

## Tecnologías

- Python 3.12+
- Django 6
- Django REST Framework
- PostgreSQL 15+
- JWT con Simple JWT
- Psycopg 3

## Estructura

```text
DJ_Try/
├── config/                 # Configuración general de Django
├── core/
│   ├── api/                # Views, serializers, rutas y JWT
│   ├── migrations/         # Carga del schema y datos iniciales
│   └── models.py           # Representación de las tablas PostgreSQL
├── database/
│   ├── 00_create_database.sql
│   ├── schema.sql
│   └── seed.sql
├── manage.py
└── requirements.txt
```

## Instalación

### 1. Clonar el repositorio

```powershell
git clone https://github.com/KuriZd/DJ_Try.git
cd DJ_Try
```

### 2. Crear y activar el entorno virtual

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Si PowerShell bloquea la activación:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 3. Instalar dependencias

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Base de datos

### 1. Crear la base PostgreSQL

Conéctate a la base administrativa `postgres` y ejecuta:

```sql
CREATE DATABASE "DJ_try"
  WITH
  OWNER = CURRENT_USER
  ENCODING = 'UTF8'
  TEMPLATE = template0;
```

También puedes ejecutar `database/00_create_database.sql`.

### 2. Configurar variables de entorno

En PowerShell:

```powershell
$env:POSTGRES_DB="DJ_try"
$env:POSTGRES_USER="postgres"
$env:POSTGRES_PASSWORD="TU_CONTRASEÑA"
$env:POSTGRES_HOST="localhost"
$env:POSTGRES_PORT="5432"
```

Opcionalmente, puedes configurar la duración de los tokens y los orígenes del
frontend:

```powershell
$env:JWT_ACCESS_MINUTES="15"
$env:JWT_REFRESH_DAYS="7"
$env:CORS_ALLOWED_ORIGINS="http://localhost:5173,http://localhost:3000"
```

### 3. Ejecutar migraciones

```powershell
python manage.py migrate
```

La primera migración ejecuta, dentro de una transacción:

1. `database/schema.sql`
2. `database/seed.sql`

Los modelos de `core/models.py` utilizan `managed = False`, porque las tablas
son administradas por el esquema SQL.

## Ejecutar el servidor

```powershell
python manage.py runserver
```

La API estará disponible en:

```text
http://127.0.0.1:8000/api/
```

Comprobación de servicio y PostgreSQL:

```text
GET http://127.0.0.1:8000/api/health/
```

## Autenticación

### Iniciar sesión

```http
POST /api/auth/login/
Content-Type: application/json
```

```json
{
  "email": "kurizd@djtry.local",
  "password": "0330"
}
```

La respuesta contiene los tokens `access` y `refresh`, además de los datos del
usuario.

### Usar el access token

```http
Authorization: Bearer ACCESS_TOKEN
```

### Renovar el token

```http
POST /api/auth/refresh/
Content-Type: application/json
```

```json
{
  "refresh": "REFRESH_TOKEN"
}
```

### Cerrar sesión

```http
POST /api/auth/logout/
Authorization: Bearer ACCESS_TOKEN
Content-Type: application/json
```

```json
{
  "refresh": "REFRESH_TOKEN"
}
```

## Endpoints

| Método | Ruta | Descripción | Autenticación |
|---|---|---|---|
| `GET` | `/api/` | Raíz de la API | No |
| `GET` | `/api/health/` | Estado de API y PostgreSQL | No |
| `POST` | `/api/auth/login/` | Iniciar sesión | No |
| `POST` | `/api/auth/refresh/` | Renovar access token | No |
| `POST` | `/api/auth/logout/` | Cerrar sesión | Sí |
| `GET` | `/api/aspirantes/` | Listar aspirantes | Sí |
| `GET` | `/api/aspirantes/{id}/` | Consultar aspirante | Sí |
| `GET` | `/api/convocatorias/` | Listar convocatorias | Sí |
| `GET` | `/api/convocatorias/{id}/` | Consultar convocatoria | Sí |
| `GET` | `/api/vacantes/` | Listar vacantes | Sí |
| `GET` | `/api/vacantes/{id}/` | Consultar vacante | Sí |
| `GET` | `/api/certificados/` | Listar certificados | Sí |
| `GET` | `/api/certificados/{id}/` | Consultar certificado | Sí |

## Usuario de prueba

```text
Email:      kurizd@djtry.local
Contraseña: 0330
Rol:        administrador
```

Este usuario y su contraseña son exclusivamente para desarrollo. Deben
eliminarse o reemplazarse antes de desplegar el proyecto en producción.

## Conexión desde React

URL base para desarrollo:

```env
VITE_API_URL=http://127.0.0.1:8000/api
```

Ejemplo:

```javascript
const response = await fetch(`${import.meta.env.VITE_API_URL}/auth/login/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});

const data = await response.json();
```

## Validación

Para comprobar la configuración de Django:

```powershell
python manage.py check
```
