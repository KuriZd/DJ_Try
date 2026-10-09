# Video privado en S3

La API registra el video, firma su carga y entrega una URL temporal de lectura.
El bucket debe permanecer privado. Configurar `AWS_STORAGE_BUCKET_NAME` y
`AWS_S3_REGION_NAME`; credenciales AWS mediante entorno/SDK con permisos mínimos.

- `POST /api/videos/upload/`: solicita carga directa; ver contrato en Swagger.
- PUT a la URL firmada: el navegador envía el archivo a S3.
- `POST /api/videos/{id}/confirm/`: comprueba el objeto cargado.
- `GET /api/videos/{id}/playback/`: solicita reproducción según permisos.

La firma PUT dura 600 segundos y la firma de reproducción 3600. La firma
temporal no sustituye los permisos del endpoint: una URL ya emitida continúa
vigente hasta su expiración. Configurar CORS del bucket para el origen real
y probar reproducción, rangos y seek desde navegador.

Ver [operación](VIDEO_S3_OPERACION.md) y [guía general](GUIA_COMPLETA.md).
