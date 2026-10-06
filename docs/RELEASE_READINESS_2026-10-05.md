# Preparación de primera versión — 5 de octubre de 2026

Estado: backend preparado para validar en staging Azure. La liberación a
producción aún depende de comprobar infraestructura e integraciones reales.
No se ha desplegado, publicado, creado recursos de pago ni modificado una BD
de desarrollo/producción. Las pruebas usan un PostgreSQL de QA aislado.

## Cambios

| Hallazgo | Resultado |
|---|---|
| PROD06 | Una captura sin importe/moneda o con importe no finito se rechaza; no entrega la compra |
| PROD07 | Fallos de comprobante, incluido registro BD, se aíslan y se registran en logs sin propagar al pago confirmado |
| PROD08 | Se repite la comprobación de refund bajo bloqueo de orden; entregas concurrentes del mismo ID son idempotentes |
| QAS01/02 | Imágenes exigen estructura y píxeles decodificables, límite de bytes/píxeles y formato correcto; fixtures usan imágenes reales |
| QAS04 | Limpieza de portada comparte bloqueo con confirmación y preserva la clave actualmente activa |
| QAS06 | Por defecto DRF ignora X-Forwarded-For del cliente; proxies configurables explícitamente |
| QAS07/08 | STATIC_ROOT y perfil de estáticos WhiteNoise; reconocimiento HTTPS de proxy configurable |
| QAS09 | Documentos ausentes restaurados y enlaces README comprobados |
| Dependencias | 47 versiones exactas; auditoría del día sin avisos conocidos; pip check correcto |

Se agregaron perfil `config.production`, configuración Gunicorn, CI Linux con
PostgreSQL y guía de variables/aceptación para Azure. El perfil exige secretos,
hosts y orígenes explícitos, HTTPS y TLS PostgreSQL; ignora los archivos .env.
Las reproducciones corregidas ahora participan en discovery habitual.

## Límites y decisiones pendientes

- QAS03: un 403 de portada sigue siendo 503. Sin política IAM verificada no
  se puede distinguir ausencia de objeto de permisos insuficientes. Se añadió
  regresión de ese contrato; no se afirma cerrado el diagnóstico de carga pendiente.
- El fallo al crear un registro de comprobante deja log, pero necesita
  conciliación operativa: `reintentar_correos` solo recupera filas existentes.
- Se conserva configuración explícita de HSTS subdominios/preload; cero warnings
  se verificó con ambos habilitados en un perfil sintético, no con dominios reales.
- El borrado seguro mantiene bloqueo mientras consulta S3; medir duración y
  capacidad en staging. MP4 mantiene comprobación de firma, sin decodificación completa.
- WhiteNoise comprime sin manifest porque ReDoc incluye un sourcemap ausente;
  los estáticos no reciben nombres con hash de contenido en esta versión.
- CI fue creado, pero su ejecución en GitHub/Linux aún no está acreditada.
- Faltan navegador/frontend real, SMTP, PayPal, S3/IAM/CORS, red/proxy Azure,
  persistencia de PDFs, backup/restauración y pruebas con volumen representativo.
- Acordar reembolsos parciales, retención y acceso a documentos, cuotas y
  limpieza de cargas abandonadas. Revisar los datos demo sembrados por migraciones.

Los informes históricos de auditoría se pueden consultar en el historial de Git;
este documento resume el estado posterior a las correcciones.
La reproducción QAS03 original mantiene su expectativa anterior (409) y no
es el contrato adoptado para un 403 sin contexto IAM.

Procedimiento de aceptación: [producción Azure](PRODUCCION_AZURE.md).

## Verificación final local

- Suite completa: **467 pruebas aprobadas**, 90.859 segundos, después de corregir
  el build de estáticos de ReDoc.
- `check --deploy --fail-level WARNING`: aprobado con perfil sintético completo.
- `collectstatic` y GET de CSS admin con DEBUG=False: aprobados en prueba aislada.
- `makemigrations --check --dry-run`: sin cambios.
- `pip check`: sin dependencias rotas. `pip-audit`: 47 paquetes, cero avisos conocidos.
- Después de cambios concurrentes de marca AISER, se verificaron otras 53
  pruebas de publicaciones/bajas y correo: aprobadas, salida 0.
- Plantilla de infraestructura preparada en [infra/azure](../infra/azure/README.md).
  Compilación Bicep aprobada. Falta validar con Azure CLI y ejecutar what-if
  en una suscripción; el equipo no dispone de Azure CLI. No acredita infraestructura ya creada.
