targetScope = 'subscription'

@minLength(1)
param environmentName string

@allowed(['austriaeast'])
param location string

param sessionId string
param deployedBy string
param createdAt string
param projectName string

// Resource names are explicit reviewed inputs; no generated suffixes.
param resourceGroupName string
param virtualMachineName string
param osDiskName string
param virtualNetworkName string
param networkSecurityGroupName string
param networkInterfaceName string
param publicIpName string
param subnetName string
param ipConfigurationName string
param backupStorageAccountName string
param backupContainerName string

@allowed(['Standard_B2als_v2'])
param vmSize string

@allowed([64])
param osDiskSizeGB int

@allowed(['fikrahadmin'])
param adminUsername string

@minLength(32)
@description('SSH public key only. Never supply private key material.')
param adminSshPublicKey string

@minLength(10)
@maxLength(18)
@description('Required single caller public IPv4 address in /32 notation. Validate before deployment. Broader prefixes cannot enable SSH.')
param adminSourceCidr string

@minLength(3)
@maxLength(63)
@description('Explicit globally available Azure public IP DNS label; lowercase letters, digits, and hyphens.')
param dnsLabel string

@allowed(['24.04.202611170'])
@description('Exact Canonical Ubuntu 24.04 x64 Gen2 image verified in Austria East at scaffold time. Updating requires review.')
param ubuntuImageVersion string

var tags = {
  'app-onboard-skill': 'true'
  'app-onboard-session-id': sessionId
  'session-id': sessionId
  'created-at': createdAt
  environment: environmentName
  project: projectName
  'deployed-by': deployedBy
}

// GA versions are provider-verified and covered by installed Bicep type schemas.
resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module network './modules/network.bicep' = {
  name: 'fikrah-network'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    virtualNetworkName: virtualNetworkName
    networkSecurityGroupName: networkSecurityGroupName
    networkInterfaceName: networkInterfaceName
    publicIpName: publicIpName
    subnetName: subnetName
    ipConfigurationName: ipConfigurationName
    adminSourceCidr: adminSourceCidr
    dnsLabel: dnsLabel
  }
}

module virtualMachine './modules/virtual-machine.bicep' = {
  name: 'fikrah-virtual-machine'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    virtualMachineName: virtualMachineName
    osDiskName: osDiskName
    vmSize: vmSize
    osDiskSizeGB: osDiskSizeGB
    adminUsername: adminUsername
    adminSshPublicKey: adminSshPublicKey
    networkInterfaceId: network.outputs.networkInterfaceId
    ubuntuImageVersion: ubuntuImageVersion
  }
}

module backupStorage './modules/backup-storage.bicep' = {
  name: 'fikrah-backup-storage'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    storageAccountName: backupStorageAccountName
    containerName: backupContainerName
    vmSubnetId: network.outputs.subnetId
    vmPrincipalId: virtualMachine.outputs.principalId
  }
}

output publicIPAddress string = network.outputs.publicIPAddress
output fqdn string = network.outputs.fqdn
output vmResourceId string = virtualMachine.outputs.vmResourceId
// Nonsecret metadata for the separately reviewed SSH bootstrap configuration.
output backupStorageAccountName string = backupStorage.outputs.storageAccountName
output backupContainerName string = backupStorage.outputs.containerName
output backupBlobEndpoint string = backupStorage.outputs.blobEndpoint
