# 의존성 추출 알고리즘 (2026-07-20 기준)

`graphkb`가 "A를 만들려면 B가 먼저 있어야 한다"를 어떻게 알아내는지, 프로바이더별로
정확히 무엇을 읽고 무엇을 추측하는지 적는다. 수치는 전부 현재 산출물 실측이다.

> **핵심 구분은 하나다: 사실인가, 추측인가.**
>
> 신뢰도 숫자(0.5~1.0)는 이 저장소가 오래 써왔지만 **척도가 정의된 적이 없다**.
> 0.9와 0.95를 가르는 규칙도, 두 근거를 결합하는 규칙도 없다. 실제로 그 값을 쓰는
> 코드는 두 곳뿐이고(중복 시 최댓값 우선, `capacitykb`의 단일 임계선 0.8), 그마저
> 임계선이 가장 붐비는 값 위에 놓여 있다. 그래서 이 문서는 신뢰도로 줄을 세우지 않고
> **"상위가 선언한 것"과 "우리가 이름으로 추측한 것"** 으로만 나눈다.
> 숫자는 현행 코드를 설명하기 위해 병기할 뿐이다.

---

## 0. 공통 구조

**노드** = 리소스 타입 하나. id는 `{provider}::{타입명}`
(`aws::AWS::EC2::Subnet`, `azure::Microsoft.Network/virtualNetworks`, `gcp::ComputeSubnetwork`).

**엣지** = 방향 있는 관계. 필드:

| 필드 | 뜻 |
|---|---|
| `from` / `to` | `from`이 `to`를 필요로 한다 |
| `type` | `references`(참조) / `contained_in`(계층) / `equivalent_to`(동치, 매핑 레이어) |
| `via_property` | **어느 속성을 채워야 이 의존이 생기는가.** 루트부터의 실제 경로 |
| `required` | 그 속성이 필수인가 |
| `cardinality` | `one` / `many` |
| `evidence` | 아래 표의 근거 종류 |
| `confidence` | 현행 숫자(위 경고 참조) |

**중복 판정 키**는 `(from, to, type, via_property)`이고, 같은 키면 `confidence`가 높은
쪽만 남는다. 즉 **같은 속성 경로**에서 여러 근거가 충돌하면 강한 근거가 이긴다. 반대로
**경로가 다르면 별개 엣지**다 — `IoT::TopicRule → IAM::Role`이 40개 액션 경로로 40개
엣지가 된다. 이게 옳은지는 §5의 미결 항목이다.

**self-loop는 버린다**(`add_edge`가 `from == to`를 거부).

### 근거 종류 한눈에

| evidence | 프로바이더 | 사실/추측 | 무엇을 근거로 하나 | 현재 수 |
|---|---|---|---|---|
| `relationshipRef` | AWS | **사실** | AWS 스키마가 선언한 참조 | 87 |
| `cdk-oob` | AWS | **사실**(2차) | AWS CDK 팀이 손으로 검수한 목록 | 1,205 |
| `heuristic` | AWS | 추측 | 속성 이름 접미사 | 1,203 |
| `arm-hierarchy` | Azure | **사실**(구조) | 타입 이름의 경로 구조 | 2,223 |
| `bicep-ref` | Azure | 추측 | 속성 **타입 이름**이 리소스 이름과 일치 | 65 |
| `heuristic` | Azure | 추측 | 속성 이름 접미사 | 18 |
| `kcc-ref` @1.0 | GCP | **사실** | 구글의 `servicemapping` 표 | 143 |
| `kcc-ref` @0.9 | GCP | **사실**(문서) | CRD 설명문이 대상 타입을 명시 | 51 |
| `heuristic` | GCP | 추측 | 필드 이름 접미사 | 9 |
| `cb-spider-driver` | 매핑 | **사실**(사람) | CB-Spider 드라이버를 사람이 읽고 검수 | 28 |

---

## 1. AWS — `graphkb/parsers/cfn.py`

입력: CloudFormation 리소스 스키마 zip(타입 1,635개) + CDK OOB 관계 JSON.
산출: 노드 1,638 / 엣지 2,495.

### 1-1. 왜 어려운가

대부분의 스키마에 **관계가 적혀 있지 않다.** 서브넷이 VPC 안에 있다는 사실을 찾으려고
`aws-ec2-subnet.json`의 `properties.VpcId`를 열면 전부가 이것이다:

```json
"VpcId": { "type": "string",
           "description": "The ID of the VPC the subnet is in." }
```

기계 입장에서는 아무 문자열이나 넣어도 되는 칸이다. 그래서 세 갈래로 나눠 처리한다.

### 1-2. 갈래 A — `relationshipRef` (사실, 87건)

일부 스키마에 AWS가 참조를 직접 선언해 뒀다.

```json
"SecurityGroups": { "type": "array", "items": { "anyOf": [
    { "relationshipRef": { "typeName": "AWS::EC2::SecurityGroup",
                           "propertyPath": "/properties/GroupId" } } ] } }
```

순회 중 `relationshipRef`를 만나면 그 자리에서 엣지를 만든다. `typeName`이 대상이다.

> ⚠️ **`propertyPath`를 버리고 있다**(미해결 B3). "보안그룹의 `GroupId` 값을 쓴다"는
> 정보가 사라져서, `VPCEndpoint → VPC`가 실은 VPC의 `DefaultSecurityGroup` 출력을
> 참조한다는 사실이 기록되지 않는다. 방향도 대상도 맞고 **결합 지점만 틀린** 형태라
> 원본 대조로는 안 잡힌다.

### 1-3. 갈래 B — `cdk-oob` (사실 2차, 1,205건)

CDK 팀이 모아둔 `{타입: {속성경로: [{대상타입, 대상속성}]}}`을 그대로 옮긴다.
스키마와 대조해 `readOnly`인 속성은 제외하고, 배열이면 `cardinality=many`로 둔다.

AWS 공식이 아니라 CDK의 별도 산출물이라 `relationshipRef`보다 한 단계 약한 사실로 본다.

### 1-4. 갈래 C — `heuristic` (추측, 1,203건)

**이것이 AWS에만 있는 위험 지점이다.**

```
1. 속성명이 정규식 ^(\w+?)(Id|Ids|Arn|Arns)$ 에 맞는가?
     VpcId → base="Vpc"
2. base가 총칭 이름 블랙리스트에 있으면 즉시 포기        ← §1-6
3. 타입 인덱스에서 base를 찾는다
     인덱스 = { 타입명 마지막 세그먼트(소문자) : [타입명…] }
     "vpc" → ["AWS::EC2::VPC"]
4. 후보가 정확히 1개일 때만 채택.
   여럿이면 같은 서비스 안에서 유일할 때만 채택, 아니면 포기
5. 대상이 같은 서비스면 confidence 0.6, 다르면 0.5
```

**"유일 매칭"이 유일한 안전장치**라는 점을 봐 두는 게 좋다. 이름이 구체적이면 잘 맞고,
총칭이면 무너진다.

### 1-5. 순회 알고리즘

```
visit(node, path, in_array, required, depth, seen):
    깊이 32 초과면 중단

    node에 relationshipRef 있으면 → 엣지 발행 (갈래 A)

    node에 $ref 있으면 → definitions[이름]을 **같은 path로** 방문
                          (seen에 이름 누적해 순환 차단)

    anyOf/oneOf/allOf 하위 → 같은 path로 방문
    items                  → 같은 path, in_array=True
    patternProperties/additionalProperties → 같은 path, in_array=True

    properties의 각 (이름, 값):
        path가 비어 있고(=루트) 그 이름이 readOnly면 건너뜀
        새 경로 = path + (이름,)
        재귀 방문
        그 속성에 relationshipRef가 없으면 → 갈래 C 시도
```

시작은 **루트 하나뿐**이다: `visit(schema, (), False, True, 0, ∅)`.

**여기가 2026-07-20에 크게 바뀐 곳이다.** 예전에는 루트를 훑은 뒤 `definitions`를
**빈 경로로 다시** 순회했다. 그래서:

- `Settings/MongoDbSettings/CertificateArn`이 `CertificateArn`으로 기록됐다.
  실측 **heuristic 486/1,102(44.1%), relationshipRef 18/59(30.5%)** 가 실재하지 않는
  경로였다. `via_property`는 "이 의존을 만들려면 어느 속성을 채우나"를 답하는 필드라,
  틀리면 그 값으로 템플릿을 만드는 쪽이 전부 깨진다. → **지금 0%.**
- readOnly 필터가 통째로 우회됐다. `Bedrock::ModelInvocationJob`은 `VpcConfig`를 포함해
  대부분이 생성 **출력**인데 거기서 엣지가 나왔다. → 지금은 안 나온다.
- `patternProperties`(맵 타입, 253개 스키마)를 안 봐서 `AppConfig::Extension`의
  `Actions` 아래 `RoleArn` 같은 실참조를 놓쳤다. → 순회에 추가.

### 1-6. 가드 세 개

**(a) readOnly 최상위 속성 제외.** 생성 출력은 순서 제약이 아니다.
단 포인터가 **정확히 `/properties/Name` 깊이일 때만** 그 속성 전체를 배제한다.
예전에는 깊이를 안 봐서 `/properties/ComputeResources/Ec2Configuration/*/BatchImageStatus`
하나 때문에 `ComputeResources` 서브트리가 통째로 날아갔다(`Batch::ComputeEnvironment`의
LaunchTemplate·Subnet·SecurityGroup 참조 소실).

**(b) 총칭 이름 블랙리스트.** `field`, `version`, `type`, `name`, `key`, `policy`,
`target` 등 30개. 이름이 총칭이면 **이름만으로는 대상을 단정하지 않는다.**

```
FieldId   → AWS::Cases::Field       (QuickSight 차트의 내부 필드 식별자)
VersionId → AWS::Lambda::Version    (CloudFormation 훅·모듈의 버전)
TypeId    → AWS::Cassandra::Type    (ACMPCA 인증서 템플릿 종류)
```

경로 버그를 고치자 QuickSight 한 타입에서만 `FieldId` 오탐이 **421곳**으로 번져
방어가 필수가 됐다. **이름으로 긍정만 막을 뿐, 갈래 A·B의 선언은 그대로 통과한다.**

**(c) 순환·깊이.** `$ref` 순환은 `seen` 집합으로, 폭주는 깊이 32로 막는다.

### 1-7. 현재 정확도 (실측)

| 층위 | 상태 |
|---|---|
| 경로 라벨 | 세 근거 모두 **허구 0%** (수정 전 504건) |
| 대상 추론(갈래 C) | 원본 설명문 대조 — 확증 51.5%, 설명문 부재 42.4%, 미확증 5.4%(표본 검토 시 대부분 실제로는 맞음) |
| 구조적 사각지대 | `cdk-oob`가 아는 관계의 **54.2%가 `~Id`/`~Arn` 밖**(`S3BucketName`, `IAMServiceRole`, `AlarmActions`…). 갈래 C가 원리적으로 못 본다 |

---

## 2. Azure — `graphkb/parsers/azure.py`

입력: Bicep 타입 정의(커밋 고정). 산출: 노드 3,382 / 엣지 2,306.
**엣지의 96.4%가 추론이 아니다.**

### 2-1. 갈래 A — `arm-hierarchy` (사실·구조, 2,223건)

Azure 타입 이름은 그 자체가 경로다. **스키마를 읽지 않고 이름을 자른다.**

```
Microsoft.Compute/virtualMachines/extensions
  rpartition("/") → 부모: Microsoft.Compute/virtualMachines
  부모가 실제 타입 목록에 있고 "/"를 포함하면 contained_in 엣지
```

`"/"`를 요구하는 이유는 `Microsoft.Compute` 같은 네임스페이스 자체는 리소스가 아니기 때문이다.

검증: 부모 경로 규칙 위반 **0건**, 부모가 노드에 없는 경우 **0건**.
단 **대소문자까지 엄격히 보면 14건 위반**이다(`microsoft.insights/...` ↔ `Microsoft.Insights/...`).
관계는 맞고 **id 표기가 흔들리는** 문제이며, 네임스페이스 표기가 5쌍에서 갈린다.

### 2-2. 갈래 B — `bicep-ref` (추측, 65건)

속성의 **타입 이름**이 리소스 타입 이름과 맞으면 참조로 본다.

```
1. 속성의 $ref를 따라가 대상 엔트리를 얻는다 (ArrayType이면 itemType까지)
2. 그게 ObjectType이면:
     이름이 타입 목록에 정확히 있나?           → 채택
     아니면 정규화(소문자·"common" 접두 제거·복수형→단수) 후 인덱스 조회
     인덱스는 **이름이 두 타입과 충돌하면 아예 제외**한다
3. 채택되면 엣지를 만들고 **거기서 멈춘다** (대상 내부는 대상 자신의 것)
```

> ⚠️ **오탐 22.5%**(대조 가능한 40건 중 9건, 미해결 B4). 판별법이 있다 —
> ARM에서 진짜 리소스 참조는 대상 객체에 반드시 `id` 속성이 있다.
>
> ```
> Network/vpnGateways/vpnConnections.properties.routingConfiguration
>   → Network/networkManagers/routingConfigurations
>   실제 속성: [associatedRouteTable, inboundRouteMap, outboundRouteMap, …]
>   = 인라인 라우팅 설정이지 별도 리소스 참조가 아님   (같은 패턴 6건)
> ```
>
> 게다가 이름 휴리스틱인데 신뢰도 0.8로, 같은 파서의 `heuristic`(0.6)보다 **높다.**

### 2-3. 갈래 C — `heuristic` (추측, 18건)

정규식 `^(\w+?)(Id|Ids)$`(대소문자 무시)에 맞고 **대상이 문자열 타입일 때만** 시도한다.
갈래 B가 이미 채택했으면 오지 않는다.

전수 확인 결과 **18건 전부 정확하다.** AWS와 달리 Azure 타입 이름이 길고 구체적이라
(`diskEncryptionSets`, `privateLinkServices`) 총칭 충돌이 구조적으로 어렵다.

### 2-4. 순회 가드

- `flags & 2`(read_only) 속성은 아예 안 내려간다
- `visited`에 타입 인덱스를 누적해 `$ref` 순환 차단, 깊이 24
- 자기 자신이나 자기 하위 경로(`from + "/"`)로 가는 엣지는 버린다 — 계층으로 이미 표현됨

---

## 3. GCP — `graphkb/parsers/gcp.py`

입력: Config Connector CRD YAML(태그 `v1.153.0`) + `servicemapping`.
산출: 노드 95 / 엣지 203. **셋 중 근거가 가장 강하다.**

### 3-1. 후보 선정

CRD의 `spec` 스키마를 재귀 순회하며 두 조건을 **동시에** 만족하는 필드만 본다:

```
(1) 필드명이 ^(\w+?)Refs?$ 에 맞고
(2) 그 값이 KCC 참조 객체 모양이다
      = properties에 "external"이 있거나 {"name","namespace"}를 포함
```

즉 후보 자체가 **KCC 규약상 참조 필드**로 한정된다. AWS처럼 아무 문자열 속성이나
후보가 되지 않는다. 채택되면 그 아래(`external`/`name`)로는 내려가지 않는다.

### 3-2. 대상 해석 3단계

```
1) servicemapping 표에 (kind, 필드명)이 있으면 → 그 대상. evidence=kcc-ref, 1.0
     구글이 명시한 표. 추론 0.
2) 설명문 4종 패턴 중 하나에 맞으면 → 잡힌 이름. evidence=kcc-ref, 0.9
     "Allowed value: The `selfLink` field of a `X` resource"
     "externally managed X resource"
     "The name of a X resource"
     "reference to a (GCP)? X"
3) 이름 추론: base로 끝나는 kind를 찾아 유일하면 채택.
   여럿이면 같은 서비스로 좁혀 유일할 때만. evidence=heuristic, 0.6/0.5
```

### 3-3. 정확도 (실측)

- 설명문 유래 51건 중 **50건이 설명문과 일치**(1건은 설명문이 비어 대조 불가)
- 이름 추론 9건 **전수 정확**(`targetHTTPProxyRef → ComputeTargetHTTPProxy` 등)
- 대상 kind가 노드에 없는 경우 **0건**

### 3-4. 미해결 두 가지

**(a) 설명문 패턴 미탐 20건**(B6). CRD 안에 답이 있는데 표현 변형을 몰라 놓친다:

```
아는 것: "Allowed value: The `selfLink` field of a `X` resource"
못 잡음: "The Google Cloud resource name of a `ComputeFirewallPolicy` resource"
        "The ComputeTargetSSLProxy selflink in the form ..."
```

그래서 `ComputeFirewallPolicyAssociation`이 **엣지 0개**다 — 연결 리소스인데
`attachmentTargetRef`·`firewallPolicyRef` 둘 다 CRD에 있고 대상까지 적혀 있다.
참조 모양 필드 221개 중 현재 패턴이 187개(84.6%)를 해석하고, 넓은 패턴으로는
207개(93.7%)까지 간다.

**(b) 수집 범위**(B7). KCC v1.153.0에는 CRD가 **510개(서비스 136개)** 있는데 우리는
compute·container 두 서비스 **95개**만 긁는다. `Project`를 참조하는 필드가 33건인데
그 노드가 없는 이유가 이것이다. 소스의 한계가 아니라 `--services` 인자의 한계다.

---

## 4. 매핑 레이어 — `graphkb/parsers/mapping.py`

"AWS의 VPC와 Azure의 Virtual Network는 같은 것"을 잇는 `equivalent_to` 28건.
자동 추출이 아니라 **CB-Spider 드라이버 코드를 사람이 읽고 검수한** 번들 JSON이다.
`status: confirmed`만 그래프에 들어간다.

`note`에 근거가 되는 드라이버 파일·함수가 적혀 있고, 등가가 완전하지 않으면 그것도 적혀 있다:

```json
{"core": "securityGroup", "provider": "gcp", "target": "ComputeFirewall",
 "confidence": 0.7,
 "note": "gcp SecurityHandler.go: Firewalls.Insert — 규칙별 방화벽 1:N 생성 +
          TargetTag 네트워크 태그 관례. 단일 리소스 동치가 아님"}
```

`suggest()`가 이름 유사도로 후보(`status: candidate`)를 만들면 사람이 검수해
`confirmed`로 바꾸는 반자동 파이프라인이다.

---

## 5. 미결 — 점검이 필요한 것

**(a) 같은 타입쌍의 다중 경로.** 중복 키가 `via_property`를 포함하므로
`IoT::TopicRule → IAM::Role`이 40개 액션 경로에서 40개 엣지가 된다. 각 경로가 실재하므로
데이터로는 옳은데 `creation_order` 같은 질의에는 중복이다. **질의 계층에서 접을지
저장 시점에 접을지 미정.**

**(b) 같은 `(from,to,type)`에 `required`/`cardinality`가 갈린다.** aws 19/61건.
경로별로 답이 다를 뿐 모순은 아니지만, "A는 B를 반드시 필요로 하는가?"에 단일 답이 없다.
집계 규칙이 정의돼 있지 않아 소비자가 `first()`로 뽑으면 순서에 좌우된다.

**(c) 신뢰도.** 위 경고대로 척도가 정의된 적이 없다. **사실/추측 이분으로 재설계**하되,
`bicep-ref`(추측인데 0.8)와 `heuristic`(추측인데 0.5~0.6)이 같은 칸에 들어가는 게
맞는지, `cdk-oob`(사람 검수)와 `relationshipRef`(상위 선언)를 구분할지가 결정 사항이다.

**(d) AWS에 `contained_in`이 0건.** Azure는 2,223건이 계층인데 AWS는 전부 `references`다.
CloudFormation이 계층을 이름으로 표현하지 않아서인데, `primaryIdentifier` 등으로 유도할
수 있는지 미조사. 고립 노드 31.8%의 일부가 여기서 설명될 수 있다.

**(e) `*Association` 계열 고립.** AWS `AmazonMQ::ConfigurationAssociation`,
GCP `ComputeFirewallPolicyAssociation` 등 정의상 최소 2개를 잇는 리소스가 엣지 0개다.
GCP 쪽은 (a)패턴 미탐이 원인으로 확인됐고, AWS 쪽은 미확인.
