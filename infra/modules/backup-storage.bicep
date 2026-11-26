targetScope = 'resourceGroup'

param location string
param tags object
@minLength(3)
@maxLength(24)
param storageAccountName string
param containerName string
param vmSubnetId string
param vmPrincipalId string

// GA APIs verified against provider metadata/docs and installed compiler schemas.
resource backupStorage 'Microsoft.Storage/storageAccounts@2026-06-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    allowCrossTenantReplication: false
    defaultToOAuthAuthentication: true
    // Service endpoints require the public endpoint enabled. Firewall admission
    // remains limited to this VM subnet, with no IP rules or trusted-service bypass.
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
      ipRules: []
      virtualNetworkRules: [
        {
          id: vmSubnetId
          action: 'Allow'
        }
      ]
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      services: {
        blob: {
          enabled: true
          keyType: 'Account'
        }
      }
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2026-06-01' = {
  parent: backupStorage
  name: 'default'
  properties: {
    // Backups use unique timestamped blob names, so versions are unnecessary.
    isVersioningEnabled: false
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource backupContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-06-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2026-06-01' = {
  parent: backupStorage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'ExpireCurrentBackupsAfter30Days'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              // Azure prefixes include the container. Blob names themselves
              // start with a timestamp/folder, without another backups/ prefix.
              prefixMatch: ['${containerName}/']
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: 30
                }
              }
            }
          }
        }
      ]
    }
  }
}

var blobContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource vmBackupWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backupContainer.id, vmPrincipalId, blobContributorRoleId)
  scope: backupContainer
  properties: {
    principalId: vmPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobContributorRoleId
  }
}

output storageAccountName string = backupStorage.name
output containerName string = backupContainer.name
output blobEndpoint string = backupStorage.properties.primaryEndpoints.blob
