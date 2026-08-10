# Provider extension 후보 종합

## 목적과 판정 규칙

이 문서는 세 provider extension 감사에서 `extension`으로 분류된 표본만 종합한다. 중립 crosswalk나 provider projection은 변경하지 않는다. 고정된 inventory 또는 schema에서 둘 이상의 provider가 비교 가능한 수명 주기나 구성 경계를 드러낼 때만 반복되는 기능을 공통 후보로 제안한다. provider에 특화된 child endpoint만으로는 공통 core 개념의 증거가 되지 않는다.

증거는 다음에 기록된 고정 입력으로 한정한다.

- `depkb/neutral_candidates/{aws,azure,gcp}-extension-audit.json`
- `depkb/neutral_candidates/{aws,azure,gcp}-projection.json`
- `depkb/native/{aws,azure,gcp}-inventory.json`
- AWS CloudFormation specification `258.0.0`, Azure REST API specifications at `478f542f0e4a8872a8c6e5cde5dd4e44a01bc120`, and Google Compute discovery document `20260722`

아래에서 `Unmatched`는 고정된 provider 입력이 대응하는 managed object를 입증하지 못한다는 뜻이다. 해당 provider에 비교 가능한 제품이 없다는 뜻은 아니다.

## 계속 검토할 가치가 있는 후보

| 제안 개념과 정의 | 감사 표본 증거 | AWS / Azure / GCP의 유력한 매핑 | 우선순위 | VM 연결 IaC 실험을 차단하는가? |
|---|---|---|---|---|
| `extension.static-ip-address` — 이를 사용하는 interface 또는 listener와 독립된 수명 주기를 갖는 예약 IP 주소 | GCP `compute.addresses`, `compute.globalAddresses` | AWS: `AWS::EC2::EIP`; Azure: `.../publicIPAddresses/{publicIpAddressName}`; GCP: 표본 address 리소스 | **높음** | **조건부로 그렇다.** 실험에서 public VM 또는 load-balancer 주소의 안정성을 보장해야 할 때 필요하다. 그렇지 않으면 동적 주소로 충분하다. |
| `extension.egress-nat-gateway` — private address space에서 external network로의 managed translation | AWS `AWS::EC2::NatGateway`; Azure `.../natGateways/{natGatewayName}` | 제어면 실험에서 서로 다른 수명 주기 형태를 확인했다. AWS는 독립형 NAT Gateway를 사용하며 이를 포함한 subnet의 삭제를 막았다. Azure는 독립형 NAT Gateway를 사용하고 subnet 연결 상태에서 삭제를 거부했으며, 참조 중인 Public IP도 별도로 보호했다. GCP는 Cloud NAT를 Cloud Router에 내장하며 Router를 삭제하자 NAT 구성도 함께 삭제되었다. `depkb/experiments/{aws,azure,gcp}-neutral-nat-2026-08-07/report.md`를 참조한다. | **높음** | **조건부로 그렇다.** 테스트 VM이 private이면서 image/package를 가져와야 한다면 필요하다. public-address 기준선에는 필요하지 않다. |
| `extension.compute-capacity-reservation` — machine sizing과 구별되며 수명 주기가 관리되는 VM capacity 예약 | Azure capacity-reservation child; GCP `compute.futureReservations` | AWS: `AWS::EC2::CapacityReservation` / `CapacityReservationFleet`; Azure: capacity-reservation group 및 표본 child; GCP: `compute.reservations` 또는 표본 `compute.futureReservations` | **중간** | 아니다. 최소 연결 VM topology가 아니라 placement 보장에 영향을 준다. |
| `extension.storage-snapshot` — 연결 가능한 storage 자체가 아닌, storage의 영속적인 특정 시점 복구 원본 | Azure `.../snapshots/{snapshotName}`; GCP `compute.instantSnapshots` | AWS: 고정된 CloudFormation inventory에 일반 snapshot 수명 주기 리소스가 없다. `AWS::EC2::Volume` 생성 시 snapshot을 내장된 `SnapshotId`로 참조한다. Azure: 독립형 snapshot; GCP: `compute.snapshots`, `regionSnapshots` 및 instant 변형 | **중간** | 아니다. 첫 실험에서는 base image 또는 새 disk를 사용한다. |
| `extension.network-prefix-set` — network policy/configuration에서 참조하는, 재사용 가능한 이름 있는 IP prefix 모음 또는 할당 | AWS `AWS::EC2::PrefixList`; GCP `compute.globalPublicDelegatedPrefixes`; Azure 표본 `IpAllocations`는 관련된 할당 상태 | AWS: managed prefix list; Azure: 재사용 가능한 address set에는 inventory의 `.../ipGroups/{ipGroupsName}`가 더 유력하게 대응하며, `IpAllocations`와 public/custom prefix는 할당을 나타낸다. GCP: public delegated prefix는 할당 block이며, 고정된 generic policy address-set 대응 항목은 없다. | **중간** | 아니다. 초기 firewall rule에는 inline CIDR이면 충분하다. 더 강한 증거가 나올 때까지 “policy address set”과 “allocatable public prefix”를 별도 subtype으로 유지한다. |
| `extension.compute-workload-identity` — workload credential 또는 provider identity를 VM에 연결하는 구성 | AWS `AWS::IAM::InstanceProfile` | AWS: role을 포함하는 독립형 instance profile; Azure: 범위를 한정한 고정 inventory에서는 unmatched(내장 VM identity 구성일 가능성이 있지만 여기서는 입증되지 않음); GCP: 내장 instance service-account 구성일 가능성이 높지만 독립형 inventory 리소스는 아님 | **중간** | 기본 프로비저닝에는 아니다. bootstrap이 보호된 registry, secret 또는 API에 접근할 때만 그렇다. 공통 형태에서 독립형 profile을 요구하지 않는다. |
| `extension.compute-autoscaling-policy` — compute group의 desired size를 변경하는 policy/controller | GCP `compute.autoscalers`; 관련 표본 queued resize request | AWS: 고정된 AWS 범위에 Auto Scaling 리소스가 없어 unmatched. Azure: scale set 구성이나 외부 autoscale setting일 가능성이 있지만 이 inventory에는 없다. GCP: `compute.autoscalers` / `regionAutoscalers`; `instanceGroupManagerResizeRequests`는 policy 자체가 아니라 일시적인 child request이다. | **낮음** | 아니다. 계획된 실험은 고정 VM 수를 사용한다. |
| `extension.vm-management-extension` — VM, group 또는 project가 소유하는 영속적인 프로비저닝 후 guest-management 구성 | Azure scale-set extension 및 scale-set-instance extension 표본; GCP `compute.globalVmExtensionPolicies` | AWS: 고정 inventory에 대응하는 managed extension 경계가 없다. Azure: VM, scale-set 및 instance child 리소스. GCP: global policy 및 `compute.zoneVmExtensionPolicies` | **낮음** | 아니다. 초기 bootstrap에는 기존 compute contextualization을 우선 사용한다. 소유 범위는 provider별 metadata이지 세 가지 새로운 공통 개념이 아니다. |
| `extension.public-ip-prefix` — 개별 주소를 공급할 수 있으며 수명 주기가 관리되는 public address block | GCP `compute.globalPublicDelegatedPrefixes`; Azure `IpAllocations`는 인접 개념이지만 동등하지 않음 | AWS: 고정 inventory에 명확한 public-prefix allocation object가 없다. Azure: `.../publicIPPrefixes/{publicIpPrefixName}` 및 custom prefix. GCP: global/public delegated-prefix 리소스 | **낮음** | 아니다. 단일 주소면 충분하다. 재사용 가능한 firewall prefix set과 구별해 유지한다. |

## Provider별 또는 구성 수준의 발견 사항

추후 증거에서 반복되는 추상이 확인되지 않는 한, 다음 표본은 extension 또는 구현 세부 사항으로 유지해야 한다.

| 감사 후보 | 보수적 처리 | Cross-provider 확인 | 우선순위 | 실험을 차단하는가? |
|---|---|---|---|---|
| `AWS::EC2::InternetGateway`에서 나온 AWS `public-network-gateway` | **AWS topology extension**으로 유지하거나 public reachability의 AWS 구현으로 모델링한다. 모든 provider가 gateway object를 구체화하도록 요구하지 않는다. | Azure와 GCP projection/inventory는 범위에 포함된 VNet/VPC에 대해 이에 상응하는 필수 독립형 gateway를 입증하지 않는다. | AWS renderer에는 높음, 공통 개념으로는 낮음 | **AWS public-subnet path에는 그렇다.** 공통 실험 계약에는 아니다. |
| Azure `virtual-router` | provider extension으로 유지한다. generic route-table/route 추상은 별도 후보이며 이 표본에서 추론해서는 안 된다. | GCP에는 `compute.routers`가 있다. AWS에는 route table이 있지만 고정 schema에 비교 가능한 managed virtual-router object는 없다. 이름이 비슷하다고 역할까지 동등한 것은 아니다. | 낮음 | 단순 topology에는 아니다. |
| Azure `load-balancer-inbound-nat-rule` | 새로운 core 리소스가 아니라 내장/child load-balancer listener translation 구성으로 취급한다. | AWS listener/action 구성과 GCP forwarding-rule/backend 조합은 구조가 다르며, 세 provider에 공통인 수명 주기 경계가 입증되지 않았다. | 중간 | 실험에서 일반적인 listener-to-backend forwarding을 사용한다면 아니다. |
| Azure `application-security-group` | 또 다른 policy group이 아니라 security rule에서 사용하는 Azure selector extension으로 유지한다. | AWS security-group reference와 GCP tag/service-account selector가 관련된 선택을 표현할 수 있지만, 고정 inventory는 공통으로 addressable한 selector 리소스를 입증하지 않는다. | 낮음 | 아니다. CIDR 또는 직접적인 policy-group 연결이면 충분하다. |
| Azure `ddos-protection-plan` | managed protection extension으로 유지한다. packet-filter policy group은 아니다. | AWS나 GCP에 대응하는 표본/고정 managed plan이 입증되지 않았다. | 낮음 | 아니다. |
| Azure `storage-private-access-boundary` 및 `private-endpoint-connection` | `DiskAccess`와 private-endpoint 승인을 Azure private-service/storage access 메커니즘으로 취급한다. 모든 provider의 증거가 있을 때만 향후 private-service endpoint 계열을 검토한다. | GCP `compute.serviceAttachments`는 인접 개념이다. 고정된 AWS inventory는 이 범위에서 명확한 대응 항목을 제공하지 않는다. approval child는 service 아래의 state/configuration이며 반드시 이식 가능한 리소스인 것은 아니다. | 낮음 | 아니다. |
| Azure `compute-run-command` | 선언적 topology core 밖의 operational/guest-management child로 유지한다. | 고정된 AWS 또는 GCP inventory에는 수명 주기가 관리되는 동등한 object가 없다. bootstrap contextualization과 혼동해서는 안 된다. | 낮음 | 아니다. |
| Azure `compute-restore-point-collection`, `compute-restore-point` 및 GCP `storage-consistency-snapshot-group` | recovery-set 개념을 보류한다. 개별 storage snapshot을 먼저 모델링한다. collection 및 compute restore-point 소유권은 provider별 세부화이다. | Azure는 collection과 child restore point를 제공하고 GCP는 instant snapshot group을 제공한다. 고정된 AWS inventory는 volume 생성을 위한 snapshot reference만 제공한다. 이들의 consistency 및 restore 의미가 동등하다고 입증되지 않았다. | 낮음 | 아니다. |
| Azure `network-dscp-configuration` | Azure QoS configuration extension으로 유지한다. | 고정된 AWS/GCP inventory에는 대응하는 addressable QoS object가 입증되지 않았다. | 낮음 | 아니다. |
| Azure `interconnect-capacity-block` | provider별 개념으로, 초기 VM topology 범위 밖에 유지한다. “capacity”라는 이름이 있어도 VM capacity reservation과 병합해서는 안 된다. | 범위에 포함된 AWS/GCP inventory에 비교 가능한 object가 없으며, interconnect가 소유한다는 점에서 의미가 다르다. | 낮음 | 아니다. |
| GCP `compute-default-settings` | 배포 가능한 topology node가 아니라 provider/project 기본 구성으로 취급한다. 생성된 IaC에서는 명시적인 VM별 capacity 선택이 이를 덮어써야 한다. | 범위에 포함된 AWS/Azure inventory에 대응 object가 없다. | 낮음 | 아니다. |
| GCP `compute-group-resize-request` | autoscaling 또는 group management에 종속된 managed group의 operational child/request로 취급한다. | 고정된 AWS/Azure 입력에는 독립적으로 추적되는 대응 request 경계가 없다. | 낮음 | 아니다. |

## 첫 실험에 대한 권고

공통 실험 계약은 VM, network, subnet/segment, network attachment, security policy/rule, image, capacity choice, 필요시 storage, contextualization으로 작게 유지해야 한다. 지금은 renderer 관점의 확인 사항 두 가지만 추가한다.

1. public reachability를 명시적으로 해소한다. 안정성이 필요할 때는 reserved/static address를 사용하고 provider별 public edge 구현도 포함한다(AWS internet gateway 연결 포함).
2. outbound reachability를 명시적으로 해소한다. public VM path 또는 private VM을 위한 provider NAT 구현 중 하나를 사용한다.

그 밖의 항목은 모두 비차단 extension 후보로 유지할 수 있다. 이렇게 하면 표본 Azure child 리소스나 provider 제어면 object를 필수 공통 node로 만들지 않으면서 감사에서 발견한 정보를 보존할 수 있다.
