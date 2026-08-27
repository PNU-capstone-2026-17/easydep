# 공급자 투영 감사

날짜: 2026-08-07

## 범위 및 결과

이 감사는 `aws-projection.json`, `azure-projection.json`, `gcp-projection.json`을 `crosswalk.json`, `projection-protocol.json`, 현재의 세 `*-inventory.json` 패킷 및 해당 패킷이 지칭하는 고정 스키마와 비교한다. P1--P3, 레거시 어휘, 이전 주장, 검토 패킷, 합의 및 판정은 사용하지 않았다.

세 패킷 모두 기계적 프로토콜 검사를 통과했다. 공급자와 인벤토리 출처 식별자가 일치하고, 19개 리소스 개념이 각각 정확히 한 번 나타나며, `unmatched`가 아닌 모든 네이티브 ID가 해당 인벤토리에 존재한다. 매핑 종류와 증거 강도도 허용된 값이고, 측정된 런타임 필요성을 주장하는 패킷도 없다. 하지만 이는 의미론적 통과가 아니다. 아래 발견 사항 때문에 이 패킷들은 **공통 공급자 간 투영으로서는 차단**된다.

그 밖의 증거 강도 표기는 정직하다. 모든 항목이 `schemaCandidate`이고 모든 `runtimeNecessityConfirmed`가 false다. 이 패킷의 어떤 내용도 네이티브 문서나 런타임 증명으로 해석해서는 안 된다.

## 차단 발견 사항

### B1. 공급자마다 내장 구성을 일관되지 않게 처리함

프로토콜은 구성과 연결 개념을 포함하지만, 일치 대상에 독립적인 수명 주기 엔드포인트가 있어야 한다고 규정하지 않는다. 따라서 AWS와 GCP는 의미가 내장된 경우 개념을 이를 포함하는 리소스에 매핑하지만, Azure는 유사한 경우를 `unmatched`로 선언한다. 그 결과 개념의 의미가 아니라 공급자별 인벤토리 형태에 따라 범위가 달라진다.

구체적인 사례는 다음과 같다.

- `azure` + `neutral.machine-capacity-profile`은 `HardwareProfile.vmSize`를 인용하면서도 `unmatched`다. `aws`는 유사한 `AWS::EC2::Instance.InstanceType`을 `partial`로 매핑한다. 같은 개념의 `gcp`도 `Instance.machineType`이 있는데 `unmatched`여서 동일한 결함이 있다. Azure와 GCP 모두 컴퓨트 네이티브 ID에 `partial`로 매핑하고 프로필과 인스턴스의 차이에서 오는 손실을 유지해야 한다.
- `azure` + `neutral.compute-storage-attachment`는 `StorageProfile.dataDisks`를 인용하면서도 `unmatched`다. `gcp`는 중첩된 `Instance.disks`를 포함 리소스에 대한 `composite` 매핑으로 취급한다. Azure도 최소한 VM 네이티브 ID에 `partial`로 매핑해야 한다. 그렇지 않으면 투영 프로토콜이 모든 공급자에 대해 포함 리소스 매핑을 일관되게 금지해야 한다.
- `azure` + `neutral.load-balancer-health-check`는 고정 스키마에 probe 하위 PUT 경로가 있는데도 `unmatched`다. AWS가 `AWS::ElasticLoadBalancingV2::TargetGroup`의 상태 검사 필드를 매핑하듯, 최소한 인벤토리에 있는 상위 부하 분산기가 내장 구성을 표현할 수 있다. 더 나은 방법은 probe 엔드포인트에 자체 네이티브 ID가 부여되도록 Azure 인벤토리를 다시 생성한 후 그 ID를 매핑하는 것이다.
- `azure` + `neutral.load-balancer-listener`는 증거가 load-balancing-rule과 frontend-IP-configuration PUT 경로를 모두 식별하는데도 `unmatched`다. AWS와 GCP는 같은 개념에 분리 리소스 또는 포함 리소스를 허용한다. 인벤토리를 보수한 뒤 Azure 매핑을 `composite`로 만들거나, 분리 사실을 명시적으로 기록한 채 상위 부하 분산기에 `partial`로 매핑해야 한다.

하나의 규칙을 일관되게 적용하기 전에는 공급자별 범위와 `unmatched` 개수를 비교할 수 없으므로 이 문제들은 차단 사항이다.

### B2. Azure 부하 분산기 백엔드 풀의 잘못된 동등 판정

`azure` + `neutral.load-balancer-backend-group`은 backend-address-pool 네이티브 ID를 `equivalent`로 매핑한다. 중립 정의에는 백엔드 선택뿐 아니라 전달 프로토콜과 포트도 필요하다. 인용된 백엔드 풀은 백엔드 그룹만 설정하며, 프로토콜과 프런트엔드/백엔드 포트는 load-balancing rule에 속한다. 이를 `partial`로 바꾸거나, 누락된 rule 엔드포인트를 인벤토리에 추가한 후 rule과 함께 `composite`로 바꿔야 한다.

### B3. GCP SSH 자료를 부재로 잘못 분류함

`gcp` + `neutral.ssh-access-material`은 `unmatched`지만, 중립 정의는 독립적인 수명 주기 식별자를 요구하지 않는다. 이는 프로비저닝이나 접근과 연관된 공개 키 자료다. 패킷은 해당 자료를 담을 수 있는 인스턴스 메타데이터를 직접 인용하면서, 중첩되었다는 이유만으로 거부한다. 이는 GCP 네트워크 연결과 컨텍스트화에 사용한 포함 리소스 매핑과 정면으로 충돌한다. `compute.instances`를 `partial`로 매핑하고 메타데이터 관례와 외부 ID 메커니즘을 손실/의미 차이로 기술해야 한다. 고정 스키마만으로 SSH 키 메타데이터 관례를 식별할 수 없다면 `schemaCandidate`를 유지하고 네이티브 문서가 여전히 필요하다고 밝혀야지, 개념이 없다고 해서는 안 된다.

### B4. Azure 컨텍스트화를 잘못된 수명 주기 의미에 매핑함

`azure` + `neutral.compute-contextualization`은 VM-extension 하위 리소스에만 매핑된다. 중립 개념은 컴퓨트 인스턴스 생성 때 제공되어 부팅 말미에 실행되는 일회성 부트스트랩 데이터다. VM extension은 별도로 관리되며 생성 후에도 적용할 수 있는 사용자 지정 리소스이므로 이 수명 주기 의미를 보존하지 않는다. 같은 증거가 가리키는 `OSProfile.customData`가 VM 생성 시점 후보로 더 적절하다. VM 네이티브 ID에 `partial`로 매핑해야 하며, extension은 수명 주기 차이를 명시할 때만 추가 대안이 될 수 있다. 현재 매핑의 보존 의미는 "일회성 부트스트랩"을 일반 구성으로 조용히 확장한다.

### B5. AWS 리스너 동등 판정에서 중립 주소 차원이 누락됨

`aws` + `neutral.load-balancer-listener`는 `equivalent`지만, 자체 보존 의미와 증거가 입증하는 것은 프로토콜, 포트, 작업, 부하 분산기 참조뿐이며 중립 정의가 요구하는 리스너 주소는 아니다. 주소 선택은 부하 분산기/프런트엔드 측에 있다. 이를 `partial`로 분류하거나 `AWS::ElasticLoadBalancingV2::LoadBalancer`와 함께 `composite`로 분류하고 분리 사실을 기록해야 한다. 이는 Azure와 GCP 패킷이 이미 인정한 분리 구조에 대응하는 AWS 사례다.

## 비차단 발견 사항

### N1. AWS 네트워크 부하 분산기의 손실 기술이 불완전함

`aws` + `neutral.network-load-balancer`를 `partial`로 본 것은 합리적이지만 `lostOrDifferentMeaning`은 속성으로 선택되는 ELB 변형만 언급한다. 중립 정의에는 리스너, 백엔드 그룹, 상태 검사가 포함되지만 매핑은 `AWS::ElasticLoadBalancingV2::LoadBalancer`만 지칭한다. 이 부분들이 별도의 Listener 및 TargetGroup 리소스임을 추가해야 한다. 이는 설명의 결함이며 반드시 매핑 종류를 바꿔야 하는 문제는 아니다.

### N2. Azure 인벤토리에 증거로 확인된 누락이 있음

`azure` + `neutral.load-balancer-listener` 및 `azure` + `neutral.load-balancer-health-check` 투영은 `azure-inventory.json`에 없는 고정 PUT 경로인 load-balancing rules, frontend-IP configurations, probes를 인용한다. 이는 프로토콜이 요구하는 구체적인 공급자 확장 감사 발견 사항이다. 현재 인벤토리에 추가하거나, 범위 사유와 함께 제외 항목으로 명시해야 한다. 그전까지 투영 검증기의 "known native ID" 규칙은 정보 손실을 강제한다.

### N3. 부정 증거 로케이터는 독립적으로 검증 가능한 패킷이 아님

`aws` + `neutral.infrastructure-aggregate`, `aws` + `neutral.compute-group`, `azure` + `neutral.infrastructure-aggregate`, `gcp` + `neutral.infrastructure-aggregate`의 `unmatched` 매핑은 전체 인벤토리 또는 컬렉션 루트 로케이터를 부재 증거로 사용한다. 문구가 주장을 해당 범위의 인벤토리와 스키마로 올바르게 제한하므로 강도를 과장하지는 않는다. 그러나 향후 기계 감사는 포인터만으로 부재를 도출할 수 없다. 검색한 후보 집합이나 제외 질의/결과 다이제스트를 기록해야 한다. 이는 현재 가설 패킷을 차단하지 않는다.

### N4. `composite`가 "모든 부분이 필수"와 "네이티브 대안"을 혼합함

`gcp` + `neutral.compute-group` 및 `gcp` + `neutral.network-load-balancer`는 필수 협력 리소스와 상호 배타적인 영역/리전 또는 제품군 대안을 하나의 평면 `nativeIds` 배열에 함께 나열한다. 반대로 `gcp`의 리스너/백엔드/상태 검사 매핑은 이러한 대안을 `partial`이라고 한다. 현재 프로토콜은 어느 표현이든 검증하지만 논리곱과 대안을 구분할 수 없다. 패킷을 자동으로 소비하기 전에 그룹화 의미(예: `allOf`/`oneOf`)를 추가해야 한다. 현재 설명은 수동 검토 용도로는 충분히 신중하다.

## 1차 처분(2차 결과로 대체됨)

아직 세 패킷을 비교 가능한 공급자 투영으로 승격하지 않는다. 먼저 포함 리소스 매핑을 허용할지 결정하고 문서화하여 B1을 해결한다. AWS와 GCP에서 이미 사용한 동작을 따른다면 Azure 인벤토리 누락을 보수한 뒤 B2--B5를 수정한다. 이후 기존 프로토콜 검증기를 다시 실행한다. 검증기는 ID 소속과 패킷 완전성만 확인하고 대응 관계의 진실성은 확인하지 않으므로 의미론 검토는 여전히 필요하다.

## 2차 재감사

재감사 날짜: 2026-08-07

**결과: 가설 주도 CSP 실험 용도로 PASS.** 수정된 세 패킷은 기계적 프로토콜 검증기를 다시 통과했으며, 1차의 차단 발견 사항 다섯 개도 모두 해결되었다. 공급자별 실험에서 수동 검토된 가설로 이 패킷들을 사용하는 데 남은 의미론적 차단 사항은 없다.

기존 발견 사항별 해결 결과:

- **B1 PASS.** `azure` + `neutral.machine-capacity-profile`과 `azure` + `neutral.compute-storage-attachment`은 이제 이를 포함하는 VM 리소스에 `partial`로 매핑되며, `gcp` + `neutral.machine-capacity-profile`도 `compute.instances`에 `partial`로 매핑된다. Azure 리스너와 상태 검사는 누락된 하위 식별자 손실을 유지하면서 부하 분산기 포함 리소스에 정직한 `partial` 매핑을 사용한다. 이는 다른 곳에서 이미 사용한 포함 리소스 처리와 일관된다.
- **B2 PASS.** `azure` + `neutral.load-balancer-backend-group`은 `equivalent`에서 `partial`로 변경되었고, 전달 프로토콜과 포트가 load-balancing rule에 있음을 명시한다.
- **B3 PASS.** `gcp` + `neutral.ssh-access-material`은 `unmatched`에서 `compute.instances`의 `partial`로 변경되었다. 손실 설명은 discovery 스키마가 일반 메타데이터만 입증하며 SSH 관례와 OS Login 상호작용에는 네이티브 문서가 여전히 필요하다고 정확히 밝힌다.
- **B4 PASS.** `azure` + `neutral.compute-contextualization`은 이제 VM extension이 아니라 VM과 생성 시점의 `customData`에 매핑된다. 부트스트랩 데이터 전달만 보존한다고 명시하며 실행에 대한 스키마 증거를 주장하지 않는다.
- **B5 PASS.** `aws` + `neutral.load-balancer-listener`는 `equivalent`에서 `partial`로 변경되었고, 프런트엔드 주소/서브넷 선택이 참조된 부하 분산기에 속함을 기록한다.

1차 N1도 해결되었다. `aws` + `neutral.network-load-balancer`는 이제 리스너, 백엔드 그룹, 상태 검사가 각각 별도의 AWS 리소스로 표현됨을 기록한다.

### 남은 비차단 한계

CSP 실험 설계에 관해서는 **차단 상태로 남은 공급자+개념 발견 사항이 없다.** 다음의 정확한 한계는 남아 있다.

- `azure` + `neutral.load-balancer-listener`는 load-balancing-rule 및 frontend-IP-configuration 하위 PUT 경로가 `azure-inventory.json`에 없으므로 여전히 인벤토리에 있는 상위 리소스에만 매핑된다.
- `azure` + `neutral.load-balancer-health-check`는 probe 하위 PUT 경로가 `azure-inventory.json`에 없으므로 여전히 인벤토리에 있는 상위 리소스에만 매핑된다.
- 부정 증거의 감사 가능성 문제는 `aws` + `neutral.infrastructure-aggregate`, `aws` + `neutral.compute-group`, `azure` + `neutral.infrastructure-aggregate`, `gcp` + `neutral.infrastructure-aggregate`에 남아 있다. 범위가 제한된 부재 주장은 계속 정직하게 `schemaCandidate`로 표시되므로 이는 대응 관계 차단이 아니라 출처 강화를 위한 사안이다.

### `allOf`/`oneOf` 처분

매핑을 가설로 취급하고 사람이 작성한 실험 패킷이 공급자 제품, 범위, 리소스 조합 하나를 선택한다면 평면 `nativeIds`의 한계는 다음 CSP 실험을 **차단하지 않는다**. 설명은 이미 `gcp` + `neutral.compute-group`, `gcp` + `neutral.network-load-balancer`, `gcp` + `neutral.load-balancer-listener`, `gcp` + `neutral.load-balancer-backend-group`, `gcp` + `neutral.load-balancer-health-check`에서 중요한 논리곱과 대안의 차이를 식별한다.

그러나 모든 평면 `nativeIds` 구성원을 언제나 필수이거나 자유롭게 교환 가능한 것으로 해석하는, 향후 검토 없는 기계 실행은 **차단한다**. 자동 토폴로지 생성, 실현 가능한 변형별 범위 점수 계산 또는 자동 배포 선택 전에 패킷 스키마가 그룹화된 대안과 필수 조합(예: 중첩된 `oneOf`와 `allOf`)을 표현해야 한다. 그전까지 자동 소비자는 후보 생성 단계에서 멈추고 명시적인 공급자별 변형 선택을 요구해야 한다.
