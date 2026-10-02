# Seguimiento de auditoría de producción — 2 de octubre de 2026

**Dictamen: todavía no liberar.** PROD01–PROD05 siguen corregidos y la suite
habitual pasa. Se reprodujeron tres defectos adicionales, PROD06–PROD08, de
los riesgos pendientes del informe anterior. También siguen abiertos los
avisos de dependencias y la configuración de despliegue.

Revisión del commit **56f762b**, rama `KuriZd02`. El árbol estaba limpio al
iniciar. Esta revisión añade únicamente este informe, su enlace en el informe
anterior y las reproducciones; no modifica aplicación ni dependencias.

Durante el trabajo aparecieron cambios externos en `core/api/views.py` y
`tests/test_verificacion_correo.py` para enviar verificación al registrarse.
Se conservaron sin modificarlos. La ejecución base no acredita esos cambios
posteriores ni su prueba nueva; requiere una nueva ejecución tras estabilizar
el árbol. El fallo de correo de PROD07 también debe evaluarse en esa ruta.

## Evidencia actual

| Verificación | Resultado |
|---|---|
| Suite habitual, incluidas PROD01–PROD05 | **404/404 aprobadas**, 112.221 s, salida 0 |
| Nuevas reproducciones explícitas | **3/3 no aprobadas**: 2 fallos y 1 error, 0.646 s, salida 1 |
| Migraciones desde cero | Aplicadas al crear las bases efímeras; incluye 0036 |
| Django system check | Sin incidencias |
| Dependencias instaladas | **17 avisos en 3 paquetes**, nuevo escaneo, salida 1 |
| Configuración DEBUG=False sin dotenv | STATIC_ROOT y cabecera de proxy ausentes; OPTIONS de PostgreSQL vacío |
| collectstatic, dry run | ImproperlyConfigured por STATIC_ROOT ausente |
| HTTPS recibido por proxy | Con redirección activada y X-Forwarded-Proto=https: 301 a la misma URL HTTPS |
| Cache | DatabaseCache compartida por defecto; Redis configurable, paquete redis no declarado |

Los tests usan PostgreSQL 16 aislado en `127.0.0.1:55439`, con bases
`test_qa_followup_*`. No se leyó `.env`, no se consultó la base real ni se
hicieron cobros, envíos SMTP o cargas reales. Los proveedores se simulan.
El escáner sí consultó avisos públicos. No se actualizaron paquetes.
Los probes de configuración usan valores sintéticos, sin conexión a la BD.

## Hallazgos reproducidos

### PROD06 — Una captura sin importe ni moneda entrega la compra

**Alta, P0.** `core/services/pagos.py`, `_verificar_monto` y
`aplicar_resultado_de_captura`.

Una respuesta COMPLETED con ID de captura pero sin `monto` ni `moneda` pasa
la validación. Se completa la orden y se entrega la compra; la transacción
usa como respaldo el importe local. No hay evidencia del importe realmente
cobrado en esa respuesta. La reproducción espera MontoCapturadoDistinto y
falla porque no se lanza.

No se demostró que un cliente pueda falsificar una respuesta de PayPal.
El defecto afecta el manejo de respuestas incompletas del proveedor.

Corrección: exigir importe y moneda válidos y coincidentes antes de entregar;
conservar el diagnóstico para revisión y probar campos omitidos, inválidos,
importes distintos y moneda distinta.

Evidencia: `PaymentFollowupChecks.test_PROD06_missing_amount_and_currency_must_not_deliver`.

### PROD07 — El registro de correo puede hacer fallar el callback de pago

**Media, P1.** `core/services/correo.py:enviar` y callback de
`transaction.on_commit` en `core/services/pagos.py`.

Al simular OperationalError en `EnvioCorreo.objects.create`, el callback
propaga la excepción. Ese registro se crea fuera de la captura de errores,
pese al contrato del módulo de no propagar fallos de correo.

La prueba ejecuta el callback con `captureOnCommitCallbacks(execute=True)`:
demuestra la propagación, no simula un commit irreversible real ni verifica
una respuesta HTTP. En la ruta de producción el callback corre después del
commit, por lo que puede fallar la solicitud aunque el pago ya haya quedado
persistido. Esta consecuencia se deduce de la ubicación del callback.

Corrección: aislar los fallos del aviso del resultado financiero, registrar
el error por un canal independiente y mantener una vía de reconciliación
cuando no se puede crear la fila de correo. Evitar anunciar rollback de un
pago que ya se confirmó.

Evidencia: `PaymentFollowupChecks.test_PROD07_email_log_failure_must_not_fail_committed_payment`.

### PROD08 — El mismo reembolso concurrente provoca IntegrityError

**Media, P1.** `core/services/pagos.py:_reembolsar_orden`.

Dos conexiones consultan el mismo ID de devolución antes de tomar el bloqueo
de la orden. Ambas ven que no existe. La primera lo registra; la segunda,
aunque obtiene después el bloqueo de la orden, no repite la consulta y choca
con el índice único. Resultado: `integrity-error` y `ok`, en vez de dos
operaciones idempotentes y una sola devolución registrada.

La reproducción invoca directamente el servicio. El bloqueo actual por ID
de evento protege entregas del mismo evento, pero no serializa dos eventos
diferentes que se refieran al mismo reembolso; el servicio es compartido por
ambas rutas. No se probó este escenario contra PayPal real.

Corrección: comprobar idempotencia dentro de la transacción, después de
bloquear la orden; verificar también devolución duplicada con distintos IDs
de evento y conservar la captura asociada.

Evidencia: `RefundConcurrencyChecks.test_PROD08_same_refund_concurrent_delivery_must_be_idempotent`.

## Dependencias y despliegue

El nuevo escaneo mantiene Django 6.1: 1 aviso; PyJWT 2.13.0: 13; urllib3
2.7.0: 3. Se revisaron las 38 distribuciones instaladas. La evaluación de
aplicabilidad del informe anterior sigue pendiente de cierre; no se demostró
un exploit por cada coincidencia del escáner. requirements.txt continúa sin
fijar gran parte de los paquetes y no hay lockfile Python versionado.

Las fuentes oficiales consultadas siguen mostrando
[Django 6.1.1](https://www.djangoproject.com/download/),
[PyJWT 2.15.1](https://pyjwt.readthedocs.io/en/stable/changelog.html) y
[urllib3 2.8.0](https://urllib3.readthedocs.io/en/stable/changelog.html).
La actualización debe verificarse con tests y un nuevo escaneo antes de dar
por cerrado este bloqueo.

**Cambio respecto al informe anterior:** ya hay cache compartida en
PostgreSQL y la migración 0036 crea la tabla. El señalamiento anterior de
LocMemCache por defecto está superado. Redis requiere declarar su dependencia
si se selecciona; no se probó ese backend. Una cache compartida tampoco
convierte los contadores de DRF en límites atómicos bajo concurrencia.

Siguen sin resolverse STATIC_ROOT, configuración confiable del proxy HTTPS,
TLS de PostgreSQL, arranque de producción, CI y lockfile. También quedan las
validaciones de persistencia de archivos, integraciones reales, restauración,
observabilidad y staging en Azure. La rama y los cambios anteriores ya están
publicados en Git; ese pendiente del informe anterior está cerrado.

El reembolso parcial continúa marcando toda la compra como reembolsada.
Falta definir la política de negocio antes de clasificarlo como defecto:
qué ocurre con créditos disponibles/consumidos y con devoluciones sucesivas.
La revocación de URLs S3 ya firmadas sigue limitada por su caducidad.

## Orden de cierre

1. Corregir PROD06 y añadir su matriz de validación a la suite habitual.
2. Corregir PROD07 y PROD08; verificar callbacks y concurrencia real.
3. Actualizar/fijar dependencias y documentar cada aviso restante.
4. Preparar el perfil de despliegue y validar migraciones sobre copia del
   entorno destino, duplicados de cédula, restauración e integraciones.

## Reproducción

Las tres pruebas nuevas están en `tests/production_audit_followup_checks.py`.
Se ejecutan explícitamente para conservar la separación entre la suite base
y los defectos abiertos; no tienen expectedFailure.

```powershell
$env:QA_RUN='followup_new_20261002'
.\.venv\Scripts\python.exe scripts/qa_audit_run.py test tests.production_audit_followup_checks --noinput
```

El comando requiere el clúster aislado de QA iniciado en el puerto 55439.
Evidencia local ignorada por Git:

- `.qa_audit/followup_baseline_20261002.txt`
- `.qa_audit/followup_new_20261002.txt`
- `.qa_audit/followup_installed_20261002.txt`
- `.qa_audit/followup_dependencies_20261002.json`
- `.qa_audit/followup_config_20261002.json`
