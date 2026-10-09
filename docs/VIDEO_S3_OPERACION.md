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
## Azure Blob

El backend admite `MEDIA_STORAGE_PROVIDER=s3` (por defecto) o `azure`.
Se conservan las claves existentes (`s3_key` e `imagen_clave`), sin migrar la
base de datos. Los PDFs privados del almacenamiento local quedan fuera de esta
configuración. La instalación local utiliza Azure para nuevas cargas, sin copiar
los archivos de S3. Las referencias anteriores permanecen en la BD, pero sus
objetos no están disponibles en el nuevo contenedor.

En desarrollo, configurar `.env.azure` (ignorado por Git):

```dotenv
MEDIA_STORAGE_PROVIDER=azure
AZURE_ACCOUNT_NAME=amisstorageb1
AZURE_ACCOUNT_KEY=<secreto-local>
AZURE_CONTAINER=amis-media
```

El contenedor debe existir y ser privado. Reiniciar el backend después de cambiar
la configuración. La cuenta local es `amisstorageb1`, contenedor `amis-media`.

Configurar CORS del servicio Blob con los orígenes exactos del frontend:

```powershell
python manage.py configurar_cors_azure --origin http://localhost:3000 --origin http://localhost:5173
```

El comando agrega una regla y conserva las reglas existentes. Agregar el origen
HTTPS real cuando se publique el frontend. El cliente debe enviar **todos** los
`headers` de la respuesta de upload, incluido `x-ms-blob-type: BlockBlob`.
La URL de subida SAS permite solo crear; no permite reemplazar un blob existente.
Azure no firma el Content-Type de subida como S3: se valida al confirmar la carga.

Probar carga, confirmación, imágenes, portadas y seek
de video en el frontend. Mantener S3 hasta aceptar la migración. Si se vuelve a
S3 después de nuevas cargas en Azure, primero copiar esas cargas al origen.
Producción solo lee variables del proceso/Key Vault, nunca `.env.azure`.
