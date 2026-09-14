# Datos y certificados

El trabajo listado abajo está implementado en `0028_certificados_api` y en el
módulo de certificados. Contrato: [CERTIFICADOS.md](docs/CERTIFICADOS.md).
Se conserva `aspirantes.folio_aplicacion` por compatibilidad. La emisión de
reclutamiento es manual y exige justificación; la automática requiere definir
reglas de aprobación. Falta integrar y comprobar la pantalla del frontend y
revisar los textos institucionales con su responsable.

## Alcance implementado

## Folio por postulación

- Agregar un folio único a cada registro de `postulaciones`.
- Migrar o conservar `aspirantes.folio_aplicacion` según compatibilidad requerida.
- Exponer el nuevo folio en la API de postulaciones y en el certificado.

## Snapshot del certificado

- Definir la estructura obligatoria de `certificados.aspirante_snapshot`.
- Validar antes de emitir que incluya datos generales, académicos, laborales y
  competencias evaluadas.
- Guardar una copia inmutable de los datos usados al emitir el certificado.
- Versionar el formato del snapshot para permitir cambios futuros.

## Generación y presentación

- Confirmar que la plantilla muestre todos los campos requeridos.
- Definir cómo se representan campos opcionales o sin información.
- Agregar pruebas que verifiquen el contenido generado del certificado.
