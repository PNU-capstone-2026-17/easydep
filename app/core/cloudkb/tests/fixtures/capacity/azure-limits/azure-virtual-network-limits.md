---
 title: include file
 description: include file
 services: networking
 ms.topic: include
 ms.date: 10/16/2025
---
### <a name="azure-resource-manager-virtual-networking-limits"></a>Networking limits - Azure Resource Manager
The following limits apply only for networking resources managed through **Azure Resource Manager** per region per subscription. Learn how to [view your current resource usage against your subscription limits](../articles/networking/check-usage-against-limits.md).

> [!NOTE]
> We have increased all default limits to their maximum limits.

| Resource | Limit | 
| --- | --- |
| Virtual networks |1,000 |
| Subnets per virtual network |3,000 |
| Network interface cards |65,536 |
| Private IP addresses per virtual machine |256 * N (N is number of NICs on VM) |
| [Concurrent TCP or UDP flows per NIC of a virtual machine or role instance](../articles/virtual-network/virtual-machine-network-throughput.md#flow-limits-and-active-connections-recommendations) |500,000, up to 1,000,000 for two or more NICs. |
| Network Security Groups |5,000 |
| NSG rules per NSG |1,000 |

### <a name="public-ip-address-limits"></a>Public IP address limits

| Resource | Default limit | Maximum limit |
| --- | --- | --- |
| Public IP addresses<sup>1,2</sup> | 10 | Contact support |
| Public IP prefixes | limited by number of Standard Public IPs in a subscription | Contact support |
| Public IP prefix length | /28 | Contact support |

<sup>1</sup>Default limits for Public IPv4 addresses vary by offer category type.
