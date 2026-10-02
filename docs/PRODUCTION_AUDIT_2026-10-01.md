# Auditoría general para producción — 1 de octubre de 2026

Seguimiento actualizado: [auditoría del 2 de octubre](PRODUCTION_AUDIT_2026-10-02.md).
Incluye PROD06–PROD08 y la verificación de cache compartida y dependencias.

**Dictamen: todavía no liberar.** Los cinco hallazgos funcionales PROD01–PROD05
están corregidos en los cambios locales. Quedan un perfil de despliegue
verificable y el cierre de avisos de
dependencias. Las cinco reproducciones fallidas de la tabla siguiente
corresponden a la auditoría inicial, antes de estas correcciones.

## Seguimiento: correcciones críticas de pagos

Se relee la orden con `select_for_update` al aplicar la respuesta de PayPal.
Una captura COMPLETED tardía conserva REFUNDED y no vuelve a entregar la
compra. Una respuesta PENDING solo modifica órdenes PENDING, CREATED o
APPROVED; conserva el pago y la transacción si ya están completados.
Los errores de captura y las denegaciones también conservan las órdenes
COMPLETED y REFUNDED. El evento de captura registra el estado anterior leído
bajo bloqueo, en lugar de uno anterior a la llamada HTTP.

Las capturas completadas desde estados FAILED o CANCELLED mantienen el
comportamiento existente: una confirmación de dinero recibido puede completar
la orden. REFUNDED conserva precedencia sobre las capturas tardías.

Las dos reproducciones originales se incorporaron a la suite habitual en
`tests/test_pagos_estados.py`, junto con verificaciones de conservación de
compra, transacciones y datos del pago. Se añadió una prueba con conexiones
PostgreSQL separadas: se confirma el reembolso mientras otra solicitud espera
la respuesta de captura. Las llamadas al proveedor y al correo se simulan.

Validación focalizada: **29/29 aprobadas**, 41.366 s, salida 0, contra el
PostgreSQL aislado de QA. Registro: `.qa_audit/critical_focused_20261001.txt`.
Esta ejecución precede a la incorporación de la prueba con conexiones separadas.

Validación final: **391/391 pruebas aprobadas**, 83.301 s, salida 0; incluye
la prueba con conexiones separadas y todos los cambios locales presentes.
Django system check sin incidencias. Registro:
`.qa_audit/critical_full_20261001.txt`. No se realizaron cobros reales.

## Seguimiento: cierre de PROD03, PROD04 y PROD05

- **PROD03:** `CambioPasswordSerializer` valida que `refresh`, cuando se
  envía, sea texto antes de guardar credenciales. Objetos, listas, números,
  booleanos y null producen 400 y conservan el hash de contraseña.
- **PROD04:** emisión y canje de tokens bloquean primero la cuenta del
  usuario, incluso cuando todavía no existe un token. Las emisiones se
  serializan: la última invalida las anteriores y deja un único token activo.
  El canje sigue siendo de un solo uso y comparte el orden de bloqueos.
- **PROD05:** la migración `0035_cedula_unica` añade un índice único sobre
  `LOWER(BTRIM(cedula_profesional))` para expedientes activos con cédula no
  vacía. El conflicto concurrente devuelve 400 de campo y revierte toda la
  actualización de perfil. Se comprueban mayúsculas, espacios y borrado lógico.

La migración **no resuelve duplicados automáticamente**: si los encuentra,
se detiene con un mensaje explícito y no modifica credenciales. No se consultó
la base real; antes de desplegar se deben revisar sus posibles duplicados.
La siguiente consulta permite localizarlos en el entorno autorizado:

```sql
SELECT LOWER(BTRIM(cedula_profesional)) AS cedula_normalizada, COUNT(*) AS total
FROM aspirantes
WHERE eliminado_en IS NULL AND NULLIF(BTRIM(cedula_profesional), '') IS NOT NULL
GROUP BY LOWER(BTRIM(cedula_profesional)) HAVING COUNT(*) > 1;
```

Las regresiones se incorporaron a la suite habitual en
`tests/test_production_hallazgos.py`. La barrera de recuperación simultánea
se coloca antes de emitir, para permitir que el bloqueo serialice las
escrituras sin crear un bloqueo artificial en el propio test.
Validación focalizada: **5/5 aprobadas**, 7.377 s, salida 0, antes de añadir
la regresión de canje concurrente. Registro:
`.qa_audit/remaining_focused_20261001.txt`.

Validación final: **397/397 aprobadas**, 106.308 s, salida 0, incluidas las
cinco regresiones PROD01–PROD05 y el canje concurrente. Se aplicó la migración
0035 al crear la base efímera desde cero. Django system check sin incidencias;
`makemigrations --check --dry-run`: No changes detected. Registro:
`.qa_audit/remaining_full_20261001.txt`. Todo se ejecutó contra PostgreSQL
aislado; no se modificó la base de aplicación ni se enviaron correos reales.

La revisión corresponde a `0fb61cd` **más los cambios locales presentes**,
incluidas las correcciones QA01–QA09 y las interacciones del muro. Esos cambios
todavía no están todos registrados en Git. No equivale a aprobar únicamente
el HEAD ni un artefacto publicado.

## Alcance y resultados

Se revisaron configuración, rutas, autenticación, permisos, registro/perfil,
pagos, almacenamiento privado, videos, cursos, certificados, correo,
migraciones, requisitos, documentación y pruebas. Se consultaron documentación
y avisos oficiales para contrastar dependencias y requisitos de despliegue.
No se encontraron instrucciones AGENTS.md.

| Verificación | Resultado |
|---|---|
| Suite existente | **348/348 aprobadas**, 75.024 s, salida 0 |
| Nuevos escenarios de producción | **5/5 fallan**, 2.550 s, salida 1; hallazgos PROD01–PROD05 |
| Migraciones desde cero | Aplicadas al crear las bases efímeras de pruebas |
| `makemigrations --check --dry-run` | No changes detected |
| Django system check | Sin errores |
| `check --deploy` con configuración aislada | W004, W008, W012 y W016; salida 0 **no significa aprobación** |
| Configuración real del módulo, sin `.env`, DEBUG=False | Falta STATIC_ROOT; collectstatic falla; falta reconocimiento de HTTPS del proxy |
| `pip check` del entorno del proyecto | No broken requirements found |
| `pip-audit` 2.10.1 sobre versiones instaladas | **17 avisos en 3 paquetes**, salida 1 |
| Marcadores fuertes de secretos en archivos versionados | Sin coincidencias en 118 archivos para claves AWS, tokens GitHub y claves privadas; no es un escaneo exhaustivo del historial |
| Documentación enlazada desde README | Faltan CERTIFICADOS.md, VIDEO_S3.md y VIDEO_S3_OPERACION.md |

Pruebas contra PostgreSQL 16 exclusivo en `127.0.0.1:55439`, bases
`test_qa_production_*`, con `scripts/qa_audit_run.py`. No se leyó el contenido de
`.env`, no se conectó a la base de aplicación ni se hicieron cobros, envíos
de correo o cargas reales. Las llamadas a PayPal/S3/correo de las pruebas
fueron simuladas. El análisis de dependencias consultó avisos públicos y se
instaló en `.qa_audit/audit_tools`, sin actualizar el entorno del proyecto.

El usuario indicó **Azure como destino probable**, todavía en definición;
no se proporcionó servicio concreto, dominio ni recursos configurados.
Los resultados de proxy y configuración son reproducciones con valores
sintéticos, **no una medición de un servidor de producción existente**.

## Bloqueos de código confirmados

| ID | Severidad | Resultado reproducido | Prioridad |
|---|---|---|---|
| PROD01 | Alta | Una respuesta de captura en curso reactiva una compra reembolsada | P0 |
| PROD02 | Alta | Una respuesta PENDING atrasada degrada una orden ya completada | P0 |
| PROD03 | Media | Cambio de contraseña con refresh no textual devuelve 500 | P1 |
| PROD04 | Media | Dos solicitudes de recuperación simultáneas devuelven 204 y 500 | P1 |
| PROD05 | Media | Dos cuentas registran la misma cédula simultáneamente | P1 |

### PROD01 — Una captura en curso puede revertir el reembolso local

- Ubicación: `core/services/pagos.py:678`, en particular la transición de
  `aplicar_resultado_de_captura`, y `_entregar_compra` en la línea 579.
- Escenario: una captura se inicia en estado APPROVED; PayPal devuelve una
  respuesta COMPLETED. Antes de persistirla, otra solicitud procesa el
  reembolso. Al aplicar la respuesta de captura pendiente, el bloqueo relee la
  orden, pero solo protege el estado COMPLETED, no REFUNDED.
- Obtenido: **orden COMPLETED, compra PAGADA** después del reembolso.
- Esperado: conservar **REFUNDED/REEMBOLSADA**; registrar el resultado tardío
  sin devolver disponibilidad ni créditos a una compra reembolsada.
- Evidencia: `ProductionPaymentChecks.test_PROD01_capture_in_flight_must_not_reactivate_refunded_purchase`.
- La reproducción controla el orden de las operaciones dentro de la respuesta
  simulada del proveedor. Las escrituras y restricciones de PostgreSQL son
  reales. No supone que una nueva consulta de PayPal devuelve información
  obsoleta: reproduce una respuesta ya obtenida pero aún no aplicada.
- Corrección propuesta: definir transiciones permitidas y volver a comprobar
  el estado bajo el bloqueo de la orden; proteger también la entrega de la
  compra. Cubrir captura/reembolso y eventos con IDs distintos concurrentes.

### PROD02 — Una respuesta PENDING degrada una orden completada

- Ubicación: `core/services/pagos.py:698`, rama PENDING.
- Escenario: aplicar COMPLETED y después una respuesta PENDING obtenida por
  otra operación en curso. La rama PENDING no verifica estados terminales.
- Obtenido: **orden APPROVED, compra PAGADA**; también se actualiza la
  transacción de captura a PENDING. El conjunto pierde coherencia.
- Esperado: conservar COMPLETED y su captura completada.
- Evidencia: `ProductionPaymentChecks.test_PROD02_stale_pending_must_not_downgrade_completed_order`.
- Corrección propuesta: usar la misma regla de transiciones para todos los
  resultados; verificar también respuestas tardías de denegación y error.

### PROD03 — Falta validar refresh en el cambio ordinario de contraseña

- Ubicación: `core/api/views.py:341`, después de guardar el nuevo hash.
- Solicitud autenticada a `POST /api/auth/password/`, con contraseñas actual
  y nueva válidas, y `refresh: {"unexpected": 1}`.
- Obtenido: **500** porque `token_hash` llama `.encode()` sobre un objeto.
- Esperado: error JSON **400** antes de modificar credenciales o sesiones.
- Evidencia: `ProductionAuthChecks.test_PROD03_password_change_non_string_refresh_must_return_400`.
- Corrección propuesta: validar el tipo de refresh como parte del serializer;
  probar objetos, listas, números, booleanos y null. La corrección previa de
  refresh/logout no cubría esta ruta.

### PROD04 — Recuperaciones simultáneas compiten por el token activo

- Ubicación: `core/services/tokens.py:59`, emisión de tokens; llamada desde
  `core/api/views.py:357`.
- Dos conexiones actualizan los tokens anteriores cuando todavía no existe
  ninguno; ambas intentan crear uno activo. El índice único protege la base,
  pero el conflicto de la segunda no se convierte en respuesta controlada.
- Obtenido: **[500, 204]**. Esperado: **[204, 204]**, manteniendo el contrato
  público de recuperación y una política definida para el último token.
- Evidencia: `ProductionConcurrencyChecks.test_PROD04_concurrent_recovery_requests_must_not_return_500`.
- Corrección propuesta: serializar emisión por usuario, incluso cuando no
  existe token previo, y verificar su interacción con el canje. La barrera
  sincroniza solicitudes reales sin sustituir escrituras de la base.

### PROD05 — La cédula es única solo en la validación previa

- Ubicación: `core/api/serializers.py:830` y `:890`;
  `database/schema.sql:195` no declara unicidad para cédula.
- Dos usuarios con expedientes diferentes validan la misma cédula antes de
  guardar. Bloquear cada expediente evita sobrescribirlo, pero no serializa
  el valor compartido entre expedientes diferentes.
- Obtenido: **[200, 200], dos duplicados persistidos**. Esperado: una sola
  cédula aceptada; la otra solicitud recibe 400 o conflicto documentado.
- Evidencia: `ProductionConcurrencyChecks.test_PROD05_two_users_must_not_register_the_same_cedula_concurrently`.
- Corrección propuesta: detectar y resolver duplicados existentes; añadir
  unicidad en base acorde con normalización, valores vacíos y borrado lógico;
  convertir el conflicto en error de campo con rollback adecuado.

## Dependencias: actualización y evaluación necesarias

El escáner revisó la instantánea de **38 distribuciones instaladas**; no
resolvió una instalación nueva de los requisitos flotantes. El entorno tiene
Django 6.1, DRF 3.18.0 y psycopg 3.3.4.

| Paquete instalado | Avisos detectados | Acción |
|---|---|---|
| Django 6.1 | 1: CVE-2026-15830 | Evaluar actualización a 6.1.1 y repetir suite; el aviso es de GeoDjango, que no se usa en este proyecto |
| PyJWT 2.13.0 | 13 | Actualizar a una versión corregida compatible, repetir pruebas JWT y volver a escanear; varios avisos listan 2.14.0, uno requiere 2.15.0 y uno no declara versión corregida |
| urllib3 2.7.0 | 3 | Evaluar 2.8.0, probar PayPal/S3 y volver a escanear |

**17 coincidencias de versión no significan 17 exploits demostrados.** No hay
GeoDjango instalado en INSTALLED_APPS, ni JWKS configurado en SIMPLE_JWT.
SimpleJWT 5.5.1 construye un diccionario nuevo de `options` en cada decode;
no se encontró el patrón de reutilización del aviso GHSA-gvp8-978c-rx2q.
Los avisos de urllib3 requieren condiciones de proxy TLS o streaming de
respuestas; la infraestructura de destino no fue proporcionada. No se demostró
un bypass JWT ni una intercepción TLS contra esta aplicación.

El aviso sin versión corregida requiere una decisión explícita basada en
aplicabilidad; no se promete que actualizar elimina todos los avisos.
La documentación oficial actual muestra Django 6.1.1 y changelogs de PyJWT
2.15.1 y urllib3 2.8.0. `requirements.txt` no fija Django, DRF, JWT, requests ni
otras dependencias, y no hay lockfile Python versionado. La instalación de
producción puede diferir del entorno probado.

Referencias: [versiones soportadas de Django](https://www.djangoproject.com/download/),
[changelog de PyJWT](https://pyjwt.readthedocs.io/en/stable/changelog.html),
[aviso de opciones mutables](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-gvp8-978c-rx2q),
[aviso de parsing corregido en PyJWT 2.15.0](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-42vr-xj54-vc7v),
[correcciones de urllib3 2.8.0](https://urllib3.readthedocs.io/en/stable/changelog.html).

## Preparación del despliegue

Estas son carencias verificadas en el repositorio o validaciones sin evidencia
del destino. No se presentan como defectos de un servidor que no se inspeccionó.

| Área | Estado observado | Condición para liberar |
|---|---|---|
| Perfil de producción | DEBUG predeterminado True; flags seguros predeterminados False; ejemplo de entorno orientado a localhost | Perfil explícito con DEBUG=False, clave propia, hosts/CORS exactos y enlaces HTTPS reales; comprobar arranque sin secretos obligatorios |
| TLS/proxy | No existe SECURE_PROXY_SSL_HEADER; con redirección activada, X-Forwarded-Proto=https produce 301 a la misma URL | Configurar la cabecera que el proxy elimina y establece de forma confiable; verificar HTTPS sin bucles, HTTP→HTTPS y host inválido |
| Cookies/HSTS | W004, W008, W012 y W016 con el perfil sin endurecer | Configurar TLS/cookies; elegir HSTS según los dominios reales; ejecutar check --deploy sobre el perfil definitivo |
| Servidor y estáticos | README solo documenta runserver; no hay lanzamiento de producción versionado. STATIC_ROOT=None hace fallar collectstatic | Servidor WSGI/ASGI de producción y comando reproducible, o configuración equivalente del proveedor; definir STATIC_ROOT y comprobar assets de admin/docs |
| PostgreSQL | Usuario predeterminado postgres y OPTIONS vacío; no hay configuración TLS en el proyecto | Rol de aplicación con privilegios mínimos; conexión y TLS acorde con el proveedor; límites/timeouts y migración sobre copia anonimizada |
| PDFs privados | FileSystemStorage y PRIVATE_MEDIA_ROOT local | Volumen persistente o storage privado compartido; backup y restauración junto con la BD; nunca publicar ese directorio como media pública |
| Videos S3 | URLs firmadas, If-None-Match y permisos cubiertos con mocks | Bucket privado, IAM mínimo, CORS real, expiración, PUT/GET/Range y reproducción/seek desde el frontend; limpieza de cargas fallidas y cuotas |
| Throttles | LocMemCache por defecto; no hay CACHES configurable ni cache compartida | Definir límites en el proxy y política entre workers; evaluar cache compartida y límites globales/IP, además de los de cuenta |
| PayPal | Sandbox predeterminado; eventos y capturas probados con mocks | Cerrar PROD01/02; probar sandbox real, firma, entregas fuera de orden, reintentos y política de reembolsos; configurar live y reconciliación de órdenes |
| Correo | SMTP síncrono y reintentos manuales; tokens no reconstruibles | SMTP real, remitente autenticado, enlaces correctos, alertas y ejecución programada de reintentos; EMAIL_REDIRIGIR_A vacío en producción |
| Observabilidad | LOGGING vacío; health comprueba únicamente SELECT 1 | Logs centralizados sin secretos, métricas/alertas de 5xx, correo fallido y pagos pendientes; distinguir liveness de readiness según el proveedor |
| Respaldo y rollback | No se encontró procedimiento de restauración/despliegue versionado | Restauración ensayada de BD, archivos privados y objetos; RPO/RTO definidos; rollback de aplicación compatible con las migraciones |
| Rendimiento | Varios listados no tienen paginación; muro combina conteos de dos relaciones | Prueba de carga con datos representativos, índices y EXPLAIN; paginar recursos grandes y medir tiempos/memoria. No se midió un fallo de rendimiento |
| Artefacto/CI | No hay CI ni manifest de despliegue en archivos versionados; correcciones locales pendientes de Git | Elegir y versionar el artefacto exacto, fijar dependencias, ejecutar checks/tests/audit en CI y documentar despliegue/rollback; Docker no es requisito si el proveedor ofrece equivalente |
| Datos demo/documentación | Migraciones instalan datos de demostración; contraseñas deshabilitadas mediante 0033; tres documentos enlazados faltan | Aplicar y verificar 0033, aprovisionar administrador propio, decidir limpieza de datos ficticios sin romper relaciones y completar contratos operativos |
| Frontend/privacidad | Frontend ausente de este repositorio; resultados de certificados y autores del muro son públicos por diseño | E2E de compra/curso/recuperación, móviles y accesibilidad; revisar consentimiento y alcance de información pública según la política del producto |

La documentación de [despliegue de Django](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/)
requiere evaluar configuración definitiva y servidor de producción. Los
[throttles de DRF](https://www.django-rest-framework.org/api-guide/throttling/)
no constituyen una protección suficiente contra abuso y pueden tener carreras;
poner Redis por sí solo no garantiza un límite estricto bajo concurrencia.

### Riesgos por revisión, todavía sin reproducción

- `_verificar_monto` acepta ausencia simultánea de monto y moneda; conviene
  fallar cerrado ante respuestas incompletas antes de entregar una compra.
- `_denegar_orden` y algunas ramas de error pueden modificar estados
  terminales; extender la matriz de eventos tardíos de PROD01/02.
- El reembolso parcial marca toda la compra reembolsada; hace falta un contrato
  de negocio explícito y probar varias devoluciones/concurrencia.
- `correo.enviar` crea el registro fuera de su captura de excepciones. Una
  falla de BD al crear el aviso puede propagarse, aunque el módulo declara
  que nunca lanza; revisar el callback después del commit de pago.
- Las URLs S3 ya firmadas siguen funcionando hasta su caducidad aunque cambie
  la inscripción. Definir si una hora satisface la política de revocación.

## Orden de cierre y aceptación

### Decisiones específicas para Azure

La opción inicial a evaluar es App Service Linux para Django y PostgreSQL
Flexible Server; Container Apps sigue siendo una alternativa si se elige
desplegar una imagen. Es una propuesta de evaluación, no una selección de
arquitectura ni una estimación de costos.

- **App Service:** confirmar runtime, build y arranque de `config.wsgi`.
  Su automatización puede ejecutar `collectstatic`, por lo que el fallo
  reproducido de STATIC_ROOT debe resolverse. Configurar la cabecera HTTPS
  según su proxy. [Documentación Python/Django en App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-language-python).
- **PDFs:** el directorio predeterminado está dentro del código; con build
  automatizado puede quedar en una ubicación temporal. Elegir almacenamiento
  persistente fuera de esa ruta y ensayar reinicio/escalado. Si se usa
  contenedor propio, verificar también su configuración de persistencia.
  [Build y archivos de Python](https://learn.microsoft.com/en-us/azure/app-service/configure-language-python),
  [persistencia de contenedores](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container).
- **PostgreSQL:** comprobar red, certificados y `sslmode` en la configuración
  Django; OPTIONS está vacío. La primera migración requiere `pgcrypto`;
  verificar allowlist y permisos antes de ejecutarla. [TLS en Flexible Server](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls-how-to-connect),
  [allowlist de extensiones](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-allow-extensions).
- **Videos:** el código depende de boto3 y S3. Alojar Django en Azure permite
  conservar ese backend; pasar a Blob Storage requiere adaptar upload, HEAD,
  reproducción firmada y sus pruebas. No basta cambiar el nombre del bucket.
- **Secretos:** definir App Settings/Key Vault y acceso por identidad
  administrada donde sea compatible. Esa identidad no sustituye automáticamente
  credenciales de PayPal o AWS. [Conexiones seguras desde App Service](https://learn.microsoft.com/en-us/azure/app-service/tutorial-connect-overview).
- **Aceptación:** probar staging desde Azure con frontend, dominio HTTPS,
  PostgreSQL y storage elegidos; reinicio, escalado, restauración, webhook y
  recuperación. Los tests actuales en Windows no validan el despliegue Linux.

### Secuencia recomendada

1. **P0 cerrado en código:** PROD01/02 y regresiones de captura, devolución y
   eventos atrasados/concurrentes aprobadas.
2. **P1 cerrado en código:** PROD03/04/05; revisar duplicados de cédula en el
   entorno definitivo antes de aplicar 0035; mantener aprobadas las regresiones.
3. **P1:** actualizar/fijar dependencias y repetir escaneo. Cada aviso restante
   necesita aplicabilidad evaluada y decisión documentada.
4. **P1:** preparar el entorno definitivo: TLS/proxy, servidor, estáticos,
   PostgreSQL, archivos persistentes, cache/límites, correo y PayPal/S3.
5. **P1:** ensayar migración de un esquema existente, backup/restauración,
   rollback, despliegue desde un commit identificado y aprovisionamiento de
   administrador; aprobar smoke tests reales y E2E de staging.
6. **P2:** cerrar documentación, datos demo, observabilidad, paginación y
   mediciones de carga según volumen previsto. Observabilidad mínima y backup
   son condiciones de la primera liberación, no mejoras opcionales posteriores.

La liberación requiere cinco regresiones nuevas aprobadas, suite base aprobada,
perfil de producción comprobado, dependencias evaluadas y evidencia de las
integraciones/restauración. Esta auditoría no fija una fecha de salida sin esas
condiciones ni sustituye una prueba del entorno de destino.

## Evidencia y reproducción

- `tests/production_audit_checks.py`: cinco casos nuevos. El nombre no empieza
  con `test_`, para distinguir la suite histórica de las reproducciones de
  esta auditoría. Inicialmente fallaban sin expectedFailure. Ahora también
  se incluyen por herencia en `test_pagos_estados.py` y
  `test_production_hallazgos.py`, dentro de la suite habitual.
- `.qa_audit/production_baseline_20261001.txt`: suite base, 348/348.
- `.qa_audit/production_new_checks_20261001.txt`: cinco fallos y estados persistidos.
- `.qa_audit/production_deploy_20261001.txt`: advertencias de despliegue.
- `.qa_audit/production_config_probe_20261001.txt`: DEBUG=False sin dotenv,
  configuración, fallo de estáticos y reproducción de redirección HTTPS.
- `.qa_audit/production_migrations_20261001.txt`: consistencia de modelos.
- `.qa_audit/production_installed_20261001.txt`: versiones revisadas.
- `.qa_audit/production_dependencies_20261001.json`: resultado íntegro de pip-audit.
- `.qa_audit/production_repo_probe_20261001.json`: archivos y marcadores revisados.

```powershell
# Solo contra el clúster local de QA:
& 'C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe' -D .qa_audit/pgdata -l .qa_audit/postgres.log -o '-h 127.0.0.1 -p 55439' -w start
$env:QA_RUN='production_review'
.\.venv\Scripts\python.exe scripts/qa_audit_run.py test tests.production_audit_checks --noinput -v 2
# Suite histórica + nuevos casos juntos (actualmente devuelve fallos):
.\.venv\Scripts\python.exe scripts/qa_audit_run.py test tests tests.production_audit_checks --noinput -v 1
& 'C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe' -D .qa_audit/pgdata -m fast -w stop
```

Esta tarea añadió el informe y las reproducciones; no corrigió los nuevos
hallazgos ni actualizó dependencias de la aplicación. Se conservaron los
cambios locales existentes. No hubo despliegue. El clúster local de QA se
detuvo al concluir.
