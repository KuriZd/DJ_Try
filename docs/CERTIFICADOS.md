# Certificados

La API gestiona emisión, descarga PDF, envío e historial de certificados.
La implementación y sus pruebas están en `core/api/certificado_views.py`
y `tests/test_certificados.py`. Consultar Swagger para campos y contratos.

Listado y operaciones están bajo `/api/certificados/`. Los permisos limitan
el acceso al titular y al personal autorizado. La verificación por código es
pública y tiene límite de frecuencia; revisar los datos públicos como parte
de la aceptación de privacidad.

Las acciones incluyen `descargar`, `cancelar`, `revocar`, `enviar`, `historial`
y `envios`. Probar emisión, PDF, verificación pública y revocación en staging.
No confundir este módulo con el certificado de finalización de un curso.

Ver [guía completa](GUIA_COMPLETA.md) y [producción Azure](PRODUCCION_AZURE.md).
