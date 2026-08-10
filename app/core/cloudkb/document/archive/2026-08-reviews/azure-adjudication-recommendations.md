# Azure 판정 권고안

이 문서는 권고 목적으로만 사용한다. 이 매핑은 `azure-consensus.json`의 충돌 14건을 다룬다. `discovery-protocol.json`에서 한정한 Docker-on-VM 범위를 커밋 `478f542f0e4a8872a8c6e5cde5dd4e44a01bc120`에 고정된 Azure REST API Specifications에 적용했다. 어떤 요소를 포함하라는 권고만으로 관계 edge가 성립하는 것은 아니다.

## 1. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachineScaleSets/{vmScaleSetName}`

- 권고: **first**.
- 근거: 고정된 operation은 VM scale set을 생성하거나 업데이트한다고 설명된 `VirtualMachineScaleSets_CreateOrUpdate`이다. 생성이 더 구체적인 지배 결과이므로, 두 번째 판정의 더 포괄적인 수명 주기 레이블보다 `provisioningOutcome`을 뒷받침하는 근거가 강하다.
- 불확실성: 낮음.
- 관계 증거 공백: inventory에는 이 subject의 schema-reference 후보가 있지만, 충돌 항목의 path locator만으로는 포함된 다른 요소로 이어지는 확정 edge를 입증할 수 없다.

## 2. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachineScaleSets/{vmScaleSetName}/extensions/{vmssExtensionName}`

- 권고: **first**.
- 근거: 고정된 operation은 VM scale-set extension을 생성하거나 업데이트하며, body는 `VirtualMachineScaleSetExtension`이다. 이처럼 연결된 extension은 VM 프로비저닝/구성을 직접 변경하므로 명시된 프로비저닝 경계 안에 있다.
- 불확실성: 중간. extension이 Docker와 무관한 작업을 수행할 수도 있지만, 이 native 요소는 관찰 전용 서비스가 아니라 연결된 프로비저닝 메커니즘이다.
- 관계 증거 공백: inventory는 이 subject에서 나온 후보를 제시하지만 parent scale set으로 이어지는 typed edge를 확정하지 않는다. 요소를 포함한다고 해서 해당 edge를 만들어 내서는 안 된다.

## 3. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachineScaleSets/{vmScaleSetName}/virtualMachines/{instanceId}`

- 권고: **second**.
- 근거: `VirtualMachineScaleSetVMs_Update`는 VM-scale-set virtual machine을 생성하는 것이 아니라 업데이트한다고 명시되어 있다. `lifecycleOutcome`이 `provisioningOutcome`보다 고정된 operation에 더 정확히 부합한다.
- 불확실성: 낮음.
- 관계 증거 공백: 계층형 path는 포함 관계를 강하게 시사하지만 inventory 후보는 parent scale-set edge를 확정하지 않는다. 해당 관계에는 여전히 별도의 증거가 필요하다.

## 4. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachineScaleSets/{vmScaleSetName}/virtualMachines/{instanceId}/extensions/{vmExtensionName}`

- 권고: **first**.
- 근거: 고정된 `VirtualMachineScaleSetVMExtensions_CreateOrUpdate` operation은 특정 scale-set VM instance에 연결된 extension을 생성하거나 업데이트한다. 이는 범위 안에서 VM 구성/프로비저닝을 직접 제어한다.
- 불확실성: 중간. scale-set 수준 extension과 마찬가지로 실제 workload에 따라 달라지기 때문이다.
- 관계 증거 공백: extension을 instance에 연결하는 확정 후보 edge가 없다. 이 판정만으로 중첩된 ARM path가 관계 증거로 전환되지는 않는다.

## 5. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachineScaleSets/{vmScaleSetName}/virtualMachines/{instanceId}/runCommands/{runCommandName}`

- 권고: **first**.
- 근거: `VirtualMachineScaleSetVMRunCommands_CreateOrUpdate`는 특정 VM instance에 연결된 run-command 리소스를 영속화한다. Docker host 프로비저닝에 필요한 VM 구성을 직접 수행할 수 있으므로, VM 연결이 없다는 이유로 제외하는 것은 고정된 중첩 operation과 모순된다.
- 불확실성: 중간. 임의의 명령은 연구 workload 범위 밖일 수 있지만 이 제어면 요소는 VM에 직접 연결된다.
- 관계 증거 공백: 이 run-command 리소스와 해당 VM instance의 관계를 확정하는 inventory 후보가 없다. 요소 판정만 유지한다.

## 6. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachines/{vmName}`

- 권고: **first**.
- 근거: `VirtualMachines_CreateOrUpdate`는 virtual machine을 생성하거나 업데이트한다고 명시하며 생성 시에만 적용되는 property도 언급한다. `provisioningOutcome`이 연구 경계의 핵심 operation에 가장 가까운 criterion이다.
- 불확실성: 낮음.
- 관계 증거 공백: VM에서 시작하는 schema-reference 후보가 많지만 target type이 자동으로 확정되는 것은 아니다. 요소 판정만으로 disk, NIC, identity, placement edge를 입증할 수 없다.

## 7. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachines/{vmName}/extensions/{vmExtensionName}`

- 권고: **first**.
- 근거: `VirtualMachineExtensions_CreateOrUpdate`는 VM 바로 아래의 extension을 생성하거나 업데이트하여 VM에 연결된 프로비저닝/구성 메커니즘을 제공한다.
- 불확실성: 중간. extension의 실제 publisher와 setting에 따라 특정 사용 사례가 범위 안에 있는지가 결정된다.
- 관계 증거 공백: inventory 후보가 extension-to-VM 관계를 확정하지 않으므로 요소 포함을 edge 판정 대신 사용할 수 없다.

## 8. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Compute/virtualMachines/{vmName}/runCommands/{runCommandName}`

- 권고: **first**.
- 근거: `VirtualMachineRunCommands_CreateOrUpdate`는 VM 아래에 중첩된 run-command 리소스를 생성하거나 업데이트한다. 한정된 Docker-host 결과에 맞게 VM을 직접 구성할 수 있으므로, 직접 또는 전이적인 VM 연결이 없다는 두 번째 판정의 주장과 배치된다.
- 불확실성: 중간. 명령 내용에 제약이 없기 때문이다.
- 관계 증거 공백: run command를 VM에 연결하는 확정 후보가 없다. 중첩 path는 요소의 범위를 뒷받침할 뿐, 별도로 검토된 관계를 뒷받침하지 않는다.

## 9. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Network/bastionHosts/{bastionHostName}`

- 권고: **first**.
- 근거: `BastionHosts_CreateOrUpdate`는 Azure Bastion host를 생성한다. provider-native 목적은 VM 관리 접근성이므로 개별 배포에서 선택 사항이더라도 protocol의 접근성 경계에 직접 포함된다.
- 불확실성: 중간. 고정된 path 설명만으로는 대상 VM이나 virtual network가 열거되지 않는다.
- 관계 증거 공백: inventory에는 이 Bastion host를 subnet, virtual network, NIC 또는 VM과 연결하는 후보가 없다. 요소는 포함하되 해당 edge를 추론하지 않는다.

## 10. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Network/loadBalancers/{loadBalancerName}/inboundNatRules/{inboundNatRuleName}`

- 권고: `status: included`, `criterion: networkReachability`, reason: `The pinned InboundNatRules_CreateOrUpdate operation creates or updates a load-balancer inbound NAT rule that directly controls inbound reachability to a VM backend.`로 **override**한다. 충돌 항목의 source locator는 변경하지 않는다.
- 근거: schema 설명에 따르면 이 operation은 load-balancer inbound NAT rule을 생성하거나 업데이트한다. NAT forwarding은 주로 inbound reachability를 제어하므로 `failureRouting`이나 일반적인 provisioning보다 더 정확하다.
- 불확실성: 요소와 criterion에 대해서는 낮음. backend 연결은 rule 구성에 따라 달라진다.
- 관계 증거 공백: 이 충돌의 inventory 후보 중 rule을 frontend configuration, public address, backend NIC 또는 VM에 연결한다고 확정하는 것은 없다. override로 해당 edge를 추가해서는 안 된다.

## 11. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Network/publicIPPrefixes/{publicIpPrefixName}`

- 권고: **first**.
- 근거: `PublicIPPrefixes_CreateOrUpdate`는 정적 또는 동적 public-IP prefix를 생성하지만, 고정된 operation만으로 prefix 자체가 VM에 직접 연결된다고 볼 수 없다. prefix는 할당 컨테이너이며 다른 리소스가 주소를 소비해야 VM 접근성에 영향을 준다.
- 불확실성: 중간. 실제 배포에서는 public-IP 또는 NAT 리소스를 통해 prefix를 연결할 수 있다.
- 관계 증거 공백: 유일한 inventory 후보는 property reference를 통해 prefix에서 NAT gateway를 가리키지만 native target edge를 확정하지 못한다. 이 consumer chain의 부재는 직접 효과가 있다는 두 번째 판정의 주장을 받아들이지 않는 결정적 이유다.

## 12. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Network/routeTables/{routeTableName}`

- 권고: **first**.
- 근거: `RouteTables_CreateOrUpdate`는 route table을 생성하거나 업데이트한다. route는 VM-subnet 트래픽이 목적지에 도달할 수 있는지를 결정하므로 `networkReachability`가 `failureRouting`보다 직접적이고 일반적이다.
- 불확실성: criterion은 낮음. 특정 graph에서 포함된 subnet과의 연결은 중간.
- 관계 증거 공백: table의 route collection에 대한 inventory 후보가 확정되지 않았고, 이 충돌 항목은 수용된 subnet-to-table edge를 제공하지 않는다.

## 13. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Network/routeTables/{routeTableName}/routes/{routeName}`

- 권고: **first**.
- 근거: `Routes_CreateOrUpdate`는 route table의 route를 생성하거나 업데이트한다. route는 트래픽 접근성을 직접 제어하며, failure routing은 이 제어의 가능한 용도 중 하나일 뿐이다.
- 불확실성: criterion은 낮음. 구체적인 효과는 route의 실제 next-hop과 address prefix에 따라 결정된다.
- 관계 증거 공백: inventory는 이 child route에서 해당 route table로, 나아가 VM에 연결된 subnet으로 이어지는 확정 관계를 제공하지 않는다.

## 14. `ARM PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Network/virtualNetworks/{virtualNetworkName}/virtualNetworkPeerings/{virtualNetworkPeeringName}`

- 권고: **first**.
- 근거: `VirtualNetworkPeerings_CreateOrUpdate`는 virtual network의 peering을 생성하거나 업데이트한다. peering은 참여하는 virtual network 내 VM의 network reachability를 직접 변경하며 protocol에서 제외한 네트워크 범주에 속하지 않는다.
- 불확실성: 중간. operation 설명 자체로는 VM을 포함한 remote network를 식별할 수 없다.
- 관계 증거 공백: 이 subject의 inventory 후보는 확정되지 않은 schema reference이며 remote virtual-network edge나 VM path를 성립시키지 않는다. 관계를 추가하지 않고 요소만 포함한다.

## 범위 확인

- 문서화한 충돌 항목: 14개.
- 문서화한 고유 충돌 ID: 14개.
- 권고: first 12개, second 1개, override 1개. public-IP-prefix의 first 판정은 제외 판정이다. 개수는 포함 상태가 아니라 권고 레이블을 기준으로 한다.
