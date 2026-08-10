# GCP 판정 권고안

자문 목적의 분석일 뿐이다. 이 파일은 판정, 합의 또는 검토 패킷을 변경하지 않는다. 검토 범위는 고정된 Google Compute API Discovery Document(`20260722`, SHA-256 `b71cb75cb68d790065cecb01363b0d714c6388304ae027c45108255b311a3203`)와 `discovery-protocol.json`이다. `first`와 `second`는 `gcp-consensus.json`에 포함된 결정을 가리킨다.

로케이터가 리소스 값을 갖는 속성을 지칭하거나 컬렉션의 VM/부하 분산 역할이 명백한 경우 불확실성은 낮다. 컬렉션이 선택 사항이거나 특수 목적 또는 레거시인 경우에는 중간이다. "추출기 오탐"은 후보가 명시된 주체가 소유한 관계가 아니라 스키마 재사용으로 생긴 부산물일 수 있음을 뜻한다.

## 요소 충돌(42개)

| 충돌 ID | 권고 | 출처 기반 근거 | 불확실성 | 추출기 오탐 |
|---|---|---|---|---|
| `compute.addresses` | first | `addresses` 컬렉션은 VM 및 forwarding rule의 도달성에 쓰이는 리전 IP 주소를 할당하므로 일반 프로비저닝보다 `networkReachability`가 더 구체적이다. | low | no |
| `compute.autoscalers` | override: `{"nativeId":"compute.autoscalers","status":"included","criterion":"lifecycleOutcome","reason":"The autoscalers collection changes managed-instance-group capacity over its lifecycle.","sourceLocators":["gcp-compute#/resources/autoscalers"]}` | 이 컬렉션은 수명 주기 결과인 관리형 VM 그룹 크기를 제어하며, 그 자체가 장애 라우팅인 것은 아니다. | low | no |
| `compute.forwardingRules` | first | 주소/프로토콜/대상을 결합하는 전달 엔드포인트를 생성하므로 상태 기반 장애 라우팅보다 first 기준이 더 가깝다. | medium | no |
| `compute.globalAddresses` | first | 전역 주소 할당은 외부에서 도달 가능한 부하 분산 엔드포인트를 제공하여 도달성에 직접 영향을 준다. | low | no |
| `compute.globalForwardingRules` | first | 전역 forwarding rule은 리스너와 대상의 결합을 프로비저닝하며, 그 자체로 상태 기반 장애 라우팅을 수행하지 않는다. | medium | no |
| `compute.httpHealthChecks` | second | 전용 HTTP 상태 검사 컬렉션은 VM 연결 부하 분산에서 사용하는 백엔드 상태 판정을 제어한다. | low | no |
| `compute.httpsHealthChecks` | second | 전용 HTTPS 상태 검사 컬렉션은 VM 연결 부하 분산에서 사용하는 백엔드 상태 판정을 제어한다. | low | no |
| `compute.instanceGroupManagers` | second | 관리자는 관리형 VM 그룹의 생성, 갱신, 복구, 크기 조절 동작을 소유하므로 수명 주기가 더 구체적인 기준이다. | low | no |
| `compute.instanceGroups` | first | 인스턴스 그룹은 관리형 그룹과 부하 분산이 사용하는 VM 그룹화/백엔드 객체이므로 그룹 객체를 수명 주기 제어기로 보기보다 프로비저닝으로 보는 편이 낫다. | medium | no |
| `compute.instanceTemplates` | first | 템플릿은 VM 인스턴스와 관리형 인스턴스 그룹의 프로비저닝 입력이다. | low | no |
| `compute.instances` | first | 이 컬렉션은 주된 VM 프로비저닝 표면이며 수명 주기 효과도 있지만 범위 내의 핵심 역할은 프로비저닝이다. | low | no |
| `compute.instantSnapshots` | first | 나중에 복원할 디스크 상태를 포착하므로 VM 데이터 영속성에 직접 관여한다. | low | no |
| `compute.networkAttachments` | first | 네트워크 인터페이스 연결을 표현하며 VM 네트워크 연결성을 직접 변경한다. | medium | no |
| `compute.networkEndpointGroups` | first | 백엔드 서비스 트래픽 전달을 위해 VM/네트워크 엔드포인트를 등록하여 도달성에 직접 영향을 준다. | low | no |
| `compute.networkFirewallPolicies` | first | VM 트래픽을 허용하거나 거부하는 강제 가능한 네트워크 방화벽 정책을 정의한다. | low | no |
| `compute.nodeGroups` | first | 단독 테넌트 노드 그룹은 VM 인스턴스를 호스팅하고 VM을 프로비저닝할 위치를 직접 제한한다. | medium | no |
| `compute.nodeTemplates` | first | 노드 템플릿은 VM이 사용하는 단독 테넌트 노드 그룹에 필요한 프로비저닝 명세다. | medium | no |
| `compute.regionAutoscalers` | override: `{"nativeId":"compute.regionAutoscalers","status":"included","criterion":"lifecycleOutcome","reason":"The regionAutoscalers collection changes regional managed-instance-group capacity over its lifecycle.","sourceLocators":["gcp-compute#/resources/regionAutoscalers"]}` | 리전 자동 확장은 관리형 VM 용량을 바꾸며, 그 효과는 장애 라우팅이 아니라 수명 주기 관리다. | low | no |
| `compute.regionCommitments` | second | commitment는 상업적/용량 약정이며 범위가 정해진 VM 배포의 프로비저닝이나 연결에 필요한 리소스가 아니다. | low | no |
| `compute.regionInstanceGroupManagers` | second | 리전 관리자는 리전 관리형 VM 그룹의 롤링 업데이트, 복구, 크기 조절을 제어한다. | low | no |
| `compute.regionInstanceTemplates` | first | 리전 템플릿은 VM 생성 매개변수를 제공하는 프로비저닝 입력이다. | low | no |
| `compute.regionInstantSnapshots` | first | 리전 instant snapshot은 복원 가능한 디스크 상태를 포착하여 영속성을 직접 지원한다. | low | no |
| `compute.regionNetworkEndpointGroups` | first | 리전 엔드포인트 그룹은 VM/네트워크 엔드포인트를 리전 백엔드 라우팅에 연결한다. | low | no |
| `compute.regionNetworkFirewallPolicies` | first | 리전 방화벽 정책은 VM 트래픽 도달성을 직접 통제한다. | low | no |
| `compute.regionSslCertificates` | first | 리전 인증서는 VM 백엔드를 제공하는 리전 HTTPS/TLS 프록시 엔드포인트 프로비저닝에 필요한 구성 리소스다. | medium | no |
| `compute.regionTargetHttpProxies` | first | 리전 HTTP 리스너와 URL map을 잇는 라우팅 구성 요소를 프로비저닝한다. | low | no |
| `compute.regionTargetHttpsProxies` | first | VM 백엔드로 향하는 리전 HTTPS 종료와 URL map 라우팅을 프로비저닝한다. | low | no |
| `compute.regionTargetTcpProxies` | first | forwarding rule이 사용하는 리전 TCP 프록시 대상을 프로비저닝한다. | low | no |
| `compute.regionUrlMaps` | first | URL map은 요청에 사용할 백엔드 서비스를 선택하여 트래픽 도달성을 직접 제어한다. | low | no |
| `compute.resourcePolicies` | override: `{"nativeId":"compute.resourcePolicies","status":"included","criterion":"lifecycleOutcome","reason":"Resource policies can schedule VM starts/stops and govern attached-disk snapshot or placement lifecycle behavior.","sourceLocators":["gcp-compute#/resources/resourcePolicies"]}` | 인스턴스 일정과 디스크/배치 정책을 포함하여 VM 수명 주기에 직접 영향을 주지만 일반 프로비저닝 객체는 아니다. | medium | no |
| `compute.routers` | first | Cloud Router 구성은 VM 연결 VPC 경로의 동적 라우팅에 관여하므로 도달성 효과가 있다. | medium | no |
| `compute.routes` | first | route는 VM 패킷의 다음 홉을 선택하여 도달성을 직접 제어하므로 장애 라우팅은 지나치게 좁은 기준이다. | low | no |
| `compute.serviceAttachments` | second | service attachment는 Private Service Connect 생산자 서비스를 노출하며 범위가 정해진 Docker-on-VM 그래프에는 필요하지 않다. | medium | no |
| `compute.sslCertificates` | first | 인증서는 VM 백엔드로 라우팅하는 HTTPS/TLS 프록시 엔드포인트에 필요한 구성이다. | medium | no |
| `compute.storagePools` | second | storage pool은 VM 디스크의 기반 용량을 제공하여 영속성에 직접 관여한다. | low | no |
| `compute.targetGrpcProxies` | first | forwarding rule과 백엔드 라우팅 사이의 gRPC 프록시 대상을 프로비저닝한다. | medium | no |
| `compute.targetHttpProxies` | first | HTTP 프록시 대상을 프로비저닝하며 상태 기반 장애 라우팅은 백엔드 서비스와 상태 검사가 수행한다. | low | no |
| `compute.targetHttpsProxies` | first | HTTPS 종료와 URL map 라우팅을 프로비저닝하며 그 자체로 상태를 판정하지 않는다. | low | no |
| `compute.targetInstances` | first | target instance는 트래픽을 특정 VM에 결합하는 forwarding rule 대상이므로 장애 라우팅보다 프로비저닝이 더 정확하다. | low | no |
| `compute.targetSslProxies` | first | SSL 프록시 대상을 프로비저닝하며 백엔드 상태 처리는 다른 곳에서 이뤄진다. | low | no |
| `compute.targetTcpProxies` | first | TCP 프록시 대상을 프로비저닝하며 백엔드 상태 처리는 다른 곳에서 이뤄진다. | low | no |
| `compute.urlMaps` | first | URL map은 요청 속성에 따라 백엔드 서비스를 선택하여 VM 백엔드 도달성을 직접 결정한다. | low | no |

## 후보 충돌(46개)

값이 다른 네이티브 리소스의 URI/식별자인 속성에는 `reference`가 보수적인 관계 종류다. VPC에 속한다는 사실만으로 자동으로 `containment`가 되지는 않으며, 속성 자체가 대상의 연결을 나타내지 않는 한 네트워크 인터페이스를 통한 사용도 자동으로 `attachment`가 되지는 않는다.

| 충돌 ID | 권고 | 출처 기반 근거 | 불확실성 | 추출기 오탐 |
|---|---|---|---|---|
| `03f40732b257088517f4` | first | `NetworkInterface.subnetwork`은 리전 인스턴스 템플릿이 사용하는 명시적인 서브네트워크 식별자다. | low | no |
| `0a7d81c4f6bfc7bdf0b7` | first | `TargetPool.healthChecks`는 상태 검사 리소스를 명시적으로 참조한다. `selection`은 스키마 연결이 아니라 런타임 동작을 기술한다. | low | no |
| `0a811a5eaf4ff9428f92` | first | `Address.subnetwork`은 리소스 참조이며 주소가 서브네트워크를 포함하는 것은 아니다. | low | no |
| `0b4a5a0eb8555df2114a` | first | `TargetHttpsProxy.sslCertificates`는 리전 프록시가 사용하는 인증서 리소스를 명시적으로 식별한다. | low | no |
| `0f0620266be4f77b6cb3` | second | `AttachedDiskInitializeParams.storagePool`은 템플릿 생성 디스크가 프로비저닝될 풀을 식별한다. 대상이 명확하며 디스크 초기화에서의 사용은 attachment가 가장 잘 나타낸다. | medium | no |
| `1561ed1133ad14da32e0` | first | `Route.network`은 VPC를 명시적으로 참조하며 route가 네트워크를 포함하지는 않는다. | low | no |
| `1c0e01a1dd7367cf6c57` | first | `NetworkInterface.network`은 리전 템플릿 스키마의 명시적인 네트워크 URI다. | low | no |
| `1c9e05c4d9bfce59e4a2` | first | `TargetInstance.instance`는 VM을 명시적으로 참조하며 `attachment`는 필드에 없는 의미를 추가한다. | low | no |
| `2019561cb80e89cbe704` | first | `Subnetwork.network`은 명시적인 상위 네트워크 URI지만 추출 방향이 서브네트워크에서 네트워크로 향하므로, 보수적인 `reference`가 포함 의미의 역전을 피한다. | low | no |
| `22e4ee64939d257d6434` | first | `Address.subnetwork`은 서브네트워크를 명시적으로 참조하며 주소가 이를 포함하지는 않는다. | low | no |
| `26858651e9252de3581a` | first | `Address.network`은 네트워크를 명시적으로 참조하며 주소가 이를 포함하지는 않는다. | low | no |
| `29cf742e07b319c8a1d7` | first | `NetworkAttachment.subnetworks`는 서브네트워크 리소스 식별자를 명시적으로 나열한다. | low | no |
| `30430fccd3a2f4878a39` | first | 머신 이미지 네트워크 인터페이스 스키마는 명시적인 네트워크 참조를 유지하므로 `attachment`는 불필요하게 강하다. | medium | no |
| `3ce3edd2f4dcf7eda41f` | first | 인스턴스 템플릿의 네트워크 인터페이스가 네트워크 리소스를 명시적으로 지칭한다. | low | no |
| `41689f3b6e0e1dcf0418` | first | `Address.network`은 명시적인 VPC 참조이며 containment가 아니다. | low | no |
| `41781b27fcded05e6a29` | first | `BackendService.network`은 백엔드 서비스와 연관된 네트워크를 지칭하며 서비스가 VPC를 포함하지는 않는다. | low | no |
| `436975e2edcc1f5322d0` | first | `InstanceTemplate.sourceInstance`는 속성을 복사해 올 원본 VM을 명시적으로 식별한다. | low | no |
| `492c7ca7faa165bda4fc` | second | 재사용된 디스크 초기화 스키마는 머신 이미지에 표현된 디스크의 storage pool을 명시적으로 식별한다. | medium | suspected |
| `49828951f7ec4596ef8f` | second | service attachment 요소는 범위가 정해진 그래프 밖에 있으며, `ConsumerProjectLimit.networkUrl`은 프로비저닝 의존성이 아니라 허용된 소비자 네트워크를 기술할 수 있다. | high | suspected |
| `4f0699476e85f09cdffc` | second | `Disk.storagePool`은 리전 디스크의 기반 풀을 명시적으로 지칭한다. | low | no |
| `526e2c1cbf47292f91a8` | first | `NetworkEndpointGroup.subnetwork`은 서브네트워크를 명시적으로 식별하며, 포함된 리전 엔드포인트 그룹 주체로 인해 이 관계는 범위 안에 있다. | low | no |
| `71fe027296fdab8246ee` | first | `NetworkEndpointGroup.network`은 엔드포인트 그룹의 네트워크를 명시적으로 식별한다. | low | no |
| `734d3847dc0ad13fb783` | first | `NetworkInterface.networkAttachment`는 VM 인터페이스가 사용하는 Network Connectivity Center attachment를 명시적으로 식별한다. | medium | no |
| `746fb8d4fff2389709e9` | first | 템플릿 네트워크 인터페이스 속성이 network attachment를 명시적으로 식별한다. | medium | no |
| `776b2ac3e27468843123` | first | `NetworkEndpointGroup.subnetwork`은 엔드포인트 그룹의 서브네트워크를 명시적으로 식별한다. | low | no |
| `821efe662af144e7cf51` | first | 머신 이미지 네트워크 인터페이스 스키마는 명시적인 서브네트워크 참조를 유지한다. | medium | no |
| `846d56e03bbd6d67a29a` | first | `Firewall.network`은 규칙이 적용되는 VPC를 식별하며 방화벽 규칙이 VPC를 포함하지는 않는다. | low | no |
| `97d0b742008345f5add6` | first | 리전 템플릿 인터페이스가 network attachment를 명시적으로 식별한다. | medium | no |
| `9b20323bc382801f4478` | first | VM의 `NetworkInterface.subnetwork` 필드는 서브네트워크를 명시적으로 식별하므로 `reference`면 충분하다. | low | no |
| `a9e78e7f8e83241114f1` | second | VM의 디스크 초기화가 생성/연결될 디스크의 storage pool을 명시적으로 선택한다. | medium | no |
| `b1877a234f3e20a37d71` | first | `Route.nextHopInstance`는 VM 다음 홉을 명시적으로 식별하며 VM을 연결하는 것은 아니다. | low | no |
| `c91df762c2d5f46d3d84` | first | `Route.nextHopNetwork`은 네트워크 다음 홉을 명시적으로 식별하며 route가 이를 포함하지는 않는다. | low | no |
| `cc662f40d8c4e77c3179` | first | `Router.network`은 포함된 router가 route를 관리하는 VPC를 명시적으로 식별한다. | low | no |
| `cf2a3d772de1bece817e` | first | 머신 이미지 네트워크 인터페이스 스키마는 명시적인 network attachment 참조를 유지한다. | medium | suspected |
| `cf85df5092f52f1ca354` | first | `BackendService.network`은 네트워크 URI 참조이며 리전 백엔드 서비스가 VPC를 포함하지는 않는다. | low | no |
| `d391a306e8991ba40f44` | first | `TargetInstance.network`은 네트워크를 명시적으로 식별하며 target instance가 이를 포함하지는 않는다. | low | no |
| `e0a6cedf88a3aef61843` | second | `Disk.storagePool`은 기반 storage pool을 명시적으로 식별한다. | low | no |
| `e1aecdfc69261fa392f3` | first | `InstanceTemplate.sourceInstance`는 명시적인 원본 VM 참조이며 attachment가 아니다. | low | no |
| `ec34b094ef75c893954e` | first | VM `NetworkInterface.network`은 VPC를 명시적으로 식별하며 `reference`가 attachment 의미의 과장을 피한다. | low | no |
| `ef0ad3ba0efc9ce3e0da` | first | `TargetHttpsProxy.sslCertificates`는 인증서 리소스 식별자를 명시적으로 나열한다. | low | no |
| `f43b73146aa09d9bd744` | first | 포함된 리전 엔드포인트 그룹 스키마가 네트워크를 명시적으로 식별한다. | low | no |
| `f96403f355485bcc23a5` | first | 템플릿 네트워크 인터페이스 스키마가 서브네트워크를 명시적으로 식별한다. | low | no |
| `fa526eb69cb0107601fd` | first | `RouterInterface.subnetwork`은 포함된 router에 연결된 서브네트워크를 명시적으로 식별한다. | low | no |
| `fa63653013b87fc8c9b6` | second | 템플릿 디스크 초기화가 디스크에 사용하는 storage pool을 명시적으로 식별한다. | medium | no |
| `feaf24d4877b333ad646` | first | `NodeGroup.nodeTemplate`은 포함된 단독 테넌트 노드 그룹의 프로비저닝 템플릿을 명시적으로 식별한다. | low | no |
| `ff708ef0599d0dbbb988` | first | `InstanceGroup.network`은 그룹 네트워크를 명시적으로 식별하며 그룹이 VPC를 포함하지는 않는다. | low | no |

## 범위 및 추출기 주의 사항

- 합의 충돌 88개, 즉 요소 ID 42개와 후보 ID 46개를 정확히 다뤘다.
- 추출기 오탐 의심 항목은 `492c7ca7faa165bda4fc`, `cf2a3d772de1bece817e`, `49828951f7ec4596ef8f`이다. 앞의 두 항목은 모든 직렬화 이미지에 해당 관계가 있음을 입증하지 않은 채 중첩된 재사용 스키마가 머신 이미지 주체에 귀속될 수 있기 때문이다. 마지막 항목은 `ConsumerProjectLimit.networkUrl`이 허용 목록 차원을 기술하며 그 주체도 제외되기 때문이다.
- 재사용된 `NetworkInterface`와 `AttachedDiskInitializeParams` 스키마가 자동으로 오탐인 것은 아니다. 소유 리소스가 실제로 해당 구성을 포함하고 속성이 구체적인 네이티브 컬렉션으로 해석되는 경우에는 유지하되, 직렬화/생성 의미가 간접적인 경우 불확실성을 높였다.
