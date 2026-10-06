# Infraestructura inicial de staging

`main.bicep` declara App Service Linux, PostgreSQL 16, base vacía, VNet con
subredes delegadas y DNS privado. PostgreSQL no tiene firewall público.
Hay backups de 14 días, sin HA ni redundancia geográfica en esta plantilla de
staging. La identidad administrada permite configurar Key Vault después.

No se ha creado ningún recurso ni validado el despliegue en una suscripción.
La plantilla compila con el compilador oficial Bicep descargado para QA;
CI también incluye compilación. Azure CLI no está instalado en el equipo.
Confirmar región, nombres, disponibilidad de Python 3.12 y SKUs/costo antes
de ejecutar. No reutilizar sin revisar CIDR si ya hay una VNet en la organización.

Parámetros requeridos: `prefix`, `appServiceSku`, `postgresSku`, `postgresTier`,
`postgresAdmin`, `postgresPassword`, `djangoSecretKey`, `frontendOrigin` y
`integrationSettings`. Las SKUs no tienen default para exigir una elección
explícita. El grupo de recursos se selecciona fuera de la plantilla.

`integrationSettings` debe contener AWS bucket/región/credenciales si se usan,
SMTP y remitente, PayPal sandbox completo, y el certificado CA PostgreSQL.
Preferir referencias Key Vault de App Service para secretos de integración;
la contraseña inicial del servidor sigue siendo un parámetro seguro de ARM.
Mantener un archivo de parámetros con secretos fuera del repositorio.

Con Azure CLI instalado y autenticado, primero compilar y revisar:

```powershell
az bicep build --file infra/azure/main.bicep --stdout
az deployment group validate --resource-group GRUPO_STAGING --template-file infra/azure/main.bicep --parameters '@RUTA_PRIVADA_DE_PARAMETROS.json'
az deployment group what-if --resource-group GRUPO_STAGING --template-file infra/azure/main.bicep --parameters '@RUTA_PRIVADA_DE_PARAMETROS.json'
```

Revisar el resultado y costos antes del comando de creación:

```powershell
az deployment group create --resource-group GRUPO_STAGING --template-file infra/azure/main.bicep --parameters '@RUTA_PRIVADA_DE_PARAMETROS.json'
```

La plantilla no carga código, migra datos ni despliega frontend. El perfil Django
rechaza configuración incompleta. `integrationSettings` puede sobrescribir la
configuración base: revisar sus valores antes de desplegar. El proxy HTTPS se
mantiene desactivado hasta comprobar la frontera de confianza, y debe quedar
correctamente configurado antes de habilitar tráfico.

Los PDFs quedan en `/home`: verificar persistencia y backup separado de esa
ruta. La restauración PostgreSQL no recupera PDFs ni objetos S3. Ejecutar las
migraciones desde App Service o una máquina con acceso privado a la VNet; el
equipo local no accede directamente al PostgreSQL privado.

Para producción definir posteriormente dominio, CA, alertas, retención y
restauración probada, además del tamaño y disponibilidad requeridos. No cambiar
automáticamente PayPal a live. Ver [guía del backend](../../docs/PRODUCCION_AZURE.md).

Referencias: [App Service y VNet](https://learn.microsoft.com/en-us/azure/app-service/overview-vnet-integration),
[PostgreSQL privado](https://learn.microsoft.com/en-us/azure/postgresql/network/concepts-networking-private).
