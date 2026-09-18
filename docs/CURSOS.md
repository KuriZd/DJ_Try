# Cursos, lecciones, progreso y certificados

El modulo usa el JWT existente: `Authorization: Bearer <access_token>`.
Las rutas y los cuerpos de las acciones estan disponibles en `/api/docs/`.

## Preparacion

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py test tests.test_cursos tests.test_videos --noinput
```

La migracion `0025_cursos` crea cinco tablas administradas por Django:
`core_curso`, `core_leccion`, `core_inscripcion`, `core_progresoleccion`
y `core_certificadocurso`. `0026_permisos_cursos` agrega los permisos
`cursos:crear` y `cursos:administrar`, y el rol `instructor`.
No se modifican los certificados del proceso de reclutamiento.

`0029_cursos_catalogo` agrega `core_modulo` y los campos de ficha del
catalogo. Las lecciones que ya existian se agrupan en un modulo
`Contenido` por curso, conservando su orden.

## Permisos

| Cuenta | Facultades |
| --- | --- |
| Administrador / empresa | Administrar todos los cursos, asignar instructores y vincular videos confirmados. Se conserva la equivalencia de administracion que ya tiene empresa en este proyecto. |
| Instructor | Crear y administrar sus cursos; vincular sus propios videos. |
| Cualquier usuario activo autenticado | Inscribirse y acceder al contenido de sus cursos activos. Consultar su propio avance y descargar sus certificados. |
| Anonimo | Consultar el catalogo activo y la ficha de un curso, con su temario. Sin acceso a inscripciones, avances, certificados ni al archivo de video. |

El catalogo y la ficha dejaron de exigir sesion: un catalogo cerrado no
invita a registrarse, y el temario dice que hay dentro sin entregarlo. Lo
que sigue bajo llave es reproducir -- `videos/{id}/playback/` comprueba la
inscripcion antes de firmar la URL de S3 -- y el detalle de la leccion en
`lecciones/`. La ficha publica omite las lecciones desactivadas; quien
administra el curso las ve.

La autorizacion se basa en los permisos efectivos de los roles. La migracion
crea el rol instructor, pero no lo asigna a cuentas existentes. Un instructor
asignado a un curso debe ser una cuenta activa con `cursos:crear` o
`cursos:administrar`. Instructores y administradores pueden revisar el contenido
que administran sin inscribirse; para registrar progreso propio si deben inscribirse.

## Rutas

Todas las rutas llevan el prefijo `/api/` y barra final.

El curso se direcciona por `slug`, no por UUID: el enlace que se comparte es
`/api/cursos/marco-normativo-lisf/`. El slug se calcula del titulo al crear y
no cambia despues, para no romper enlaces ya publicados. Modulos, lecciones,
inscripciones y certificados siguen direccionandose por UUID.

| Metodo | Ruta | Uso |
| --- | --- | --- |
| GET / POST | `cursos/` | Catalogo publico / crear curso. La lista trae metadatos y el resumen del temario, no el temario. |
| GET | `cursos/{slug}/` | Ficha publica del curso activo, con `modulos` y sus lecciones. |
| PUT / PATCH / DELETE | `cursos/{slug}/` | Editar o eliminar; solo quien administra el curso. |
| POST | `cursos/{slug}/inscribir/` | Inscribirse; sin cuerpo. 201 al crear, 200 si ya existe. |
| GET | `cursos/{slug}/lecciones/` | Temario agrupado por modulo; exige inscripcion o permiso de gestion. |
| GET / POST | `modulos/` | Listar modulos accesibles / crear. Filtro opcional `?curso={uuid}`. |
| GET / PUT / PATCH / DELETE | `modulos/{id}/` | Consultar / editar / eliminar modulo. Borrarlo se lleva sus lecciones. |
| GET / POST | `lecciones/` | Listar lecciones accesibles / crear. Filtros opcionales `?curso={uuid}` y `?modulo={uuid}`. |
| GET / PUT / PATCH / DELETE | `lecciones/{id}/` | Consultar / editar / eliminar leccion. |
| POST | `lecciones/{id}/progreso/` | Marcar vista o pendiente: `{"visto": true}` o `{"visto": false}`. Por defecto `true`. |
| GET / POST | `inscripciones/` | Inscripciones propias / inscribirse con `{"curso": "uuid"}`. |
| GET | `inscripciones/{id}/` | Inscripcion propia con avance calculado. |
| GET | `progresos-lecciones/`, `progresos-lecciones/{id}/` | Avances propios. |
| GET | `certificados-cursos/`, `certificados-cursos/{id}/` | Certificados propios; incluye enlace privado `archivo_pdf`. |
| GET | `certificados-cursos/{id}/descargar/` | PDF con JWT; no es un enlace publico. |
| GET | `videos/{video_id}/playback/` | URL temporal de S3 tras comprobar acceso. |

## Flujo de ejemplo

1. Crear el curso como administrador o instructor:

```json
{
  "titulo": "Fundamentos del seguro en Mexico",
  "resumen": "Que es un contrato de seguro y como se estructura una poliza.",
  "descripcion": "Un recorrido por los cimientos de la actividad aseguradora.",
  "objetivos": ["Leer una poliza", "Explicar como se calcula una prima"],
  "imagen": "https://example.com/portadas/seguro.jpg",
  "categoria": "tecnico",
  "nivel": "basico",
  "duracion_estimada": 1800,
  "activo": false
}
```

`instructor` se toma de la cuenta que crea el curso si se omite; un administrador
puede enviar el UUID de otra cuenta habilitada. `imagen` es una URL opcional.
Las duraciones se expresan en segundos. Los cursos empiezan inactivos.

`categoria` acepta una de `tecnico`, `normativo`, `comercial`, `desarrollo`, y
se lee como par: `{"clave": "tecnico", "nombre": "Tecnico"}`. `nivel` acepta
`basico`, `intermedio` o `avanzado`. `objetivos` es una lista de textos.
La respuesta anade `slug` y el resumen del temario calculado de las lecciones
activas: `total_modulos`, `total_lecciones` y `duracion_total`.

2. Crear el modulo con `POST modulos/`:

```json
{
  "curso": "uuid-del-curso",
  "titulo": "El contrato y sus partes",
  "resumen": "Quien firma y bajo que condiciones.",
  "orden": 1
}
```

3. Cargar y confirmar el video usando el flujo existente:
`POST videos/upload/`, PUT directo a S3 y `POST videos/{id}/confirm/`.

4. Crear la leccion con `POST lecciones/`:

```json
{
  "modulo": "uuid-del-modulo",
  "titulo": "Primera leccion",
  "descripcion": "Presentacion",
  "video": "uuid-del-video-confirmado",
  "orden": 1,
  "duracion": 600,
  "activo": true
}
```

El orden es un entero no negativo, unico dentro de su ambito: el modulo dentro
del curso, la leccion dentro del modulo. Dos modulos del mismo curso pueden
tener cada uno su leccion numero 1. Para intercambiar ordenes, usar
temporalmente un numero libre. Ni un modulo ni una leccion pueden trasladarse
a otro curso. El archivo permanece en S3; no se guardan URLs firmadas en la BD.

5. Publicar con `PATCH cursos/{slug}/` y `{"activo": true}`.
6. El alumno se inscribe, consulta el temario y solicita la reproduccion del video.
7. Al terminar, envia `POST lecciones/{id}/progreso/` con `{"visto": true}`.
La respuesta incluye `progreso` e `inscripcion`, con el porcentaje actualizado.
8. Al completar todas las lecciones activas, aparece automaticamente un certificado
en `GET certificados-cursos/`. Descargarlo mediante fetch con Bearer y abrir el
blob PDF; un enlace abierto sin encabezado de autenticacion no funcionara.

## Reglas de progreso y almacenamiento

- Se cuenta cada leccion activa una vez. El porcentaje se trunca a dos decimales
  para no mostrar 100 % antes de completar todo. Un curso sin lecciones activas
  queda en 0 % y no genera certificado.
- Ni `usuario`, ni `completado`, ni `porcentaje_avance` se toman del cliente.
  Marcar una leccion es una declaracion del alumno: no mide el tiempo real de reproduccion.
- Repetir inscripciones o confirmaciones de progreso no duplica registros ni
  cambia la fecha de una leccion que ya estaba completada.
- Crear, activar, desactivar o eliminar lecciones por API recalcula todas las
  inscripciones del curso; eliminar un modulo hace lo mismo, porque se lleva sus
  lecciones por cascada. Sustituir el video de una leccion reinicia su progreso.
  Las escrituras al temario y al progreso bloquean la fila del curso en transaccion
  para coordinar solicitudes simultaneas. Cambios directos por SQL/ORM deben
  respetar ese bloqueo y llamar a `recalcular_curso`.
- El certificado acredita la finalizacion en su fecha. Se conserva, con el mismo
  codigo y PDF, si cambia el temario o el alumno desmarca una leccion despues.
- El PDF se genera con ReportLab y se guarda como binario privado en PostgreSQL,
  dentro de la misma transaccion que el progreso. No requiere almacenamiento S3
  adicional, rutas publicas ni archivos temporales. Un fallo al generarlo revierte
  la confirmacion final y permite reintentar.
- DELETE de un curso elimina sus modulos, lecciones, inscripciones, avances y
  certificados.
  Para retirar un curso conservando el historial, usar `activo: false`.
  Eliminar una leccion o un curso no elimina sus archivos de video en S3.
- Un video vinculado a cualquier curso deja de ser accesible anonimamente,
  incluso si tiene visibilidad `unlisted`. Su propietario mantiene acceso a su
  archivo; alumnos necesitan una leccion activa en un curso activo inscrito.
