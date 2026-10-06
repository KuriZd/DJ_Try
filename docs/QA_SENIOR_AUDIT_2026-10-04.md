# Auditoría QA Senior — 4 de octubre de 2026

## Evaluación general

**Recomendación: no liberar todavía a producción.** La suite habitual aprueba
451 pruebas, pero las reproducciones adicionales revelan fallos de validación
financiera, callbacks de correo, idempotencia de reembolsos y medios publicados.
La preparación del despliegue tampoco está acreditada. No se confirmó ningún
hallazgo de severidad crítica ni una explotación externa del sistema.

Se revisó `7da40657020faafd518b44be1e9f8a2d0ca3ac93`, rama `KuriZd02`.
El árbol estaba limpio. No se encontraron archivos AGENTS.md en el repositorio.
Se leyeron README, GUIA_COMPLETA, CURSOS, CORREO, las auditorías anteriores,
configuración, modelos, rutas, permisos, servicios y pruebas. Las auditorías
previas se usaron como lista de riesgos, no como evidencia de ejecución actual.

**No se modificó código de la aplicación.** Se crearon este informe y
`tests/qa_senior_20261004_checks.py`, fuera del discovery habitual. Los scripts
y salidas auxiliares están en `.qa_audit`, ignorado por Git. No se realizaron
envíos, cobros, cargas S3 ni cambios en la base de aplicación. No se leyó `.env`.

## Arquitectura, contratos y alcance

Backend Django/DRF, PostgreSQL y JWT con sesiones revocables. Parte del dominio
usa modelos unmanaged sobre tablas SQL; otras tablas son administradas por
migraciones Django. Los módulos principales son cuenta/expediente, vacantes y
postulaciones, reportes/compras psicométricas, PayPal, certificados, cursos,
videos y portadas, muro con adjuntos, moderación y correo.

El frontend no está incluido: no hay package.json ni aplicación React ejecutable
en este repositorio. Las plantillas sirven correo y previsualización de enlaces.
No se atribuyen al frontend fallos que no pudieron observarse.

### Requisitos confirmados

- Autorización por permisos efectivos de roles; `empresa` conserva permisos
  amplios equivalentes a administración por decisión documentada.
- Aspirantes ven su expediente/postulaciones; permisos de consulta amplían el
  alcance. Un ID ajeno fuera de alcance responde 404.
- El catálogo activo y el muro son públicos. El contenido privado de cursos,
  los documentos y el progreso requieren autorización específica.
- Publicar y subir adjuntos exige cuenta con correo verificado. Se confirma
  la carga en S3 antes de asociarla a una publicación.
- La captura debe coincidir exactamente con el importe reservado; entregar
  una compra exige cobro COMPLETED. Un reembolso revoca la compra.
- El correo no debe hacer fallar la operación que lo origina, según el
  contrato explícito de `core/services/correo.py`.
- Cambios de temario recalculan progreso; certificados de finalización
  anteriores se conservan. No se exige borrar certificados al agregar lecciones.
- Los adjuntos de una publicación no cambian durante la edición.

### Supuestos y decisiones sin confirmar

Política de reembolsos parciales y créditos consumidos; volumen objetivo,
SLA/RPO/RTO; dominio/servicio definitivo en Azure; reglas de revocación de URLs
firmadas; consentimiento para datos públicos; necesidad de cuotas por usuario.
No se califican incumplimientos de estos puntos como defectos funcionales.

### Ejecución

README describe Python 3.12+, PostgreSQL 15+, instalación de requirements,
`manage.py migrate` y `manage.py runserver`. No se usaron esos comandos con la
configuración real. Para la auditoría se utilizó PostgreSQL 16 exclusivo en
`127.0.0.1:55439`, bases efímeras `test_qa_senior_*`, locmem para correo y
dobles de PayPal/S3. `scripts/qa_audit_run.py` bloquea sockets hacia destinos
distintos del clúster aislado y `tests/qa_settings.py` evita cargar dotenv.

## Hallazgos reproducidos, ordenados por severidad

### PROD06 — Se entrega una compra sin evidencia de importe y moneda — Alta

**Justificación:** afecta integridad financiera; requiere una respuesta
incompleta del proveedor, no un acceso anónimo demostrado.
**Ubicación:** `core/services/pagos.py:663`, `_verificar_monto`.

**Precondiciones/pasos:** crear orden APPROVED con compra pendiente; simular
respuesta de captura COMPLETED con ID, omitiendo monto y moneda; ejecutar
`aplicar_resultado_de_captura`.
**Esperado:** rechazar la respuesta incompleta, no entregar y dejar revisión.
**Observado:** no lanza MontoCapturadoDistinto; procesa la captura y entrega.
El código usa el importe local como respaldo al registrar la transacción.
**Evidencia:** falla
`PaymentFollowupChecks.test_PROD06_missing_amount_and_currency_must_not_deliver`:
`AssertionError: MontoCapturadoDistinto not raised` en la ejecución actual.
**Impacto:** créditos/servicios entregados sin validar cuánto se cobró.
**Corrección/verificación:** exigir importe y moneda válidos y coincidentes;
probar ausencia parcial/total, formato inválido, monto/currency diferentes y
no emisión de comprobante ni entrega en cada rechazo. No se demostró que un
cliente pueda falsificar la respuesta del proveedor.

### PROD07 — El registro de correo propaga un fallo después del pago — Media

**Justificación:** produce un resultado fallido para una operación financiera
ya confirmada; depende de una falla de BD al registrar el aviso.
**Ubicación:** `core/services/correo.py:84` y
`core/services/pagos.py:802`, callback de on_commit.

**Precondiciones/pasos:** completar una captura válida; simular OperationalError
al crear EnvioCorreo; ejecutar el callback con captureOnCommitCallbacks.
**Esperado:** la falla del aviso no propaga una excepción del pago.
**Observado:** OperationalError escapa desde `EnvioCorreo.objects.create`.
**Evidencia:** error de
`PaymentFollowupChecks.test_PROD07_email_log_failure_must_not_fail_committed_payment`;
la traza llega de on_commit a avisos.enviar_comprobante y correo.enviar.
**Impacto:** reintentos innecesarios, usuario confundido sobre su compra y
comprobante sin registro para reintentar. El posible error HTTP tras commit
se deduce del callback; no se simuló una caída real de PostgreSQL ni una
respuesta HTTP posterior a un commit irreversible.
**Corrección/verificación:** aislar toda la operación de aviso, incluyendo su
registro; observabilidad independiente y reconciliación de pagos sin correo.
Probar fallo al crear/actualizar registro, fallo SMTP y callback tras commit;
preservar compra, idempotencia y respuesta financiera.

### PROD08 — Reembolsos simultáneos del mismo ID chocan — Media

**Justificación:** genera fallos y reintentos del webhook; el índice único
evita la duplicación persistida, por lo que no se afirma devolución doble.
**Ubicación:** `core/services/pagos.py:1048`, `_reembolsar_orden`.

**Precondiciones/pasos:** orden capturada; dos conexiones consultan el mismo
refund ID; sincronizar después de comprobar inexistencia y procesar ambas.
**Esperado:** dos respuestas idempotentes con una devolución registrada.
**Observado:** `['integrity-error', 'ok']`.
**Evidencia:** falla
`RefundConcurrencyChecks.test_PROD08_same_refund_concurrent_delivery_must_be_idempotent`.
La comprobación precede al bloqueo de orden y no se repite bajo bloqueo.
**Impacto:** errores/reintentos y diagnóstico inconsistente de devoluciones.
**Corrección/verificación:** comprobar idempotencia después de bloquear la orden;
probar concurrencia y eventos diferentes para la misma devolución. El test
invoca el servicio directamente; el bloqueo por event ID del webhook no protege
dos IDs diferentes referidos a un refund común. No se llamó a PayPal real.

### QAS01/QAS02 — Un PNG de ocho bytes se acepta como imagen — Media

**Justificación:** una carga corrupta se publica como válida, causando imágenes
rotas; no se demostró XSS, ejecución de archivos ni vulnerabilidad del navegador.
**Ubicación:** `core/services/medios_publicacion.py:35` y `:126`,
`core/services/portadas_curso.py:59`.

**Precondiciones/pasos:** instructor autorizado o aspirante verificado;
simular S3 con ContentLength=8, ContentType=image/png y cuerpo compuesto solo
por `89 50 4E 47 0D 0A 1A 0A`; confirmar portada y adjunto por sus endpoints.
**Esperado:** 400, sin portada activa ni adjunto listo: faltan IHDR, píxeles e IEND.
**Observado:** ambos responden 200; el adjunto queda `listo`.
**Evidencia:** fallan QAS01 y QAS02 en `SeniorMediaChecks`; ambas verificaciones
solo comprueban la firma inicial del formato. Los fixtures existentes usan
cabeceras sintéticas, por lo que no cubren decodificación real.
**Impacto:** contenido roto que el usuario creyó haber cargado correctamente;
archivos inútiles almacenados y mostrados públicamente.
**Corrección/verificación:** validar estructura/decodificación y límites de
dimensiones/recursos, con un proceso apropiado al formato. Probar imágenes
reales, truncadas y payload con firma válida pero sin imagen; no sustituir
fixtures válidos únicamente por secuencias de magic bytes.

### QAS04 — Una limpieza diferida borra la portada que volvió a quedar activa — Media

**Justificación:** inconsistencia BD/S3 bajo un orden de solicitudes permitido;
requiere reemplazo y reconfirmación durante la ventana del callback.
**Ubicación:** `core/api/curso_views.py:160`, especialmente `:187–192`.

**Precondiciones/pasos:** confirmar portada A; confirmar B capturando sin ejecutar
su callback de borrar A; volver a confirmar A mientras existe; ejecutar el
callback pendiente de B.
**Esperado:** el archivo de la portada actualmente referenciada permanece.
**Observado:** el curso referencia A y S3 recibe delete_object de A.
**Evidencia:** QAS04: `current cover deleted: True`; falla assertNotIn.
**Impacto:** el catálogo firma una URL a un archivo eliminado; portada rota.
**Corrección/verificación:** evitar reactivar versiones retiradas, controlar el
ciclo de vida de cada carga y hacer la limpieza compatible con nuevas referencias.
Una consulta aislada antes del borrado también puede tener carreras. Probar
callbacks demorados, reintentos y solicitudes con conexiones separadas.
La reproducción controla el orden de callbacks; no es una prueba de estrés
ni una ejecución real concurrente contra S3.

### QAS06 — Una cabecera de IP enviada por el cliente elude el límite — Media

**Justificación:** debilita protección de login; aplica si el backend es accesible
directamente o el proxy no elimina/reescribe cabeceras no confiables.
**Ubicación:** `core/api/throttles.py:20–21`, get_ident de DRF, configuración
REST_FRAMEWORK sin una política NUM_PROXIES explícita.

**Precondiciones/pasos:** conexión con REMOTE_ADDR=127.0.0.1, mismo email y seis
logins inválidos. Primero sin X-Forwarded-For; limpiar cache y repetir variando
solo esa cabecera en cada solicitud.
**Esperado:** el cliente no puede cambiar la identidad usada para el límite;
la sexta respuesta sigue siendo 429.
**Observado:** control `[400,400,400,400,400,429]`; cabeceras manipuladas
`[400,400,400,400,400,400]`. 400 es el contrato actual de credenciales inválidas.
**Evidencia:** QAS06 falla con `400 != 429`; se sustituyó verify_password por
False para no gastar tiempo de hashing, conservando throttles y respuestas API.
**Impacto:** más intentos de fuerza bruta o abuso desde una conexión.
**Corrección/verificación:** definir frontera de confianza y cadena de proxies;
ignorar cabeceras del cliente en acceso directo, restringir backend y aplicar
controles en proxy. Probar IP real, cabeceras falsificadas y proxy confiable.
No se inspeccionó un proxy de producción; no se afirma que Azure permita la
misma manipulación si está correctamente configurado.

### QAS07 — collectstatic no puede ejecutarse con la configuración disponible — Media

**Justificación:** bloqueo reproducible del proceso de despliegue estándar;
no describe un sitio Azure que ya haya sido inspeccionado.
**Ubicación:** `config/settings.py:169`, STATIC_URL sin STATIC_ROOT.

**Precondiciones/pasos:** importar settings sin dotenv, DEBUG=False y clave/host
sintéticos; ejecutar collectstatic con dry_run=True.
**Esperado:** un perfil de despliegue reproducible puede recopilar estáticos.
**Observado:** ImproperlyConfigured por ausencia de STATIC_ROOT.
**Evidencia:** `.qa_audit/senior_config_20261004.json`.
**Impacto:** build fallido o recursos de admin/docs sin servir.
**Corrección/verificación:** definir destino y estrategia de servicio de
estáticos; verificar collectstatic y GET reales en staging. No exige Docker.

### QAS08 — HTTPS detrás de proxy puede redirigir a la misma URL — Media

**Justificación:** bloqueo de navegación si se activa redirección TLS sin
reconocer el proxy; requiere la topología descrita.
**Ubicación:** `config/settings.py:313`; falta SECURE_PROXY_SSL_HEADER.

**Precondiciones/pasos:** activar SECURE_SSL_REDIRECT, simular petición HTTP
interna del proxy con X-Forwarded-Proto=https y host autorizado.
**Esperado:** 200 al estar la conexión pública protegida por TLS.
**Observado:** 301 a `https://audit.example.test/api/health/`, la misma URL pública.
**Evidencia:** probe de SecurityMiddleware: proxy_header=null, status=301.
**Impacto:** bucle de redirección al repetir esa topología.
**Corrección/verificación:** reconocer únicamente la cabecera establecida por
un proxy confiable, que reescribe la del cliente; verificar HTTPS, HTTP y host
inválido en el destino. No habilitar confianza global en cabeceras arbitrarias.

### QAS03 — Portada pendiente aparece como caída del almacenamiento — Baja

**Justificación:** mensaje/estado incorrecto para una condición recuperable;
no hay pérdida de datos. Depende de un bucket sin permiso ListBucket.
**Ubicación:** `core/api/curso_views.py:172–175`; comparar
`core/services/medios_publicacion.py:aun_no_existe` y el endpoint de adjuntos.

**Precondiciones/pasos:** carga aún ausente, política de mínimo privilegio
descrita por medios_publicacion y test de adjuntos; HeadObject devuelve 403;
confirmar portada con cliente S3 simulado.
**Esperado:** informar carga pendiente, como el flujo de adjuntos (409).
**Observado:** 503 «almacenamiento ... no disponible»; solo 404 se interpreta
como archivo pendiente en portadas.
**Evidencia:** QAS03: `503 != 409`; test existente de portadas cubre solo 404.
**Impacto:** el usuario diagnostica una caída y reintenta una operación equivocada.
**Corrección/verificación:** acordar clasificación según IAM real y conservar
diagnóstico de AccessDenied. Un 403 también puede significar falta de permisos:
no convertir todos los 403 en «pendiente» sin contexto. Probar ambos escenarios.
No se verificó IAM real; sí el tratamiento inconsistente bajo ese supuesto documentado.

### QAS09 — README enlaza tres documentos ausentes — Baja

**Justificación:** afecta instalación, integración y operación, sin fallo de datos.
**Ubicación:** README.md, sección Documentación del proyecto.
**Pasos:** abrir los enlaces a CERTIFICADOS.md, VIDEO_S3.md y VIDEO_S3_OPERACION.md.
**Esperado:** documentación disponible. **Observado/evidencia:** los tres
Test-Path devuelven False. **Impacto:** contratos y procedimientos incompletos
para integradores. **Corrección/verificación:** restaurar o corregir enlaces y
comprobar todos los enlaces internos automáticamente.

## Riesgos por revisión, sin defecto adicional confirmado

- **Reembolsos parciales:** la compra completa pasa a REEMBOLSADA. Falta política
  documentada de créditos/servicios; no se supone que toda devolución sea total.
- **Pagos:** `_marcar_desajuste_de_monto` puede sobrescribir diagnóstico de una
  orden terminal ante una respuesta tardía distinta; no se añadió reproducción.
- **Dependencias:** requirements flotantes; versiones locales Django 6.1,
  PyJWT 2.13.0 y urllib3 2.7.0 coinciden con el escaneo del 2 de octubre,
  que reportó 17 avisos. **No se repitió el escaneo externo en esta auditoría**;
  ese número es evidencia histórica, no resultado nuevo ni 17 exploits probados.
- **Redis:** configurable pero paquete redis no declarado. PostgreSQL cache es
  el predeterminado y su migración pasó; no se probó Redis ni límites atómicos.
- **Carga/S3:** URLs PUT no limitan el tamaño real antes de recibir el objeto;
  los límites se verifican al confirmar. Falta cuota, limpieza y carga real.
  Portadas subidas pero nunca confirmadas no tienen fila propia y no las recoge
  limpiar_adjuntos; requiere ciclo de vida S3 o reconciliación de ese prefijo.
- **Operaciones costosas:** inspección S3 dentro de transacciones con bloqueo;
  cambios de temario recorren todas las inscripciones. Sin datos representativos
  no se afirma una latencia concreta ni un fallo bajo carga.
- **Paginación:** muro/reportes de moderación y certificados sí tienen paginación;
  catálogo, usuarios, aspirantes, videos y varios listados no declaran paginación.
  Catálogo con 1 y 13 cursos: **2 consultas en ambos casos**, respuesta completa
  de 13 filas. No se confirmó N+1 en ese escenario ni problema de carga.
- **Roles/privacidad:** empresa con permisos amplios y datos públicos de autores
  y verificación de certificados son contratos actuales. Requieren aceptación
  de producto y privacidad, no se reportan como escalación no autorizada.
- **Revocación:** URLs firmadas sobreviven hasta expirar; la previsualización
  usa firmas de hasta siete días. Falta política de revocación/cachés externos.
- **Producción:** TLS PostgreSQL, servidor de arranque, logs/alertas, cache,
  persistencia y backup/restauración, CI y artefacto fijo sin aceptación demostrada.

## Pruebas y resultados

| Verificación ejecutada | Resultado |
|---|---|
| Suite habitual | 451 aprobadas, 74.837 s, salida 0 |
| Reproducciones finales aisladas | 9 tests: 1 aprobado, 7 fallos, 1 error; 0.605 s, salida 1 |
| PROD01–PROD05 | Incluidas en suite habitual, siguen aprobadas |
| PROD06–PROD08 | Los tres siguen abiertos en la ejecución actual |
| QAS01–QAS04, QAS06 | Reproducciones fallidas; QAS05 medición aprobada |
| Migraciones desde cero hasta 0038 | Aplicadas en bases temporales |
| makemigrations --check --dry-run | No changes detected |
| Django system check | Sin incidencias en tests |
| check --deploy, perfil QA | W004, W008, W012, W016; no acredita configuración de producción |
| pip check | No broken requirements found; no es un escaneo de seguridad |
| Configuración sintética | Fallos de estáticos/proxy; OPTIONS de BD vacío |

Cobertura funcional disponible: sesiones/revocación, registro y recuperación,
propiedad de expedientes/reportes, compras/PayPal/webhook, certificados,
progreso/cursos, videos/S3, muro, interacciones, moderación, bajas, adjuntos y
portadas. Son afirmaciones sobre escenarios de tests, **no un porcentaje de
cobertura de líneas** ni aprobación de combinaciones de toda la matriz de roles.

Faltan especialmente: estructura real de medios, concurrencia y borrado diferido
S3, token/correo ante fallo de registro de aviso, límite con proxies, cargas
abandonadas, callbacks/reconciliación, varios reembolsos parciales, carga y E2E.

## Experiencia de usuario y áreas sin verificar

Se revisaron mensajes/estados de API y plantillas disponibles: errores de campo,
409 de carga pendiente, 503 de integración, estados públicos/privados y URLs
firmadas. QAS01/QAS02/QAS04 afectan imágenes que el usuario verá rotas; QAS03
afecta el mensaje de recuperación y PROD07 la percepción de éxito del pago.

**Sin verificar:** formularios reales, estados vacíos/loading del frontend,
accesibilidad por teclado/lector de pantalla, contraste, móvil y navegadores;
entrega y renderizado de correo en clientes reales; reproducción/seek de video;
PayPal/Resend/S3/Azure reales; red/proxy/IAM y dominios definitivos. No hubo
sesión de navegador ni se ejecutó un frontend que no está en este repositorio.

No hubo benchmark, prueba de estrés, pentest exhaustivo, escaneo del historial
de secretos, restauración, migración sobre copia de producción ni Linux CI.
Los callbacks demorados se controlan en TestCase; las devoluciones sí usan
conexiones PostgreSQL separadas. No se comparan esos tests con carga real.
Las pruebas existentes de smoke S3 usan mocks: su salida de éxito no demuestra
un PUT/GET real en esta ejecución. La política de sockets bloqueó destinos externos.

## Plan de corrección y criterios de liberación

1. **P0:** cerrar PROD06; exigir evidencia válida del cobro y ausencia de entrega
   en cada rechazo. Revisar conciliación de órdenes ya afectadas en un entorno
   autorizado, sin asumir que existen casos en producción.
2. **P1:** cerrar PROD07/08 y QAS04; probar idempotencia, callbacks, recuperación
   y coherencia BD/S3 bajo solicitudes fuera de orden.
3. **P1:** validar imágenes reales (QAS01/02), definir confianza de proxies
   (QAS06/08), recopilar/servir estáticos (QAS07) y resolver dependencias.
4. **P2:** unificar diagnóstico S3 (QAS03), reparar documentación (QAS09),
   completar cuotas/limpieza y priorizar paginación/carga según volumen.
5. **Aceptación:** suite base y nuevas regresiones en discovery aprobadas;
   ningún hallazgo alto abierto y decisiones explícitas para medios restantes;
   perfil definitivo con check --deploy y estáticos; avisos de dependencias
   evaluados; migración/backup/restauración ensayados; E2E y seguridad de proxy
   en staging, incluyendo roles, SMTP, PayPal, S3, persistencia y navegador.

El entorno aún no satisface estos criterios. Una suite verde no basta para
recomendar liberación: los escenarios adicionales reproducen defectos y el
entorno definitivo sigue sin verificarse.

## Archivos creados y reproducción

- Informe: `docs/QA_SENIOR_AUDIT_2026-10-04.md`.
- Pruebas aisladas: `tests/qa_senior_20261004_checks.py` (QAS01–QAS06).
- Se reutiliza, sin modificar, `tests/production_audit_followup_checks.py`.
- Salidas locales: `.qa_audit/senior_baseline_20261004.txt`,
  `.qa_audit/senior_final_repros_20261004.txt`,
  `.qa_audit/senior_config_20261004.json`. Probes auxiliares ignorados por Git.

```powershell
# Requiere el cluster QA aislado iniciado en 127.0.0.1:55439.
$env:QA_RUN='senior_reproduce'
.\.venv\Scripts\python.exe scripts/qa_audit_run.py test tests.qa_senior_20261004_checks tests.production_audit_followup_checks --noinput
```

Las reproducciones no usan expectedFailure: su salida 1 corresponde a defectos
abiertos. No deben confundirse con fallos de la suite habitual ni omitirse al
decidir la liberación. El clúster QA se detuvo al terminar.
