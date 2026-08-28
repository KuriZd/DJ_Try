# Pendientes de datos y certificados

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
