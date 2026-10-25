/*
 * main.bicep — Warden in Azure (step 25), and the lock (step 20). Bicep = Azure's own
 * infrastructure-as-code language (like a Terraform file); `az deployment group create` makes the
 * resources match it, again and again (only differences change). Run it through infra/deploy.sh.
 *
 *   Log Analytics          where the containers' logs go
 *   identity               a managed identity: how Warden's container proves itself to Key Vault
 *                          (no password anywhere)
 *   Key Vault              the secrets: Warden's signing key, the database password + connection string
 *   PostgreSQL             Flexible Server B1ms (the 12-month free size), TLS required
 *   Container Apps env     the place the containers run, with one shared internal network
 *   warden                 PUBLIC https address; before each new version starts, an init container runs
 *                          `migrate` (step 24). ForwardedHeaders_Enabled: its addresses say https (step 23)
 *   notes                  the demo tool server (examples/notes-server): INTERNAL ingress only, no public
 *                          address at all, so only apps in this environment (Warden) can reach it (step 20)
 *
 * Images come from GitHub Container Registry (free), pushed by .github/workflows/warden-deploy.yml.
 * ponytail: Postgres takes connections from Azure services (firewall 0.0.0.0) with TLS + a long random
 * password; a private network (VNet + private DNS) closes it to everything but this environment.
 * ponytail: scale 0..1 replicas: free-grant friendly, a few seconds' cold start; more replicas need
 * `migrate` moved out of the init container into a one-off job (two at once would race).
 */

@description('Short name used in every resource name.')
param prefix string = 'warden'
param location string = resourceGroup().location

@description('Your Entra tenant (Directory) id and Warden\'s app registration (Application) id.')
param entraTenantId string
param entraClientId string

@description('e.g. ghcr.io/<you>/warden:<git sha>')
param wardenImage string
param notesImage string

@description('Object id of whoever runs the deploy (you, or the GitHub Actions service principal): may read/write secrets.')
param deployerObjectId string
@allowed(['User', 'ServicePrincipal'])
param deployerType string = 'User'

@secure()
param postgresPassword string
@secure()
@description('Warden\'s RSA private key, PEM, base64-encoded (deploy.sh makes it once and keeps it in Key Vault).')
param signingKeyPemBase64 string

var suffix = uniqueString(resourceGroup().id)
var pgName = '${prefix}-pg-${suffix}'
var kvName = take('${prefix}-kv-${suffix}', 24)
var wardenDb = 'Host=${pgName}.postgres.database.azure.com;Database=warden;Username=warden;Password=${postgresPassword};SSL Mode=VerifyFull'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-id'
  location: location
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
  }
}

// Key Vault Secrets User for Warden's identity (read), Secrets Officer for the deployer (read + write).
resource appReads 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, identity.id, 'secrets-user')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource deployerManages 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, deployerObjectId, 'secrets-officer')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
    principalId: deployerObjectId
    principalType: deployerType
  }
}

resource pgPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'postgres-password'
  properties: { value: postgresPassword }
}
resource dbSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'warden-db'
  properties: { value: wardenDb }
}
resource signingSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'signing-key-pem'
  properties: { value: base64ToString(signingKeyPemBase64) }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: pgName
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: 'warden'
    administratorLoginPassword: postgresPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}
resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: 'warden'
}
resource pgFromAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

var wardenUrl = 'https://${prefix}.${env.properties.defaultDomain}'
var notesUrl = 'https://notes.internal.${env.properties.defaultDomain}/mcp'

resource warden 'Microsoft.App/containerApps@2024-03-01' = {
  name: prefix
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  dependsOn: [appReads, pgDb, pgFromAzure]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8080, transport: 'auto', allowInsecure: false }
      secrets: [
        { name: 'warden-db', keyVaultUrl: dbSecret.properties.secretUri, identity: identity.id }
        { name: 'signing-key-pem', keyVaultUrl: signingSecret.properties.secretUri, identity: identity.id }
      ]
    }
    template: {
      initContainers: [
        {
          name: 'migrate'
          image: wardenImage
          command: ['dotnet', 'Warden.Api.dll', 'migrate']
          env: [
            { name: 'ConnectionStrings__Warden', secretRef: 'warden-db' }
            { name: 'Signing__PrivateKeyPem', secretRef: 'signing-key-pem' }
          ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      containers: [
        {
          name: 'warden'
          image: wardenImage
          env: [
            { name: 'ASPNETCORE_ENVIRONMENT', value: 'Production' }
            { name: 'ForwardedHeaders_Enabled', value: 'true' }
            { name: 'ConnectionStrings__Warden', secretRef: 'warden-db' }
            { name: 'Signing__PrivateKeyPem', secretRef: 'signing-key-pem' }
            { name: 'Entra__TenantId', value: entraTenantId }
            { name: 'Entra__ClientId', value: entraClientId }
          ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: [
            { type: 'Readiness', httpGet: { path: '/health', port: 8080 } }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

resource notes 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'notes'
  location: location
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: false, targetPort: 8100, transport: 'auto', allowInsecure: false }  // the lock
    }
    template: {
      containers: [
        {
          name: 'notes'
          image: notesImage
          env: [
            { name: 'WARDEN_URL', value: wardenUrl }
            { name: 'AUDIENCE', value: 'firm-a-notes' }
            { name: 'PUBLIC_URL', value: notesUrl }
          ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

output wardenUrl string = wardenUrl
output adminPage string = '${wardenUrl}/admin/'
output notesInternalUrl string = notesUrl
output keyVault string = vault.name
