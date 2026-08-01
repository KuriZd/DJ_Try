# DJ Try API

Backend REST desarrollado con Django y Django REST Framework, enfocado en la
autenticación, los datos del usuario y la consulta de aspirantes.

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
usuario. El objeto `usuario` tiene la misma forma que `GET /api/auth/me/`, es
decir incluye fechas, `roles` y `aspirante`.

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

### Editar el perfil propio

```http
PATCH /api/auth/me/
Authorization: Bearer ACCESS_TOKEN
Content-Type: application/json
```

```json
{
  "nombre_completo": "Nombre Apellido",
  "telefono": "+52 55 1234 5678"
}
```

Ambos campos son opcionales. La respuesta es el usuario actualizado. El correo
y la cédula profesional son de solo lectura; cambiarlos requiere un flujo de
verificación o revisión administrativa.

### Cambiar la contraseña

```http
POST /api/auth/password/
Authorization: Bearer ACCESS_TOKEN
Content-Type: application/json
```

```json
{
  "password_actual": "ContraseñaActual",
  "password_nueva": "ContraseñaNueva",
  "refresh": "REFRESH_TOKEN"
}
```

Responde `204 No Content`. La contraseña nueva pasa por los validadores de
`AUTH_PASSWORD_VALIDATORS` y se guarda con el hasher por defecto de Django.

Al cambiarla se revocan las sesiones abiertas del usuario. El campo `refresh`
es opcional: si se envía, esa sesión se conserva para no cerrar la del
navegador actual; si se omite, se revocan todas.

## Endpoints

| Método | Ruta | Descripción | Autenticación |
|---|---|---|---|
| `GET` | `/api/` | Raíz de la API | No |
| `GET` | `/api/health/` | Estado de API y PostgreSQL | No |
| `POST` | `/api/auth/login/` | Iniciar sesión | No |
| `POST` | `/api/auth/refresh/` | Renovar access token | No |
| `POST` | `/api/auth/logout/` | Cerrar sesión | Sí |
| `GET` | `/api/auth/me/` | Consultar usuario autenticado | Sí |
| `PATCH` | `/api/auth/me/` | Editar el perfil propio | Sí |
| `POST` | `/api/auth/password/` | Cambiar la contraseña propia | Sí |
| `GET` | `/api/aspirantes/` | Listar aspirantes | Sí |
| `GET` | `/api/aspirantes/{id}/` | Consultar aspirante | Sí |

La respuesta de `GET /api/auth/me/` incluye el objeto `aspirante` cuando la
cuenta está relacionada mediante `aspirantes.usuario_id`. Este objeto contiene
la matrícula, el teléfono, la cédula profesional y el estado del expediente.
Si la cuenta no tiene un aspirante relacionado, su valor es `null`.

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
