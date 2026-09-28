targetScope = 'resourceGroup'

@description('Region for every resource. MySQL Flexible Server must be offered there.')
param location string = resourceGroup().location

@description('MySQL admin login. Use letters only.')
param mysqlAdminLogin string = 'voltadmin'

@secure()
@description('URL-safe MySQL password. Avoid @ : / # so the connection string stays valid.')
param mysqlAdminPassword string

@secure()
param jwtSecret string

param mysqlSkuName string = 'Standard_B1ms'
param mysqlSkuTier string = 'Burstable'
param mysqlStorageGb int = 32
param appSku string = 'B1'
param clientIp string = ''

var token = toLower(take(uniqueString(resourceGroup().id), 8))
var mysqlName = 'vg${token}mysql'
var vaultName = 'vg${token}kv'
var storageName = 'vg${token}st'
var planName = 'vg${token}-plan'
var appName = 'vg${token}-api'
var insightsName = 'vg${token}-ai'

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: {
    name: appSku
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

resource mysql 'Microsoft.DBforMySQL/flexibleServers@2023-12-30' = {
  name: mysqlName
  location: location
  sku: {
    name: mysqlSkuName
    tier: mysqlSkuTier
  }
  properties: {
    version: '8.0.21'
    administratorLogin: mysqlAdminLogin
    administratorLoginPassword: mysqlAdminPassword
    storage: {
      storageSizeGB: mysqlStorageGb
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
}

resource allowAzure 'Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30' = {
  parent: mysql
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource allowClient 'Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30' = if (clientIp != '') {
  parent: mysql
  name: 'AllowClient'
  properties: {
    startIpAddress: clientIp
    endIpAddress: clientIp
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource exportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'voltgrid-exports'
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
  }
}

var databaseUrl = 'mysql+pymysql://${mysqlAdminLogin}:${mysqlAdminPassword}@${mysql.properties.fullyQualifiedDomainName}:3306/voltgrid'

resource app 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appCommandLine: 'uvicorn app.main:app --host 0.0.0.0 --port 8000'
      appSettings: [
        { name: 'DATABASE_URL', value: databaseUrl }
        { name: 'JWT_SECRET', value: jwtSecret }
        { name: 'MYSQL_SSL', value: 'true' }
        { name: 'SEED_ON_STARTUP', value: 'true' }
        { name: 'ALLOW_METER_SIMULATION', value: 'false' }
        { name: 'RUN_RECONCILER', value: 'true' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        { name: 'AZURE_BLOB_CONTAINER', value: 'voltgrid-exports' }
        { name: 'AZURE_STORAGE_CONNECTION_STRING', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}' }
      ]
    }
  }
}

resource appKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, app.id, 'secrets-user')
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output webAppName string = app.name
output webAppUrl string = 'https://${app.properties.defaultHostName}'
output mysqlHost string = mysql.properties.fullyQualifiedDomainName
output keyVaultName string = vault.name
output storageAccount string = storage.name
