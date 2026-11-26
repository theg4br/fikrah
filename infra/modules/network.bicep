targetScope = 'resourceGroup'

param location string
param tags object
param virtualNetworkName string
param networkSecurityGroupName string
param networkInterfaceName string
param publicIpName string
param subnetName string
param ipConfigurationName string
param adminSourceCidr string
param dnsLabel string

// Fail closed if someone bypasses the deployment prerequisite's /32 validation.
// Azure also validates the remaining IPv4 CIDR syntax. This never widens access.
var sshSourceCidr = endsWith(adminSourceCidr, '/32') ? adminSourceCidr : '127.0.0.1/32'

// Latest provider GA with an installed Bicep schema; newer 2026-03/05/07 are untyped.
resource networkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2026-01-01' = {
  name: networkSecurityGroupName
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowSshFromAdmin'
        properties: {
          priority: 100
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourceAddressPrefix: sshSourceCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'AllowPublicHttpHttps'
        properties: {
          priority: 110
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: ['80', '443']
        }
      }
      {
        name: 'DenyOtherInboundIncludingVnet'
        properties: {
          priority: 4096
          access: 'Deny'
          direction: 'Inbound'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2026-01-01' = {
  name: virtualNetworkName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.40.0.0/16']
    }
    subnets: [
      {
        name: subnetName
        properties: {
          addressPrefix: '10.40.0.0/24'
          defaultOutboundAccess: false
          serviceEndpoints: [
            {
              service: 'Microsoft.Storage'
              locations: [location]
            }
          ]
          networkSecurityGroup: {
            id: networkSecurityGroup.id
          }
        }
      }
    ]
  }
}

resource publicIp 'Microsoft.Network/publicIPAddresses@2026-01-01' = {
  name: publicIpName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    dnsSettings: {
      domainNameLabel: dnsLabel
    }
    idleTimeoutInMinutes: 4
  }
}

resource networkInterface 'Microsoft.Network/networkInterfaces@2026-01-01' = {
  name: networkInterfaceName
  location: location
  tags: tags
  properties: {
    enableIPForwarding: false
    enableAcceleratedNetworking: false
    ipConfigurations: [
      {
        name: ipConfigurationName
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          privateIPAddressVersion: 'IPv4'
          subnet: {
            id: virtualNetwork.properties.subnets[0].id
          }
          publicIPAddress: {
            id: publicIp.id
          }
        }
      }
    ]
  }
}

output networkInterfaceId string = networkInterface.id
output subnetId string = virtualNetwork.properties.subnets[0].id
output publicIPAddress string = publicIp.properties.ipAddress
output fqdn string = publicIp.properties.dnsSettings.fqdn
