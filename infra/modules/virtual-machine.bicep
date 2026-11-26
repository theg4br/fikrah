targetScope = 'resourceGroup'

param location string
param tags object
param virtualMachineName string
param osDiskName string
param vmSize string
param osDiskSizeGB int
param adminUsername string
param adminSshPublicKey string
param networkInterfaceId string
param ubuntuImageVersion string

resource virtualMachine 'Microsoft.Compute/virtualMachines@2026-04-01' = {
  name: virtualMachineName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: ubuntuImageVersion
      }
      osDisk: {
        name: osDiskName
        osType: 'Linux'
        createOption: 'FromImage'
        deleteOption: 'Detach'
        diskSizeGB: osDiskSizeGB
        caching: 'ReadWrite'
        // Azure managed disks use encryption at rest with platform-managed keys.
        // No disk encryption set, Key Vault, or extra paid service is required.
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
    osProfile: {
      computerName: virtualMachineName
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: adminSshPublicKey
            }
          ]
        }
      }
      // No customData, application configuration, or secrets enter ARM history.
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: networkInterfaceId
          properties: {
            primary: true
            deleteOption: 'Detach'
          }
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
  }
}

output vmResourceId string = virtualMachine.id
output principalId string = virtualMachine.identity.principalId
