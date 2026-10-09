targetScope = 'resourceGroup'

@description('Prefijo corto en minúsculas, por ejemplo djtry-stg. Nombres deben estar disponibles.')
@minLength(3)
@maxLength(25)
param prefix string
param location string = resourceGroup().location
@description('SKU App Service aprobada, con integración VNet (Basic o superior).')
param appServiceSku string
@description('SKU PostgreSQL aprobada y disponible en la región.')
param postgresSku string
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresTier string
param postgresAdmin string
@secure()
param postgresPassword string
@secure()
param djangoSecretKey string
@description('Origen HTTPS del frontend, sin ruta ni slash final.')
param frontendOrigin string
@description('Secretos/variables de SMTP, PayPal y AWS. No guardar valores en Git.')
@secure()
param integrationSettings object
@minValue(7)
@maxValue(35)
param backupRetentionDays int = 14

var webName = '${prefix}-api'
var databaseName = 'dj_try'
var appSubnetId = resourceId('Microsoft.Network/virtualNetworks/subnets', '${prefix}-vnet', 'app')
var dbSubnetId = resourceId('Microsoft.Network/virtualNetworks/subnets', '${prefix}-vnet', 'postgres')
var baseSettings = {
  DJANGO_SETTINGS_MODULE: 'config.production'
  DJANGO_DEBUG: 'false'
  DJANGO_SECRET_KEY: djangoSecretKey
  ALLOWED_HOSTS: '${webName}.azurewebsites.net'
  CORS_ALLOWED_ORIGINS: frontendOrigin
  CSRF_TRUSTED_ORIGINS: frontendOrigin
  FRONTEND_BASE_URL: frontendOrigin
  POSTGRES_DB: databaseName
  POSTGRES_USER: postgresAdmin
  POSTGRES_PASSWORD: postgresPassword
  POSTGRES_HOST: postgres.properties.fullyQualifiedDomainName
  POSTGRES_PORT: '5432'
  POSTGRES_SSLMODE: 'verify-full'
  PRIVATE_MEDIA_ROOT: '/home/djtry/private-media'
  SCM_DO_BUILD_DURING_DEPLOYMENT: '1'
  EMAIL_REDIRIGIR_A: ''
  PAYPAL_MODE: 'sandbox'
  PAYPAL_RETURN_URL: '${frontendOrigin}/pagos/paypal/return'
  PAYPAL_CANCEL_URL: '${frontendOrigin}/pagos/paypal/cancel'
  // Revisar fronteras de confianza en staging antes de activar proxy HTTPS.
  DJANGO_TRUST_PROXY_HTTPS: 'false'
  DJANGO_NUM_PROXIES: '0'
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${prefix}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.42.0.0/16'] }
    subnets: [
      {
        name: 'app'
        properties: {
          addressPrefix: '10.42.1.0/24'
          delegations: [{ name: 'appservice', properties: { serviceName: 'Microsoft.Web/serverFarms' } }]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.42.2.0/24'
          delegations: [{ name: 'postgres', properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' } }]
        }
      }
    ]
  }
}

resource dns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: '${prefix}.postgres.database.azure.com'
  location: 'global'
}
resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: dns
  name: '${prefix}-link'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: vnet.id } }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: '${prefix}-pg'
  location: location
  sku: { name: postgresSku, tier: postgresTier }
  properties: {
    version: '16'
    administratorLogin: postgresAdmin
    administratorLoginPassword: postgresPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: backupRetentionDays, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: {
      delegatedSubnetResourceId: dbSubnetId
      privateDnsZoneArmResourceId: dns.id
    }
  }
  dependsOn: [dnsLink]
}
resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: databaseName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-plan'
  location: location
  kind: 'linux'
  sku: { name: appServiceSku }
  properties: { reserved: true }
}
resource app 'Microsoft.Web/sites@2023-12-01' = {
  name: webName
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: appSubnetId
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn config.wsgi:application -c gunicorn.conf.py'
      alwaysOn: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
  }
  dependsOn: [vnet, database]
}
resource appSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: app
  name: 'appsettings'
  properties: union(baseSettings, integrationSettings)
}

output apiUrl string = 'https://${app.properties.defaultHostName}'
output webAppName string = app.name
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output managedIdentityPrincipalId string = app.identity.principalId
