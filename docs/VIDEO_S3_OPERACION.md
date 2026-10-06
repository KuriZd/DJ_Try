# Operación de video

Antes de una migración, respaldar PostgreSQL y comprobar retención/versionado
del bucket. La aplicación y S3 guardan partes diferentes del mismo contenido.
Ensayar restauración en un entorno separado y comprobar referencias a objetos.

```sh
python manage.py migrate --check
python manage.py verificar_video_schema
python scripts/smoke_video_s3.py --help
```

El smoke real requiere credenciales, bucket y origen autorizados; puede crear
objetos. Leer sus opciones antes de ejecutarlo. Las pruebas unitarias de S3
usan mocks y no acreditan red, IAM, CORS ni reproducción real.

Para staging: cargar un archivo, confirmarlo, comprobar GET 206 y Content-Range,
reproducir y hacer seek desde el frontend. Revisar que usuario sin permisos
no pueda obtener una firma. Rotar credenciales y revisar errores en logs.

Para el despliegue completo ver [producción Azure](PRODUCCION_AZURE.md).
