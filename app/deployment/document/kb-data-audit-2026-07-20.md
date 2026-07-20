# 데이터셋 전수 감사 (2026-07-20) — 재설계 입력

`kb-defects-2026-07-18.md`가 **코드가 데이터를 어떻게 잘못 쓰는가**였다면, 이 문서는
**데이터 자체가 어떻게 잘못 만들어졌는가**를 다룬다. 네 축의 산출물을 실측으로 전수 조사했다.

> **이 문서는 패치 목록이 아니다.** 결함 28건을 하나씩 때우는 대신, **데이터셋 구축을
> 처음부터 다시 하기로** 결정했다(2026-07-20). 개별 예외에 오버피팅하면 다음 덤프에서
> 같은 모양의 새 예외가 나온다. 여기 적힌 것은 재설계가 **반드시 만족해야 할 제약 목록**이지
> 순차적으로 처리할 티켓이 아니다.
>
> 신뢰도 수치도 재설계 대상이다 — 근거가 없다는 것이 §4에서 수치로 확인됐다.

조사 방법: 산출물 JSON 직접 쿼리 + 상위 원본 대조. 아래 수치는 전부 실측이다.
아래 항목 중 **본문에 실측 출력이 인용된 것은 별도로 직접 재현해 확인한 것**이다.

---

## 1. 결함이 수렴하는 세 지점

개별 결함 28건은 세 개의 구조적 공백에서 나온다. 재설계는 이 셋을 없애는 것이 목표다.

### (1) 레코드 **간** 정합성을 검사하는 계층이 없다

`Graph.validate()`·`CapacitySet.validate()`·각 `schema.json`은 전부 **레코드 하나의 형태**만
본다. 아래는 전부 현재 스키마를 **통과하는** 데이터다.

| 모순 | 건수 | 확인 |
|---|---|---|
| `default`가 자기 속성의 `min`을 위반 | **30건** (전부 `cfn-schema`, 신뢰도 1.0) | 🔎 |
| `mutability=read_only`인데 `required=True` | **9건** | 🔎 |
| 같은 `evidence`에 `confidence`가 여러 값 | 4개 라벨 | 🔎 |
| `acceleratorType`은 있는데 `acceleratorCount=0` | **234건** (211건은 accMem>0) | 🔎 |
| 번들 `memGiB`가 미러의 `memGiBActual`과 일치 | **19건** | 🔎 |
| capacitykb `type_id`가 graphkb 노드에 없음 | **2건** (대소문자) | 🔎 |

```
aws::AWS::MediaLive::CloudWatchAlarmTemplate Period: default=0 min=10 (evidence=cfn-schema conf=1.0)
aws::AWS::Pipes::Pipe BatchArrayProperties/Size: default=0 min=2 (evidence=cfn-schema conf=1.0)
aws::AWS::NotificationsContacts::EmailContact EmailContact/Arn  ← read_only인데 required=True
```

`default=0` 30건의 실체는 "미지정 가능하며 미지정 시 0으로 직렬화된다"인데 파서가 이를
**의미 있는 기본값으로 오독**했다. 두 레코드 다 신뢰도 1.0이라 소비자가 우선순위로
해소할 수도 없다. 산문 추출(0.34%)만 위험하다고 알려져 있었으나 이건 스키마 유래 1.0
레코드의 결함이다.

> **해결 (2026-07-21, R2)** — `kbcommon/invariants.py`에 레코드 간 검사 계층을 두고
> `write_dataset`(쓰기 관문)에 걸었다. 심각도가 둘이다: `error`는 산출물을 쓰지 않고,
> `report`는 쓰되 건수를 반드시 말한다. 전부 빌드 실패로 만들면 미러가 못 만들어진다.
>
> | 불변식 | 심각도 | 감사 시점 | 지금 |
> |---|---|---|---|
> | `default`가 min·max 안에 있는가 | error | 30건 | **0** |
> | 읽기 전용이 필수로 표시되지 않았는가 | error | 9 + azure 1 | **0** |
> | 가속기 종류가 있으면 개수도 있는가 | perfkb error / costkb report | 234건 | perfkb 0 / costkb 234 (미러라 그대로) |
> | evidence당 신뢰도 하나 | report | 5개 라벨 | 5개 라벨 (R4가 닫는다) |
> | capacitykb `type_id`가 graphkb에 있는가 | verify | 2건 | 2건 (R3가 닫는다) |
> | 번들 메모리가 미러 보정값과 같은가 | verify | — | **36/36 통과** |
>
> 앞의 둘은 원인이 서로 달랐다. `default`는 **상류가 실제로 모순돼 있다**
> (`Period: default=0, minimum=10`) — AWS의 의도를 지어낼 수 없으므로 경계만 싣고
> 모순된 기본값은 버린다. `required`는 **우리 잘못**이었다: CFN의
> `definitions.X.required`와 Azure의 `flags&1`은 "응답에 늘 들어 있다"는 뜻인데
> 파서가 "네가 채워야 한다"로 옮겼다. 사용자에게 채울 수 없는 칸을 채우라고 하게 된다.
>
> 마지막 두 개는 KB **사이**의 검사라 어느 빌드에도 넣을 수 없다(단방향 규약).
> `python -m kbcommon verify`가 산출물 JSON을 **데이터로** 읽어서 본다.
>
> 여섯 번째 항목은 감사에서 모순으로 분류됐지만 재조사 결과 **모순이 아니었다.**
> 미러의 `memGiB`에는 상류 버그가 그대로 있고(16,000 MiB ÷ 1024 = 15.625)
> `memGiBActual`이 보정값인데, 번들은 이미 보정값을 쓰고 있었다. 그래서 검사는
> "번들은 보정값과 일치해야 한다"로 세웠고 36건 전부 통과한다.

### (2) id 정규화가 KB마다 따로 있다

graphkb 파서에는 `_canon()`이 있고 capacitykb에는 **없다**.

```
capacity: azure::Microsoft.Compute/cloudServices/roleInstances/networkInterfaces
graph:    azure::microsoft.Compute/cloudServices/roleInstances/networkInterfaces
                 ^ 조인 실패
```

`capacitykb/model/records.py`의 "두 지식베이스는 코드가 분리돼 있지만 이 규약 덕분에
질의 시점에 조인할 수 있다"는 명시적 규약이 깨진 지점이다. 지금은 2건이지만 원인이
"한쪽에만 정규화가 있다"라 Azure 스키마가 갱신될 때마다 재발한다.

> **해결 (2026-07-21, R3)** — 규칙을 `kbcommon/type_ids.py` 한 곳에 두고 두 파서가
> 함께 쓴다. 실은 capacitykb에도 같은 로직(`select_latest`)이 **복사돼 있었는데 정작
> id를 만들 때 쓰이지 않았다** — 함수는 두 벌, 적용은 한 곳이었다.
>
> 표기가 갈리는 원인은 Azure 자신이다. ARM 타입명은 대소문자를 안 가려서 **API
> 버전마다 다르게 적혀 있고**(2025-03-01은 `Microsoft.Compute`, 2025-07-01은
> `microsoft.Compute`), index.json 전체에서 그런 타입이 **71종**이다. 문자열만 보고
> 옳은 표기를 고를 수 없으므로 순수 함수로는 정규화가 불가능하다 — 대신 모든 KB가
> 같은 index.json에서 같은 규칙(소문자로 묶고 최신 안정 버전의 표기를 대표로)으로
> 대표를 고른다. 소스에 핀이 박혀 있어 이 선택은 빌드마다 재현된다.
>
> 결과: `capacity-joins-graph` 1,913건 전부 통과, azure-capacity 6,607 → 6,587
> (갈려 있던 id가 합쳐지며 중복 레코드 20건 소멸), graphkb 산출물은 **불변**.
> 재발 방지로 `no-casing-duplicate-ids`·`edges-point-at-real-nodes` 불변식을
> graphkb 쓰기 관문에 걸었다.

같은 뿌리의 다른 증상: Azure 네임스페이스 표기가 5곳에서 갈리고(`Microsoft.Insights` 27
vs `microsoft.insights` 7 등, 소수 표기 노드 14개), `contained_in` 2,223건 중 **14건**이
"자식 id = 부모 id + `/세그먼트`" 규칙을 위반한다. perfkb는 Azure `id`만 specName을
소문자화해(34,846건) 손으로 id를 조립하는 소비자가 전건 무음 실패한다 — 다만 costkb와
perfkb가 같은 덤프에서 같은 형태의 id를 받으므로 **현재 조인 경로는 정상 작동**한다(🔎).
잠복 함정이지 활성 버그가 아니다.

### (3) `evidence` 라벨이 실제 추론 강도를 반영하지 않는다

Azure `bicep-ref`(신뢰도 **0.8**)는 대상 ObjectType의 **이름만** 리소스 타입 인덱스에
매칭하는 이름 휴리스틱인데, 같은 파서의 명시적 `heuristic`(0.6)보다 높은 신뢰도를 받는다.
Bicep 원본 대조 결과 65건 중 **12건이 오탐** — 대상에 `id` 속성이 없는 인라인 설정 객체다.

```
Microsoft.Compute/virtualMachines → Microsoft.Compute/sshPublicKeys
  via properties.osProfile.linuxConfiguration.ssh.publicKeys
  대상 객체 속성: ['keyData','path']   ← id 없음. 인라인 공개키이지 리소스 참조가 아님

Microsoft.Network/.../vpnConnections → Microsoft.Network/networkManagers/routingConfigurations
  대상 객체 속성: ['associatedRouteTable','inboundRouteMap',...]  ← 인라인 라우팅 설정 (동일 패턴 9건)
```

**신뢰도로 필터링하는 소비자가 오탐을 우선 채택**한다. 판별 기준은 단순하다 —
ARM에서 진짜 리소스 참조는 대상이 반드시 `id` 속성을 갖는다.

> **해결 (2026-07-21)** — `id` 판별을 파서에 넣었고, 오탐 12건은 애초에 안 생긴다.
> 고치는 과정에서 **더 큰 문제가 드러났다**: 대상 후보를 그 파일 안 타입으로만 만들고
> 있어서 파일을 넘는 참조가 원리적으로 불가능했다. Compute 파일에는
> `Microsoft.Network/networkInterfaces`가 없으니 가상머신이 네트워크 인터페이스를
> 가리킬 방법 자체가 없었던 것이다 — 참조 71/2,294개, **가상머신 나가는 관계 0개**.
>
> 후보를 전체 타입으로 넓히니 이번엔 이름이 모호해졌다(`networkInterface`로 끝나는
> 타입 5개). 이름으로는 못 푸는 문제라 파서가 짐작하는 대신 사람이 채운 표
> (`graphkb/reviewed/azure-references.json`)를 보게 했다. 결과: 참조 243개, 가상머신
> 16개, 미결 껍데기 125종/841곳 → 2종/6곳, 전체 2,509개 엣지 검수 완료.
> 자세한 내용은 `document/cloud-kb-guide.md` §S3-(나).

---

## 2. 축별 결함 목록

재설계가 통과해야 할 검사 항목으로 읽어라. "몇 건"은 재설계 후 0이 되거나, 0이 아니라면
**의도적으로 그렇다는 근거가 데이터에 있어야** 한다.

### costkb (미러 73,083건 / 번들 36건)

| # | 결함 | 규모 |
|---|---|---|
| D1 | `acceleratorType` 있는데 `acceleratorCount=0` — L4 분수 GPU 등. GPU 필터가 **양방향으로** 틀림 | 234건 🔎 |
| D2 | Inferentia/Trainium/FPGA에 가속기 필드 전무 → 같은 스펙 11배 가격차의 이유가 데이터에 없음 | 130건 |
| D3 | `kt rp-48x2-rtx` = 48 vCPU / **2 GiB**. 형제는 전부 450 GiB — 상위에서 잘린 값 | 1건 🔎 |
| D4 | `diskSizeGB`의 두 번째 센티널 `0.0`. 알려진 `-1`과 합쳐 **79.2%**가 사용 불가값 | 20,439건 🔎 |
| D5 | **번들 `memGiB`가 미러의 `memGiBActual`을 담음** — 같은 질의가 두 답을 냄 | 19건 🔎 |
| D6 | tencent 가격 100%가 센트 단위 양자화 → 저가 구간 유효숫자 1자리, 최저가 정렬이 사실상 무작위 | 2,862/2,863 |
| D7 | 전 가격이 float32를 거침(`$0.192 → 0.19200000166893005`) | 68,705/68,705 🔎 |
| D8 | `acceleratorModel` 무정규화 — T4가 4가지 표기, `"NA"` 160건이 값으로 유입 | 2,100건 |
| D9 | `infraType`이 전 레코드 `"node"` — 정보량 0 | 73,083건 🔎 |
| D10 | GCP 공유코어(e2-micro/small/medium, f1, g1) **182건 전부 가격 null** | 패밀리 5개 전멸 🔎 |

**D5가 가장 중요하다.** 미러 설계의 존재 이유(라이브 MCP와 답 일치)를 깨는 유일한 지점이다.

```
Standard_D2s_v5:  번들 8  /  미러 7.8125 (actual 8.0)
mem>=8, azure  번들 → 통과 / 미러 → 탈락
```

번들이 낡았다는 증거도 함께 나왔다 — `c5.xlarge` 가격 7.7% 차이, GCP `e2-small`/`e2-medium`은
번들엔 가격이 있는데 미러엔 null(D10과 연결: 값이 없는 게 아니라 **이 덤프에 안 들어온 것**).

### perfkb (65,032건)

| # | 결함 | 규모 |
|---|---|---|
| P1 | **`t1.micro`를 confidence 1.0으로 "상시 성능 보장"이라 단언** | 8건 🔎 |
| P2 | Azure `id`가 specName을 소문자화 — 손조립 id 무음 실패 | 34,846건 🔎 |
| P3 | `_by_provider_name`의 first-wins가 `c8gn.48xlarge` 성능을 **2배 과대 진술**, 그것도 순서 의존 | 1 spec / 25건 🔎 |
| P4 | Azure ACU가 같은 패밀리 안에서 SKU별로 갈림 — 결측을 성능차로 오해 | 3패밀리 12스펙 |
| P5 | `sustainedCpu.note: null` — "필드 부재 = 모른다" 규약 위반(여기선 null이 "해당 없음") | 62,851건 🔎 |

**P1이 이 저장소 유일의 "확신에 찬 오답"이다.**

```
t계열 value 집합:  t1={True}  t2={False}  t3={False}  t3a={False}  t4g={False}
aws+us-west-1+t1.micro: {"value": true, "note": null,
                         "evidence": "aws-burstable-field", "confidence": 1.0}
```

AWS API가 실제로 `BurstablePerformanceSupported: false`를 준다. t1은 T2 크레딧 모델보다
앞선 세대라 AWS가 그 필드에서 제외한 것인데, 파서가 이를 "상시 성능 보장"으로 번역했다.
**원본에 충실했는데 결과 명제가 사실과 반대**다. 8건뿐이지만 C4에서 다룬 병의 잔존 사례다.

P3은 C3 수정 때 내가 만든 인덱스다. docstring에 "경고에 쓰는 신호는 리전 불변"이라고
적었는데 감사가 그 전제를 수치로 검증해줬다 — **다리전 그룹 3,221개 중 3,220개에서 참(99.97%)**,
갈리는 건 EBS 4필드뿐이고 `sustainedCpu`·`currentGeneration`은 **0건**. 전제는 맞았으나
first-wins가 정책이 아니라 **파일 순서에 의존한 우연**인 것은 그대로 문제다.

### graphkb

| # | 결함 | 규모 |
|---|---|---|
| G1 | ~~Azure `bicep-ref`의 20%가 오탐 (대상에 `id` 없는 인라인 객체)~~ **해결 2026-07-21** — `id` 판별을 파서에 넣었고, 그 과정에서 드러난 파일 경계 문제(참조 71개·가상머신 0개)까지 함께 고쳤다. 위 §(3) 참조 | 12/59건 → 0 |
| G2 | AWS `heuristic`의 `via_property`가 **실재하지 않는 경로** — `definitions`를 빈 경로로 재순회 | 482건(+relationshipRef 18) |
| G3 | `relationshipRef`의 `propertyPath`를 버려 "무엇을 참조하는가"가 붕괴 | 24건 병합 |
| G4 | Azure 노드 id 대소문자 비일관 | 14건 |
| G5 | 같은 `(from,to,type)`의 `required`/`cardinality`/`confidence`가 갈림 | 19/61/85건(aws) |
| G6 | AWS `contained_in` **0건** — 컨테인먼트 축 미모델링. 고립 노드 31.8% | 🔎 |

G2는 `via_property`가 "이 의존을 만들려면 어느 속성을 채워야 하는가"를 알려주는 필드인데
실재하지 않는 경로가 들어간 것이라, 그 값으로 템플릿을 만드는 소비자가 전부 실패한다.

```
aws::AWS::ACMPCA::Certificate → aws::AWS::Cassandra::Type  via TypeId
   ← ACMPCA::Certificate에 TypeId라는 루트 속성이 없다. 덤으로 오탐까지 생성
```

G3은 방향도 대상도 맞으면서 **결합 지점만 틀린** 형태라 원본 대조로는 잡히지 않는다:
```
AWS::EC2::VPCEndpoint  SecurityGroupIds → AWS::EC2::VPC
   propertyPath=/properties/DefaultSecurityGroup   ← VPC의 이 출력을 참조하는 것
```

G6은 프로바이더별로 표현 가능한 관계 종류가 다르다는 뜻이다(🔎 `aws: {references: 2373}`
vs `azure: {contained_in: 2223, references: 83}`). 고립 31.8%가 "AWS 리소스의 1/3이 독립적"이
아니라 "축을 안 뽑았다 + `*Association` 계열을 놓쳤다"인데 데이터만 보면 구분이 안 된다.

### capacitykb

| # | 결함 | 규모 |
|---|---|---|
| K1 | `pattern` 컴파일 불가 — Java 방언(`\p{L}`, `(?s)`)과 원본 버그(`\s-_`)가 구분 없이 섞임 | 191/5,064건 🔎 |
| K2 | `pattern`이 `^`로 시작 안 함 — `match`/`fullmatch`에 따라 결과가 갈림 | 1,007건 🔎 |
| K3 | `default`가 자기 `min`/`max` 위반 | 30건 🔎 |
| K4 | `read_only`인데 `required=True` / read_only 속성에 생성시 강제 불가한 제약 | 9건 / 2,353건 🔎 |
| K5 | `required`가 **13,506건 전부 True** — "필수 아님"과 "정보 없음"이 구분 불가 | 🔎 |
| K6 | 음수 센티널(`-1`=무제한)과 진짜 하한(위도 -90)이 구분 불가 | 42건 중 ~12건 |
| K7 | `cfn-description` 하나에 confidence 0.6/0.7/0.8 — 기록되지 않은 숨은 변수가 dedup 승자를 결정 | 158건 🔎 |
| K8 | azure-quota의 `default`에 비수치 문자열(`"256 * N"`, `"/28"`) / `unit` 52/52 null | 3건 / 52건 |
| K9 | 커버리지 비대칭이 기록되지 않음 — azure 91.8%·gcp 100% 미커버 | 🔎 |

---

## 3. 문제 없음이 확인된 영역

억지 결함이 아님을 보이기 위해, 그리고 재조사 낭비를 막기 위해 명시한다.

**costkb** — (provider,region,specName) 중복 0건(대소문자 무시 기준도 0), 같은 spec의
리전 간 vCPU/메모리/architecture 불일치 0건, `id` 불변식 위반 0건, `memGiBActual` 보정
불변식 위반 0건, region 문자열 위생 0건, architecture 결측 0건(4개 값 전부 예상 내),
가격 상·하한 이상치 전부 정당(H100 8장 등), 같은 spec의 리전 간 가격 4배 초과 0건,
메모리/vCPU 비율 상한 전부 정당, 스키마 검증 미러·번들 양쪽 0건, 번들 36건 미러 존재 100%.

**perfkb** — 값의 물리적 타당성 **전 항목 0건**(clockGHz 범위, EBS baseline↔max 역전,
IOPS 역전, ACU 극단값, threadsPerCore), 필드 조합 모순 0건(`networkIsBurst`↔`networkPerformance`
양방향 완전 일치, `note`↔`value=false` 정확히 1:1), evidence↔confidence↔provider **완전 1:1**,
스키마 위반 0건, specName 대소문자 변종 충돌 0건, **Azure B계열 41개 오분류 0건**,
**GCP 공유코어 5개 오분류 0건**, details 재파싱 `DetailsMismatch` 0건.
`cachedDiskIops < diskIops` 77스펙은 서로 다른 축이라 **결함 아님**.

**graphkb** — 고아 참조 **0건**(5개 파일 전부), 완전 중복 엣지 0건, 노드 메타데이터 결측 0건,
**엣지 방향 역전 0건**(AWS relationshipRef 59건·Azure contained_in 2,223건·mapping 28건 전수 대조).

**capacitykb** — 중복 레코드 **파일 내·파일 간 모두 0건**, `min>max` 0건(세 축 전부),
`max=0` 결측 대용 0건, enum 품질 이상 0건(빈 리스트·산문 오염·중복 멤버), property 표기
혼재 0건, `value_type` 충돌 0건, quota `default>maximum` 0건. `min==max` 259건은 고정 길이
식별자·구분자로 전부 정당.

---

## 4. 신뢰도 수치에 근거가 없다 (재설계 대상) 🔎

사용자 지적을 실측으로 확인했다. **8개 값이 쓰이는데 척도 정의가 어디에도 없다.**

```
0.5: 466건   0.6: 662건   0.7: 38건   0.8: 35,027건
0.85: 1건    0.9: 1,274건 0.95: 21건  1.0: 85,871건
```

### (a) 같은 evidence에 여러 confidence — 라벨이 신뢰도의 함수가 아니다

| evidence | confidence | 비고 |
|---|---|---|
| `cfn-description` | **0.6 / 0.7 / 0.8** | 기록되지 않은 숨은 변수가 값을 정함 |
| `heuristic` (aws) | **0.5 / 0.6** | `_service(target)==service`면 0.6, 아니면 0.5 |
| `kcc-ref` | **0.9 / 1.0** | |
| `cb-spider-driver` | **0.7 / 0.8 / 0.85 / 0.9 / 0.95** | 28개 엣지에 다섯 값 — 손으로 하나씩 매긴 것 |

`cb-spider-driver`의 0.85는 **전 데이터셋에서 단 1건**이다.

> **정확히 하자면**: `core_vendor_map.json`의 값들은 *이유가 없는* 게 아니다. `note`에
> 근거가 적혀 있고 그 내용도 타당하다 — 예컨대 `securityGroup → gcp::ComputeFirewall`의
> 0.7에는 "규칙별 방화벽 1:N 생성 + TargetTag 관례. **단일 리소스 동치가 아님**"이 붙어 있다.
> 문제는 **척도가 없다는 것**이다. 왜 0.7이고 0.65나 0.75가 아닌지, 0.95와 0.9를 가르는
> 규칙이 무엇인지가 어디에도 없다. 근거는 산문으로 남았는데 숫자는 그 산문에서
> **유도되지 않는다.** 재설계가 풀어야 할 것은 "근거를 만들어라"가 아니라
> "근거에서 등급이 함수적으로 나오게 하라"다.

### (b) 실제로 작동하는 임계선은 하나뿐이고, 하필 가장 붐비는 값에 놓여 있다

`capacitykb/query.py:16`의 `CHECK_MIN_CONFIDENCE = 0.8`이 유일한 판정 임계선이다.
이게 실제로 무엇을 가르는지 재보면:

```
0.8 미만(참고로 밀림): 43건   전부 cfn-description
정확히 0.8(판정에 쓰임): 115건  전부 cfn-description
0.8 초과:            53,260건
```

**전 데이터셋에서 이 임계선이 실제로 가르는 것은 `cfn-description` 158건뿐**이고, 그중
115건이 **정확히 임계값에 놓여 있다.** 상수를 0.81로 바꾸면 115건이 판정에서 빠진다.
나머지 53,260건은 임계선 근처에 있지도 않다.

더 심한 건 perfkb다 — `azure-family-name 0.8`이 **34,846건**(전체 신뢰도 보유 레코드의 약 40%)에
붙는데, 이 하나의 상수가 그 값이어야 할 근거가 없고 역시 임계값에 정확히 걸쳐 있다.

### (c) 척도의 의미가 정의되지 않았다

0.9와 0.95의 차이가 무엇을 뜻하는지, 두 근거를 결합하면 어떻게 되는지, 0.8이 "80% 확률"인지
"우리가 꽤 믿는다"인지가 어디에도 없다. 현재 코드는 **최댓값 우선 dedup**과 **단일 임계선**
두 곳에서만 이 값을 쓴다 — 즉 실제로 필요한 것은 연속 척도가 아니라 **순서가 있는 소수의
등급**일 가능성이 높다.

**재설계 방향(초안)**: 연속값을 버리고 근거 종류에서 **유도되는** 등급으로 간다. 예를 들어
`declared`(상위가 명시) / `derived`(구조에서 유도, 규칙 기록) / `inferred`(이름·산문 추론)
3단계. 등급은 evidence에서 함수적으로 결정되므로 (a)가 구조적으로 불가능해지고, 판정에
쓸지는 등급으로 정하므로 (b)의 임계선 민감도가 사라진다. **다만 이건 초안이고, 재설계 시
등급 수·이름·판정 규칙을 근거와 함께 확정해야 한다.**

---

## 5. 재설계가 만족해야 할 것 (체크리스트)

1. ~~**빌드 시점 불변식 계층**~~ — ✅ **2026-07-21 R2 완료.** `kbcommon/invariants.py`
   (검사 얼개·공용 검사) + KB별 `invariants.py` + `python -m kbcommon verify`(KB 간).
   6종 전부 걸었고 고칠 수 있는 것은 고쳤다 — 위 §1-(1) 표 참조.
2. ~~**id 정규화 단일화**~~ — ✅ **2026-07-21 R3 완료.** `kbcommon/type_ids.py`
   (`read_azure_index` / `AzureTypeIndex.type_id`)를 두 파서가 함께 쓴다. 조인율
   어서션은 `python -m kbcommon verify`가 걸며 1,913건 전부 통과한다.
3. **evidence당 등급 하나** — 어서션으로 강제한다. 위반이 곧 라벨 세분화 필요 신호다.
4. **센티널 정규화 일원화** — `hourlyUSD`의 `<=0 → null` 규칙을 `diskSizeGB`·
   `acceleratorMemoryGB`에도 적용. 스키마 `exclusiveMinimum`으로 재유입을 막는다.
5. **커버리지를 데이터에 기록** — 산출물마다 `{types_in_graph, types_covered,
   extracted_at, source_versions}`. "정보 없음"과 "제약 없음"을 소비자가 구분할 수 있어야 한다.
6. **다대일 접기 정책 명시** — perfkb 리전 접기를 first-wins(순서 의존)에서 명시적
   정책으로. 성능 KB에서는 과대 진술이 과소 진술보다 해로우므로 보수적 최소값 + 범위 병기.
7. **소스 고정** — §6 참조. 현재 AWS·Azure는 핀이 없어 재현이 불가능하다.

---

## 6. 소스 재현성 — AWS·Azure는 핀이 없다 🔎

| 소스 | 핀 | 재현 가능? |
|---|---|---|
| cb-tumblebug 덤프 | `v0.12.25` | ✅ 태그 고정 |
| GCP KCC CRD | `v1.153.0` | ✅ 태그 고정 |
| **AWS CloudFormation 스키마** | 없음 (`CloudformationSchema.zip` 라이브 URL) | ❌ |
| **Azure bicep-types-az** | 없음 (`main` 브랜치) | ❌ |
| **Azure 쿼터 문서** | 없음 (`main` 브랜치) | ❌ |

AWS zip과 Azure `main`은 **언제든 바뀐다.** 지금 산출물이 어느 시점 스키마에서 나왔는지
기록이 없어, 위 결함 수치를 나중에 재현할 수 없고 "고쳤다"를 증명할 수도 없다.
재설계에서 가장 먼저 닫아야 할 구멍이다 — 이게 안 닫히면 나머지 검증이 전부 흔들린다.

> ✅ **2026-07-20 R1 완료.** S2는 태그, S3·S4는 커밋 SHA로 고정했다(`kbcommon/sources.py`).
> S1(AWS zip)은 원리적으로 고정 불가라 `digest`로 분류하고 받은 바이트의 sha256을
> 기록한다. 모든 산출물이 `_source` 블록으로 출처를 싣는다.
>
> **재빌드가 이 문서의 전제를 검증했다.** 고정된 소스들은 결과가 **완전히 동일**했고
> (azure-graph 3,382/2,306, azure-capacity 6,608, gcp 95/203, costkb 73,083,
> perfkb 65,032 — 전부 불변), **고정 불가한 AWS만 바뀌었다**:
>
> | 산출물 | 감사 시점 | 재빌드 후 |
> |---|---|---|
> | aws-graph 노드 / 엣지 | 1,631 / 2,373 | **1,638 / 2,381** |
> | aws-capacity 제약 | 46,810 | **47,109** |
>
> 즉 이 문서의 AWS 관련 수치는 **더 이상 존재하지 않는 zip 기준**이다. 재조사 시
> `_source`의 sha256(`83b88800e04bb5cf…`, `Last-Modified: 2026-07-18`)으로 대조할 것.
> Azure·GCP·Tumblebug 수치는 그대로 유효하다 — 그게 고정의 효과다.

자세한 소스·가공 경로·스키마는 `cloud-kb-guide.md` §18(데이터 출처 전체 목록)에 있다.
