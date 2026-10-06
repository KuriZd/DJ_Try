# Primera versión en Azure

Destino propuesto: Azure App Service **Linux**, Python 3.12, PostgreSQL Flexible
Server y almacenamiento persistente para PDFs privados. S3 permanece como
almacenamiento de videos y fotos. Falta confirmar los recursos y dominios reales.

Como aún no existen recursos, se preparó una [plantilla Bicep de staging](../infra/azure/README.md)
con red y PostgreSQL privados. Los tamaños quedan como parámetros para revisar costos.

## Configuración

Usar variables de App Service o referencias a Key Vault. El perfil
`config.production` no lee `.env` ni `.env.paypal`. Separar bases, secretos,
bucket/prefijos y correo de staging y producción. No subir archivos locales.

| Variable | Valor o propósito |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.production` |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_SECRET_KEY` | Clave aleatoria exclusiva, de al menos 50 caracteres |
| `ALLOWED_HOSTS` | Host Azure y dominio API, separados por coma, sin `*` |
| `CORS_ALLOWED_ORIGINS` | Origen HTTPS exacto del frontend |
| `CSRF_TRUSTED_ORIGINS` | Orígenes HTTPS que utilizan sesión/admin, si corresponde |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | Conexión al servidor dedicado |
| `POSTGRES_SSLMODE` | `verify-full` por defecto; requiere CA de confianza |
| `POSTGRES_SSLROOTCERT` | Ruta al archivo de CA mantenido según Azure |
| `PRIVATE_MEDIA_ROOT` | `/home/djtry/private-media` o montaje persistente equivalente |
| `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME` | Bucket privado y región real |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Si se usan credenciales AWS; guardar en Key Vault, limitar IAM y rotar |
| `FRONTEND_BASE_URL` | URL HTTPS del frontend |
| `PAYPAL_MODE` | `sandbox` en staging, `live` al habilitar cobros reales |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` | Credenciales y webhook del mismo ambiente |
| `PAYPAL_RETURN_URL`, `PAYPAL_CANCEL_URL` | URLs HTTPS del frontend |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Proveedor SMTP real |
| `DEFAULT_FROM_EMAIL` | Remitente verificado |
| `EMAIL_REDIRIGIR_A` | Vacío en este perfil; probar correos con cuentas de staging |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `1` para instalar requirements y recopilar estáticos con Oryx |
| `WEB_CONCURRENCY` | `2` inicial, ajustar con pruebas y conexiones disponibles |

El cache predeterminado usa PostgreSQL y la migración 0036 crea su tabla.
`REDIS_URL` habilita un Redis compartido, con paquete incluido. No publicar Redis.

`DJANGO_TRUST_PROXY_HTTPS=true` reconoce `X-Forwarded-Proto` **solo** cuando la
infraestructura impide acceso directo y sobrescribe esta cabecera. Verificar
ese contrato antes de activarlo. `DJANGO_NUM_PROXIES=0` ignora por defecto
`X-Forwarded-For`; definir un número mayor solo tras comprobar la cadena real.
Con cero, clientes detrás de un proxy pueden compartir límite: probarlo en staging.
Gunicorn también limita confianza con `GUNICORN_FORWARDED_ALLOW_IPS`; evitar
`*` sin una frontera de red verificada. Activar HTTPS Only en Azure.

`check --deploy` puede mostrar W005/W021 hasta aprobar HSTS para todos los
subdominios y preload. No activarlos sin confirmar ese alcance. Para exigir
cero advertencias, configurar `SECURE_HSTS_INCLUDE_SUBDOMAINS=true` y
`SECURE_HSTS_PRELOAD=true` después de verificar todos los subdominios;
de lo contrario registrar explícitamente la excepción antes de liberar.

## Build y arranque

Configurar el comando de inicio en App Service:

```sh
gunicorn config.wsgi:application -c gunicorn.conf.py
```

Oryx instala las versiones exactas de `requirements.txt` y ejecuta collectstatic.
WhiteNoise sirve los estáticos; los PDFs privados no tienen ruta pública.
Los archivos creados en el directorio temporal de Oryx no son persistentes:
usar `/home` o un montaje y ensayar persistencia tras reinicio y despliegue.
Gunicorn se valida en Linux; no arranca nativamente en este entorno Windows.

## Liberación en staging

1. Ejecutar CI: pruebas PostgreSQL, migraciones, estáticos y auditoría de dependencias.
2. Respaldar BD y PDFs; probar restauración en una base separada. En un servidor
   nuevo, aplicar migraciones una vez desde una tarea controlada, sin varios
   workers ejecutándolas al arrancar.
3. Con las variables del destino, ejecutar:

```sh
python manage.py check --deploy --fail-level WARNING
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py verificar_video_schema
```

4. Crear administrador mediante `crear_administrador --help`; no reactivar claves
   demo. Revisar el contenido sembrado por las migraciones antes de abrir el sitio:
   datos de ejemplo no son contenido comercial aprobado.
5. Probar `/api/health/`, redirección HTTP, HTTPS sin bucle, rechazo de Host ajeno,
   estáticos de admin y comportamiento de límites con IPs reales/falsificadas.
6. Ejecutar flujos desde el frontend: registro/verificación, recuperación,
   permisos por rol, curso/video con seek, expediente privado, pago sandbox,
   captura repetida, webhook firmado, reembolso y comprobante SMTP.
7. Confirmar persistencia y restauración de PDFs, CORS S3 y política IAM. Para
   distinguir objeto ausente de AccessDenied, permitir el ListBucket mínimo
   correspondiente o adoptar una política explícita; un 403 de portada sigue
   siendo 503 porque no prueba ausencia.
8. Revisar logs/alertas de 5xx, errores PayPal, comprobantes omitidos y almacenamiento.
   Programar `reintentar_correos --simular` y después ejecución única controlada
   cada pocos minutos. Un fallo al crear el registro de correo requiere conciliación
   por referencia de pago; el comando de reintentos solo encuentra filas existentes.

## Pendientes de aceptación

No liberar hasta completar staging y acordar: reembolsos parciales y acceso a
servicios; limpieza de cargas S3 abandonadas; cuota de almacenamiento; volumen
y latencia esperados; retención de documentos y URLs firmadas. No aplicar una
regla S3 que borre indiscriminadamente portadas activas por antigüedad.

Conservar el artefacto/commit previo y respaldo. Ante falla, detener tráfico y
volver al artefacto anterior si es compatible con el esquema. No revertir
migraciones o restaurar BD sin evaluar pagos y escrituras posteriores.

Referencias: [App Service Python](https://learn.microsoft.com/en-us/azure/app-service/configure-language-python),
[TLS PostgreSQL Azure](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls),
[checklist Django](https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/).
