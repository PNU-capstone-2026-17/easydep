# 프로젝트 브리프: 멀티클라우드 리소스 타입 의존성 그래프 지식베이스

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md). 이 문서는 작성 시점의
> 스냅샷이고 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을
> 시작하지 말 것.** 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

> Claude Code Plan Mode 입력용 브리프.
> 사용법: `claude` 실행 → Shift+Tab으로 Plan Mode 전환 → 이 파일을 읽게 한 뒤
> "이 브리프를 기반으로 Phase 1 구현 계획을 세워줘. ultrathink" 로 시작.

---

## 1. 배경과 목표

클라우드 네이티브 애플리케이션 개발을 지원하는 AI 에이전트를 만들고 있다.
에이전트에게 필요한 지식베이스 중 하나로, **배포된 인스턴스가 아닌
"클라우드가 제공하는 리소스 타입 간 의존성 그래프"**를 구축한다.

예: "EC2 인스턴스 타입은 서브넷을 참조한다", "서브넷은 VPC에 포함된다",
"SecurityGroup 생성에는 vNet이 선행되어야 한다" 같은 스키마 레벨 지식.

최종 산출물은 그래프 DB(Neo4j) 또는 JSON 엣지 리스트 형태로,
에이전트가 "리소스 X를 만들려면 선행 리소스 체인이 무엇인가",
"X를 삭제하면 영향받는 타입은 무엇인가" 같은 질의에 답할 수 있어야 한다.

## 2. 전체 아키텍처: 3계층 그래프

1. **코어 레이어 (벤더 중립)**: Cloud-Barista CB-Tumblebug의 공통 리소스 모델
   (vNet, subnet, securityGroup, sshKey, spec, image, VM, NLB, K8s 등)에서
   추출한 타입 의존성 그래프. 커버리지는 좁지만 정확하고 벤더 중립적.
2. **벤더 레이어 (CSP별 상세)**: 각 벤더의 기계 판독 스키마에서 추출.
   - AWS: CloudFormation Registry 리소스 스키마의 `relationshipRef`
   - Azure: azure-rest-api-specs의 `format: arm-id` + `x-ms-arm-id-details`,
     또는 bicep-types-az 타입 인덱스 (파싱이 더 쉬움)
   - GCP: Config Connector(KCC) CRD의 `~Ref` 필드
3. **매핑 레이어**: 코어 노드 ↔ 벤더 노드 간 동치 매핑
   (예: 코어 vNet ↔ AWS::EC2::VPC ↔ Microsoft.Network/virtualNetworks
   ↔ ComputeNetwork). CB-Spider 드라이버 코드의 변환 로직이 1차 근거.

## 3. 단계별 범위

### Phase 1 (이번 구현 대상)
- **CB-Tumblebug OpenAPI(swagger) 파서**: Tumblebug GitHub 저장소의
  swagger/OpenAPI 스펙을 받아, 리소스 생성 요청 스키마의 참조 필드
  (`vNetId`, `subnetId`, `securityGroupIds`, `specId`, `imageId`,
  `sshKeyId` 등)를 추출하여 코어 레이어 타입 그래프를 생성.
- **AWS CloudFormation 스키마 파서**: 공개 리소스 스키마 zip을 받아
  `relationshipRef`(및 필요시 속성명 휴리스틱)를 추출하여
  AWS 벤더 레이어 그래프를 생성.
- 두 그래프를 공통 데이터 모델(아래 4절)로 직렬화.

### Phase 2 (이후)
- Azure(bicep-types-az), GCP(KCC CRD) 파서 추가.
- CB-Spider 드라이버 소스 분석 기반 매핑 레이어 반자동 생성
  (휴리스틱 후보 생성 → 사람 검수 파일).

### Phase 3 (이후)
- Neo4j 적재 스크립트 + 에이전트용 질의 도구(text-to-Cypher 또는
  사전 정의 질의 API).
- CB-Tumblebug MCP 서버와의 연계 검토(동적 데이터는 MCP 도구,
  정적 그래프는 본 지식베이스).

## 4. 공통 데이터 모델 (직렬화 스키마 초안)

```json
{
  "nodes": [
    {
      "id": "aws::AWS::EC2::Subnet",
      "layer": "vendor",           // core | vendor
      "provider": "aws",           // core 레이어는 "common"
      "kind": "resource_type",
      "display_name": "AWS::EC2::Subnet",
      "source": "cloudformation-registry"
    }
  ],
  "edges": [
    {
      "from": "aws::AWS::EC2::Subnet",
      "to": "aws::AWS::EC2::VPC",
      "type": "references",        // references | contained_in | equivalent_to
      "via_property": "VpcId",
      "required": true,            // 생성 순서 제약 여부
      "cardinality": "one",        // one | many
      "evidence": "relationshipRef", // relationshipRef | arm-id | kcc-ref | swagger-field | heuristic
      "confidence": 1.0            // 명시 메타데이터=1.0, 휴리스틱<1.0
    }
  ]
}
```

설계 원칙:
- 엣지 종류를 참조(references) / 포함(contained_in) / 동치(equivalent_to)로 구분.
- required 여부를 반드시 기록 (에이전트의 배포 순서 계획에 핵심).
- 추출 근거(evidence)와 신뢰도(confidence)를 남겨 휴리스틱 엣지를
  명시 메타데이터 엣지와 구별.

## 5. 데이터 소스

| 소스 | 위치 | 용도 |
|---|---|---|
| CB-Tumblebug swagger | github.com/cloud-barista/cb-tumblebug (src/interface/rest 또는 docs 내 swagger 파일) | 코어 레이어 |
| CloudFormation 리소스 스키마 | AWS 공개 스키마 zip (리전별 URL, 예: us-east-1) | AWS 벤더 레이어 |
| bicep-types-az | github.com/Azure/bicep-types-az | Azure (Phase 2) |
| KCC CRD | github.com/GoogleCloudPlatform/k8s-config-connector | GCP (Phase 2) |
| CB-Spider 드라이버 | github.com/cloud-barista/cb-spider | 매핑 레이어 (Phase 2) |

주의: 각 소스의 정확한 파일 경로/포맷은 구현 시점에 저장소를 직접 확인할 것.
브리프의 경로 추정이 틀렸다면 저장소 탐색 결과를 우선한다.

## 6. 기술 제약 및 선호

- 언어: Python 3.11+ (파서·그래프 처리), 의존성 최소화
  (requests/httpx, networkx 정도; Neo4j는 Phase 3까지 불필요).
- 클라우드 자격증명 불필요: 전부 공개 스키마/저장소의 정적 파싱.
- 출력: `output/core-graph.json`, `output/aws-graph.json`
  + 검토용 GraphML 또는 DOT 내보내기 옵션.
- 테스트: 파서별 최소 단위 테스트. 알려진 관계
  (Subnet→VPC, securityGroup→vNet 등)를 골든 케이스로 검증.
- 코드 구조: `parsers/` (소스별 파서), `model/` (공통 데이터 모델),
  `export/` (직렬화·변환), `tests/`.

## 7. 완료 기준 (Phase 1)

1. Tumblebug 스펙에서 최소 8개 이상의 코어 리소스 타입 노드와
   참조 엣지가 추출되고, vNet→subnet→securityGroup→VM 체인이 재현된다.
2. CloudFormation 스키마에서 relationshipRef 기반 엣지가 추출되며,
   `AWS::EC2::Subnet → AWS::EC2::VPC` 골든 케이스가 통과한다.
3. 두 그래프가 4절 스키마로 직렬화되고 스키마 검증을 통과한다.
4. 간단한 CLI: `python -m graphkb build --source tumblebug|cfn`,
   `python -m graphkb query --deps <type>` (선행 리소스 체인 출력).

## 8. 계획 수립 시 검토 요청 사항 (Plan Mode에서 답할 것)

- CloudFormation 스키마 중 relationshipRef가 없는 구형 스키마의 비율이
  높다면, 속성명 휴리스틱(`*Id`, `*Arn`)을 Phase 1에 포함할지 여부와
  오탐 관리 방안.
- Tumblebug swagger에서 요청 스키마와 응답 스키마 중 어느 쪽을
  참조 추출의 기준으로 삼을지 (생성 요청 스키마가 required 정보에 유리).
- networkx 인메모리 그래프 vs 순수 JSON 처리의 트레이드오프.
- 대용량 스키마(CFN 전체 zip) 처리 시 캐싱 전략.
