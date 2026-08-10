# Guía completa de DJ Try API

Esta guía complementa el README principal con la configuración opcional, el
uso de la API y la estructura funcional del proyecto.

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
│   ├── migrations/         # Migraciones del esquema y datos iniciales
│   └── models.py           # Representación de las tablas PostgreSQL
├── database/
│   ├── 00_create_database.sql
│   ├── schema.sql
│   └── seed.sql
├── docs/                   # Documentación ampliada
├── manage.py
├── README.md
├── TODO.md
└── requirements.txt
```

## Configuración opcional

La duración de los tokens y los orígenes permitidos para el frontend se
configuran mediante variables de entorno:

```powershell
$env:JWT_ACCESS_MINUTES="15"
$env:JWT_REFRESH_DAYS="7"
$env:CORS_ALLOWED_ORIGINS="http://localhost:5173,http://localhost:3000"
```

Las variables definidas con `$env:` solamente permanecen en la sesión actual
de PowerShell.

## Base de datos y migraciones

La migración inicial ejecuta dentro de una transacción:

1. `database/schema.sql`
2. `database/seed.sql`

Los modelos utilizan `managed = False` porque las tablas son administradas por
el esquema SQL. Cada vez que se descarguen migraciones nuevas debe ejecutarse:

```powershell
python manage.py migrate
```

La migración `0007_fecha_nacimiento_aspirante` agrega el campo opcional
`fecha_nacimiento` a instalaciones existentes.

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

La respuesta contiene `access`, `refresh` y los datos del usuario.

### Crear una cuenta

```http
POST /api/auth/registro/
Content-Type: application/json
```

```json
{
  "nombre_completo": "Nombre Apellido",
  "fecha_nacimiento": "1998-05-21",
  "email": "nuevo@djtry.local",
  "password": "ContraseñaSegura"
}
```

`fecha_nacimiento` es opcional, utiliza el formato `AAAA-MM-DD` y no puede ser
una fecha futura. La cuenta se crea activa, recibe el rol `aspirante` y queda
autenticada. También se genera su expediente con identificador y matrícula
consecutivos.

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

### Consultar y editar el perfil

```http
GET /api/auth/me/
Authorization: Bearer ACCESS_TOKEN
```

```http
PATCH /api/auth/me/
Authorization: Bearer ACCESS_TOKEN
Content-Type: application/json
```

```json
{
  "nombre_completo": "Nombre Apellido",
  "email": "nuevo@djtry.local",
  "fecha_nacimiento": "1998-05-21",
  "telefono": "+52 55 1234 5678",
  "cedula_profesional": "12345678"
}
```

Todos los campos son opcionales. Cambiar el correo limpia
`email_verificado_en` y obliga a utilizar el correo nuevo en el siguiente
inicio de sesión.

La cédula profesional puede registrarse una sola vez. Una corrección posterior
requiere revisión administrativa. Los campos propios del expediente responden
`400` cuando el usuario no tiene un aspirante relacionado.

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

Responde `204 No Content`. `refresh` es opcional: si se incluye, se conserva
la sesión actual; si se omite, se revocan todas las sesiones.

## Endpoints

| Método | Ruta | Descripción | Autenticación |
|---|---|---|---|
| `GET` | `/api/` | Raíz de la API | No |
| `GET` | `/api/health/` | Estado de API y PostgreSQL | No |
| `POST` | `/api/auth/login/` | Iniciar sesión | No |
| `POST` | `/api/auth/registro/` | Crear cuenta y expediente | No |
| `POST` | `/api/auth/refresh/` | Renovar access token | No |
| `POST` | `/api/auth/logout/` | Cerrar sesión | Sí |
| `GET` | `/api/auth/me/` | Consultar usuario autenticado | Sí |
| `PATCH` | `/api/auth/me/` | Editar el perfil propio | Sí |
| `POST` | `/api/auth/password/` | Cambiar la contraseña | Sí |
| `GET` | `/api/aspirantes/` | Listar aspirantes permitidos | Sí |
| `GET` | `/api/aspirantes/{id}/` | Consultar un aspirante permitido | Sí |
| `GET` | `/api/postulaciones/` | Listar postulaciones permitidas | Sí |
| `GET` | `/api/postulaciones/{id}/` | Consultar una postulación permitida | Sí |
| `GET` | `/api/vacantes/` | Listar vacantes publicadas y vigentes | No |
| `GET` | `/api/vacantes/{id}/` | Consultar una vacante publicada y vigente | No |

Los endpoints de aspirantes y postulaciones son de solo lectura. Solicitar un
registro fuera del alcance del usuario responde `404` para no revelar su
existencia.

## Roles y permisos

`GET /api/auth/me/`, el login y el registro devuelven los arreglos `roles` y
`permisos` del usuario.

| Rol | Alcance principal |
|---|---|
| `administrador` | Todos los permisos |
| `reclutador` | Aspirantes, vacantes y postulaciones completas |
| `certificador` | Aspirantes y certificados autorizados |
| `consulta` | Lectura general sin administración |
| `aspirante` | Su expediente, sus postulaciones y sus certificados |

`aspirantes:consultar` permite ver cualquier expediente. Sin este permiso, el
usuario solamente obtiene el expediente relacionado con su cuenta.

`postulaciones:consultar-todas` permite consultar postulaciones de cualquier
aspirante. Quien sólo tiene `postulaciones:consultar` ve las postulaciones de
su propio expediente.

## Datos del expediente profesional

| Grupo | Tabla principal | Datos |
|---|---|---|
| Cuenta | `usuarios` | Nombre, correo, estado y acceso |
| Expediente | `aspirantes` | Fecha de nacimiento, teléfono, matrícula, cédula y puesto aspirado |
| Perfil | `perfiles_profesionales` | Estudios, experiencia, habilidades y evaluaciones |
| Aplicación | `postulaciones` | Vacante, etapa, progreso y compatibilidad |
| Certificado | `certificados` | Folio, estado y snapshot inmutable del aspirante |

Los datos académicos incluyen nivel educativo, institución, carrera,
certificaciones y cursos. Los datos laborales incluyen empresas, puestos,
experiencia, área profesional y habilidades declaradas. Las competencias
incluyen habilidades técnicas, habilidades blandas, evaluaciones, puntaje y
compatibilidad con el perfil.

Los campos pueden permanecer vacíos hasta que el aspirante complete el
expediente o sean validados por personal autorizado.

## Usuario de desarrollo

```text
Email:      kurizd@djtry.local
Contraseña: 0330
Rol:        administrador
```

Estas credenciales son exclusivamente para desarrollo y deben eliminarse o
reemplazarse antes de desplegar el proyecto.

## Conexión desde React

```env
VITE_API_URL=http://127.0.0.1:8000/api
```

```javascript
const response = await fetch(`${import.meta.env.VITE_API_URL}/auth/login/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});

const data = await response.json();
```

## Trabajo pendiente

El folio por postulación, la estructura obligatoria del snapshot y las pruebas
del contenido de certificados están registrados en [`../TODO.md`](../TODO.md).
