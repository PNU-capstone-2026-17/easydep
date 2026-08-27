

# AWS 네이티브 리뷰 판정 권고안

이 문서는 권고만 제공하며 판정 패킷을 확정하지 않는다. 리뷰 범위는 `discovery-protocol.json`의 공급자 네이티브 Docker-on-VM 그래프다. 근거는 고정된 AWS CloudFormation Resource Specification 258.0.0(`cb04ddec...dae63`)과 각 충돌에 표시된 정확한 `aws-cfn#` 위치다. “first”와 “second”는 `aws-consensus.json`에서 대응하는 불변 결정을 뜻한다. override는 `overrideDecision`에 사용할 수 있는 완전한 결정 객체로 작성한다.

필수 ID 또는 전용 연결 리소스가 관계를 명시하는 경우 불확실성은 **낮음**이고, 스키마가 연결은 확립하지만 연구 기준이나 관계 레이블에 의미 분류가 필요한 경우는 **중간**이다.

| # | 종류 / 충돌 ID | 권고 | 출처 기반 근거 | 불확실성 |
|---:|---|---|---|---|
| 1 | 요소 `AWS::EC2::CapacityReservation` | **first** | 이 리소스는 인스턴스 유형, 수량, 플랫폼, 테넌시 및 가용 영역을 지정하므로 일치하는 VM 용량을 프로비저닝할 수 있는지를 직접 제약한다. | 낮음 |
| 2 | 요소 `AWS::EC2::CapacityReservationFleet` | **first** | `TotalTargetCapacity`, 할당 전략 및 인스턴스 유형 명세가 예약된 VM 용량의 프로비저닝을 직접 좌우한다. | 낮음 |
| 3 | 요소 `AWS::EC2::EC2Fleet` | **first** | 시작 템플릿 구성과 목표 용량 명세가 VM 플릿 용량을 직접 생성하거나 선택한다. | 낮음 |
| 4 | 요소 `AWS::EC2::EgressOnlyInternetGateway` | **first** | 필수 `VpcId`가 VPC를 IPv6 송신 경로에 연결하므로 VM 네트워크 도달성을 직접 변경한다. | 낮음 |
| 5 | 요소 `AWS::EC2::Host` | **first** | 인스턴스 유형/패밀리, 가용 영역, 테넌시 배치 및 복구 설정이 VM 배치와 프로비저닝을 직접 제약한다. | 낮음 |
| 6 | 요소 `AWS::EC2::Instance` | **first** | 프로비저닝되는 VM 자체이며, 스키마에 이미지, 인스턴스 유형, 서브넷, 인터페이스, 스토리지 및 자격 증명 프로파일 입력이 포함된다. 수명 주기에도 해당하지만 프로비저닝이 더 직접적인 기준이다. | 낮음 |
| 7 | 요소 `AWS::EC2::InstanceConnectEndpoint` | **override** — `{"nativeId":"AWS::EC2::InstanceConnectEndpoint","status":"included","criterion":"networkReachability","reason":"The endpoint's required SubnetId and optional SecurityGroupIds directly establish and filter an administrative path to instances in the subnet.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::InstanceConnectEndpoint"]}` | 출처는 서브넷 및 보안 그룹에 결합된 연결 엔드포인트임을 입증한다. 이는 VM 프로비저닝이 아니라 도달성에 해당한다. | 낮음 |
| 8 | 요소 `AWS::EC2::PrefixList` | **override** — `{"nativeId":"AWS::EC2::PrefixList","status":"included","criterion":"networkReachability","reason":"AddressFamily and Entries define a reusable set of network destinations used by VM-connected routing and security controls.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::PrefixList"]}` | `Entries`와 `AddressFamily`는 네트워크 목적지를 기술하므로 직접적인 연구 효과는 프로비저닝이 아니라 도달성이다. | 중간 |
| 9 | 요소 `AWS::EC2::Route` | **first** | 목적지 필드와 상호 배타적으로 선택되는 게이트웨이/인터페이스/인스턴스 대상이 패킷 도달성을 직접 결정한다. | 낮음 |
| 10 | 요소 `AWS::EC2::RouteTable` | **first** | 경로를 담는 VPC 종속 컨테이너는 네트워크 도달성에 직접 관여한다. “failure routing”은 더 좁은 개념이며 스키마로 입증되지 않는다. | 낮음 |
| 11 | 요소 `AWS::EC2::SpotFleet` | **first** | `SpotFleetRequestConfigData`와 태그는 목표 VM 인스턴스 플릿을 프로비저닝하는 요청을 정의한다. | 낮음 |
| 12 | 요소 `AWS::EC2::SubnetRouteTableAssociation` | **first** | 필수 서브넷 및 라우팅 테이블 ID가 서브넷의 유효 경로를 선택하여 VM 도달성을 직접 결정한다. | 낮음 |
| 13 | 요소 `AWS::ElasticLoadBalancingV2::ListenerCertificate` | **second** | 이 리소스는 리스너에 인증서를 추가하지만 고정된 스키마에는 대상을 선택하거나 라우팅/failure routing을 변경한다는 근거가 없다. | 중간 |
| 14 | 요소 `AWS::ElasticLoadBalancingV2::TrustStore` | **second** | 이 리소스는 S3 기반 CA 번들/이름이며, 스키마의 어떤 VM·리스너·대상 참조도 직접적 또는 전이적인 VM 그래프 연결을 확립하지 않는다. | 낮음 |
| 15 | 후보 `06b1592b12e48e1d1012` | **second** | `EIPAssociation.NetworkInterfaceId`는 주소 연결이 적용되는 인터페이스를 지정하며, 전용 연결 리소스이므로 attachment 관계다. | 낮음 |
| 16 | 후보 `0b0cca78118c9b249ddc` | **second** | `Instance.SubnetId`는 VM을 지정된 서브넷에 배치하며 불변이므로 일반 reference보다 containment가 구체적이다. | 낮음 |
| 17 | 후보 `13e8c33971cde500c60d` | **second** | `NatGateway.SubnetId`는 게이트웨이를 서브넷에 배치하며, 불변 서브넷 선택자는 containment를 나타낸다. | 낮음 |
| 18 | 후보 `187aa73c463dcf2b0d99` | **second** | 필수 불변 `Subnet.VpcId`는 서브넷을 하나의 VPC 안에 배치한다. | 낮음 |
| 19 | 후보 `19e659e5099708834f6e` | **second** | 선택적 불변 `Instance.PlacementGroupName`은 VM 배치에 사용할 placement-group 리소스를 명시적으로 선택한다. | 중간 |
| 20 | 후보 `1ab9a98102ad250ddf45` | **second** | `Instance.Volumes`는 볼륨 연결 구조의 변경 가능한 목록이므로 구체적인 관계는 attachment다. | 낮음 |
| 21 | 후보 `1b1bff4c9e5735df2ea7` | **second** | `Instance.SecurityGroups`는 인스턴스에 적용되는 보안 그룹 이름의 불변 목록으로, 포함된 네트워크 정책 리소스를 명시적으로 참조한다. | 중간 |
| 22 | 후보 `2780d8c3409e46842c65` | **first** | 필수 불변 `EgressOnlyInternetGateway.VpcId`는 게이트웨이가 속한 포함 VPC를 명확히 지정한다. | 낮음 |
| 23 | 후보 `293a21762a518e3bf589` | **second** | 필수 불변 `VPCCidrBlock.VpcId`는 CIDR 블록 리소스를 하나의 VPC에 종속시킨다. | 낮음 |
| 24 | 후보 `31d7c45e96ee09270173` | **override** — `{"candidateId":"31d7c45e96ee09270173","subjectNativeId":"AWS::EC2::Route","observedObjectNativeId":null,"status":"included","relationKind":"selection","resolvedObjectNativeIds":["AWS::EC2::NatGateway"],"reason":"Optional mutable NatGatewayId selects the NAT gateway as the route target.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::Route/Properties/NatGatewayId"]}` | 이 속성은 명시적인 대상 선택자이므로 일반 reference보다 `selection`이 정확하다. | 낮음 |
| 25 | 후보 `3d5ead1aae27cae5b2e9` | **second** | `NatGateway.VpcId`는 게이트웨이를 VPC에 배치하는 불변 범위 선택자다. | 중간 |
| 26 | 후보 `3f21d65b31a287c71990` | **second** | `Instance.NetworkInterfaces`는 인라인 `NetworkInterface` 속성 구조의 목록이지, 독립된 `AWS::EC2::NetworkInterface` ID를 명확히 참조하는 것이 아니다. | 낮음 |
| 27 | 후보 `42c1a1ccdf86a7ff6d5f` | **first** | 불변 `Instance.KeyName`은 VM에 사용할 EC2 키 페어를 선택하며 포함된 `KeyPair` 리소스와 일치한다. | 중간 |
| 28 | 후보 `50271e1c9b37f1fcac40` | **second** | 필수 불변 `NetworkInterfaceAttachment.NetworkInterfaceId`는 전용 attachment 리소스의 한 엔드포인트를 식별한다. | 낮음 |
| 29 | 후보 `52ec77f5f677dd668e1f` | **second** | `SecurityGroup.VpcId`는 그룹의 범위를 VPC로 한정하므로 reference보다 containment가 구체적이다. | 낮음 |
| 30 | 후보 `56a7b9ec228eaa8ac33d` | **second** | 필수 불변 `SubnetCidrBlock.SubnetId`는 CIDR 블록 리소스를 해당 서브넷에 종속시킨다. | 낮음 |
| 31 | 후보 `5d95c5c7b6a07a6ddedf` | **override** — `{"candidateId":"5d95c5c7b6a07a6ddedf","subjectNativeId":"AWS::EC2::VPCDHCPOptionsAssociation","observedObjectNativeId":null,"status":"included","relationKind":"attachment","resolvedObjectNativeIds":["AWS::EC2::VPC"],"reason":"Required immutable VpcId identifies the VPC endpoint of the dedicated DHCP-options association resource.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::VPCDHCPOptionsAssociation/Properties/VpcId"]}` | 전용 association은 구성을 VPC에 연결하며 ownership/containment 관계가 아니다. | 낮음 |
| 32 | 후보 `644aae8dd8d9afc647b6` | **second** | `EIPAssociation.InstanceId`는 주소 association 리소스의 선택적 엔드포인트를 식별한다. | 낮음 |
| 33 | 후보 `65d0ab9eefdafbfa01f7` | **second** | 주체인 `ListenerCertificate`가 위에서 제외되었으므로 listener 속성은 포함된 그래프 요소 사이의 관계를 형성하지 않는다. | 낮음 |
| 34 | 후보 `6e8340c31e3b3efc5108` | **second** | 필수 불변 `NetworkInterfaceAttachment.InstanceId`는 attachment의 VM 엔드포인트를 식별한다. | 낮음 |
| 35 | 후보 `7145aabbaaf222ab23e8` | **second** | 필수 불변 `VolumeAttachment.VolumeId`는 attachment의 영구 볼륨 엔드포인트를 식별한다. | 낮음 |
| 36 | 후보 `79cc1b809920051bd4e0` | **override** — `{"candidateId":"79cc1b809920051bd4e0","subjectNativeId":"AWS::ElasticLoadBalancingV2::TargetGroup","observedObjectNativeId":null,"status":"included","relationKind":"containment","resolvedObjectNativeIds":["AWS::EC2::VPC"],"reason":"TargetGroup.VpcId scopes the target group to the VPC in which its instance or IP targets are addressed.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::ElasticLoadBalancingV2::TargetGroup/Properties/VpcId"]}` | 불변 VPC 범위는 일반 reference보다 강한 관계다. | 중간 |
| 37 | 후보 `7a612cc1b7a3bc9ce405` | **second** | 필수 불변 `Route.RouteTableId`는 경로를 하나의 라우팅 테이블에 포함된 항목으로 만든다. | 낮음 |
| 38 | 후보 `86fb2c678c503cd4c0b8` | **second** | 변경 가능한 `EIP.InstanceId`는 지정된 VM에 주소를 적용하여 attachment를 나타낸다. | 낮음 |
| 39 | 후보 `88bdb62c142ecf208d03` | **second** | 필수 불변 `NetworkInterface.SubnetId`는 인터페이스를 하나의 서브넷에 배치한다. | 낮음 |
| 40 | 후보 `897c32b4497ea02a25d5` | **second** | `NatGateway.AllocationId`는 퍼블릭 NAT 게이트웨이가 사용하는 EIP 할당을 명시적으로 지정한다. | 낮음 |
| 41 | 후보 `9934e0e335d8a4f48caf` | **override** — `{"candidateId":"9934e0e335d8a4f48caf","subjectNativeId":"AWS::EC2::SubnetRouteTableAssociation","observedObjectNativeId":null,"status":"included","relationKind":"attachment","resolvedObjectNativeIds":["AWS::EC2::Subnet"],"reason":"Required immutable SubnetId identifies the subnet endpoint of the dedicated route-table association.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::SubnetRouteTableAssociation/Properties/SubnetId"]}` | association은 라우팅 테이블을 서브넷에 연결하며 association 자체를 서브넷의 자식으로 만들지 않는다. | 낮음 |
| 42 | 후보 `a43f64a3affd8f50d088` | **second** | 필수 불변 `VolumeAttachment.InstanceId`는 스토리지 attachment의 VM 엔드포인트를 식별한다. | 낮음 |
| 43 | 후보 `a9260723c86186b9dbf0` | **override** — `{"candidateId":"a9260723c86186b9dbf0","subjectNativeId":"AWS::EC2::VPCGatewayAttachment","observedObjectNativeId":null,"status":"included","relationKind":"attachment","resolvedObjectNativeIds":["AWS::EC2::InternetGateway"],"reason":"InternetGatewayId identifies the gateway endpoint of the dedicated VPC gateway attachment.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::VPCGatewayAttachment/Properties/InternetGatewayId"]}` | 리소스의 목적과 속성은 단순 reference가 아니라 attachment 엔드포인트를 식별한다. | 낮음 |
| 44 | 후보 `ad83fb5affba250318ce` | **override** — `{"candidateId":"ad83fb5affba250318ce","subjectNativeId":"AWS::EC2::Route","observedObjectNativeId":null,"status":"included","relationKind":"selection","resolvedObjectNativeIds":["AWS::EC2::NetworkInterface"],"reason":"Optional mutable NetworkInterfaceId selects the network interface as the route target.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::Route/Properties/NetworkInterfaceId"]}` | 전달에 사용할 경로 대상이 선택되는 것이며 인터페이스 attachment가 아니다. | 낮음 |
| 45 | 후보 `ae4d1580c086b115b36f` | **second** | `EIPAssociation.AllocationId`는 association이 사용하는 EIP 할당을 명시적으로 지정한다. | 낮음 |
| 46 | 후보 `ae9c2a060d6f3ea6c14d` | **override** — `{"candidateId":"ae9c2a060d6f3ea6c14d","subjectNativeId":"AWS::EC2::SubnetNetworkAclAssociation","observedObjectNativeId":null,"status":"included","relationKind":"attachment","resolvedObjectNativeIds":["AWS::EC2::NetworkAcl"],"reason":"Required immutable NetworkAclId identifies the ACL endpoint of the dedicated subnet-ACL association.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::SubnetNetworkAclAssociation/Properties/NetworkAclId"]}` | 교체 가능한 association은 ACL과 서브넷을 연결하며 association을 포함하는 관계가 아니다. | 낮음 |
| 47 | 후보 `c99ac2f9a7c80d8bd5a9` | **override** — `{"candidateId":"c99ac2f9a7c80d8bd5a9","subjectNativeId":"AWS::EC2::Route","observedObjectNativeId":null,"status":"included","relationKind":"selection","resolvedObjectNativeIds":["AWS::EC2::Instance"],"reason":"Optional mutable InstanceId selects an EC2 instance as the route target.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::Route/Properties/InstanceId"]}` | VM은 경로가 선택한 전달 대상이지 경로에 연결되는 것이 아니다. | 낮음 |
| 48 | 후보 `cbdb465be32d4b5a7def` | **second** | 변경 가능한 `SecondaryAllocationIds`는 NAT 게이트웨이가 사용하는 추가 EIP 할당을 명시적으로 나열한다. | 낮음 |
| 49 | 후보 `ce71407b47241bba2871` | **second** | 필수 불변 `NetworkAcl.VpcId`는 ACL의 범위를 하나의 VPC로 한정한다. | 낮음 |
| 50 | 후보 `e12098ee65ce6063f035` | **second** | 필수 불변 `NetworkAclEntry.NetworkAclId`는 규칙을 하나의 ACL에 포함된 항목으로 만든다. | 낮음 |
| 51 | 후보 `e42fee5c01602f688828` | **first** | `InstanceConnectEndpoint.SecurityGroupIds`는 엔드포인트를 필터링하는 보안 그룹을 명시적으로 나열한다. | 낮음 |
| 52 | 후보 `e866af26d8ebde558e46` | **override** — `{"candidateId":"e866af26d8ebde558e46","subjectNativeId":"AWS::EC2::SubnetNetworkAclAssociation","observedObjectNativeId":null,"status":"included","relationKind":"attachment","resolvedObjectNativeIds":["AWS::EC2::Subnet"],"reason":"Required immutable SubnetId identifies the subnet endpoint of the dedicated subnet-ACL association.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::SubnetNetworkAclAssociation/Properties/SubnetId"]}` | 동일한 association의 반대쪽 엔드포인트이므로 containment보다 attachment가 정확하다. | 낮음 |
| 53 | 후보 `ec27f2c239dcb8159993` | **second** | 필수 불변 `RouteTable.VpcId`는 라우팅 테이블의 범위를 하나의 VPC로 한정한다. | 낮음 |
| 54 | 후보 `f102cfaaf87fbc0848ad` | **override** — `{"candidateId":"f102cfaaf87fbc0848ad","subjectNativeId":"AWS::EC2::SubnetRouteTableAssociation","observedObjectNativeId":null,"status":"included","relationKind":"attachment","resolvedObjectNativeIds":["AWS::EC2::RouteTable"],"reason":"Required immutable RouteTableId identifies the route-table endpoint of the dedicated subnet association.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::SubnetRouteTableAssociation/Properties/RouteTableId"]}` | 이는 association 엔드포인트이지 테이블에 포함된 자식 리소스가 아니다. | 낮음 |
| 55 | 후보 `f2c51faca42ec6fdb21a` | **override** — `{"candidateId":"f2c51faca42ec6fdb21a","subjectNativeId":"AWS::EC2::InstanceConnectEndpoint","observedObjectNativeId":null,"status":"included","relationKind":"containment","resolvedObjectNativeIds":["AWS::EC2::Subnet"],"reason":"Required immutable SubnetId places the instance-connect endpoint in exactly one subnet.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::InstanceConnectEndpoint/Properties/SubnetId"]}` | 엔드포인트는 명확한 서브넷 연결을 가지며, 배치 관계이므로 reference보다 containment가 구체적이다. | 낮음 |
| 56 | 후보 `fed5617cb6ad04e7a680` | **override** — `{"candidateId":"fed5617cb6ad04e7a680","subjectNativeId":"AWS::EC2::VPCGatewayAttachment","observedObjectNativeId":null,"status":"included","relationKind":"attachment","resolvedObjectNativeIds":["AWS::EC2::VPC"],"reason":"Required immutable VpcId identifies the VPC endpoint of the dedicated gateway attachment.","sourceLocators":["aws-cfn#/ResourceTypes/AWS::EC2::VPCGatewayAttachment/Properties/VpcId"]}` | 전용 리소스가 게이트웨이를 VPC에 연결하므로 containment보다 attachment가 정확하다. | 낮음 |

## 커버리지 확인

이 표는 `aws-consensus.json`의 순서가 지정된 충돌 56건을 각각 정확히 한 번 다루기 위한 것이다. ID는 판정 키이며 `#` 열은 사람이 비교할 수 있도록 합의 순서를 보존한다.
