# cb-tumblebug 자원 전수 조사와 의존성 판정 기준 (2026-07-29)

> **과거 조사:** 현재 VM 의존성 기준은 depkb 재검증 후 확정한다. 이 문서를 현재 제품 범위로 사용하지 않는다.

> **측정 기록이다.**
>
> **수치는 유효하다.** 다만 서술의 전제는 [`docs/cloud-native-extension.md`](cloud-native-extension.md)를 따른다 —
> 이 문서 서두가 지식베이스를 '배포 기질'로 놓고 쓰였는데, 지금 기준은 그것이
> 아니다(지식베이스는 모든 단계를 가로지른다). **측정과 판정 절차는 그대로 쓴다.**


> **§3.2의 `D1~D9` 증거층 분류는 폐기됐다 (2026-07-30).** 그건 우리가 얹은
> 분류이지 근거가 아니었고, 층 이름이 인용을 가리는 구조였다. 산출물은 관측
> 중심으로 다시 세웠다 — 관측마다 **출처·형태·인용(`file:line`)**이 붙는다.
> **측정치와 인용은 전부 유효하다.** 층 이름만 근거로 쓰지 말 것.

배포 기질로 삼기로 한 cb-tumblebug이 **무엇을 만들 수 있고, 그것들이 서로 무엇을
요구하는가**를 전수로 조사한 기록입니다. 범위 결정은
`kb-scope-tumblebug-2026-07-29.md`에 있고, 이 문서는 그 §3.4가 *"아직 모르는 한
칸"*으로 남긴 자리를 채웁니다.

기계가 읽는 산출물은 **`app/core/cloudkb/graphkb/parsers/tumblebug_resources.json`**입니다.
아래 수치는 전부 그 파일에서 나왔고, 그 파일은 손으로 적은 것이 아니라 소스에서
뽑았습니다.

---

## 1. 무엇을 재료로 삼았나

| 재료 | 핀 | 무엇을 읽었나 |
|---|---|---|
| cb-tumblebug 소스 | `v0.12.25` | `core/model`(자원 스키마) · `core/resource`(CRUD) · `core/infra`(프로비저닝) · `interface/rest/server`(라우트) · `assets/*.yaml`(프로바이더별 제약) · `testclient/scripts`(운영 순서) |
| cb-spider 소스 | `v0.12.37` | `cloud-driver/interfaces/resources`(CSP 중립 계약) |
| cb-tumblebug swagger | `v0.11.8` | 지금 우리 `core-graph`가 쓰는 소스 — **대조군** |

셋 다 캐시에 있던 것이라 네트워크 없이 돌았습니다.

### 1.1 "전수"를 무엇으로 보증했나

사람 눈으로는 보증할 수 없어서 **파서가 보증**합니다. Go 구조체를 전수로 읽고
(`650종 · 94파일`), 참조 필드를 규칙으로 걸러 낸 다음, **규칙이 판정하지 못한 것을
버리지 않고 남겼습니다.**

```
구조체 650 → 자원 참조 필드 174 → 요청 스키마에서 도달 가능 44
             규칙이 판정 못 한 `*Id` 필드 99 (서로 다른 stem 29)
```

전수의 조건은 미판정이 0인 것이 **아니라** 미판정 하나하나가 자원이 아님이 확인되는
것입니다. 29개를 전부 분류했습니다(§3.5).

**초안 규칙은 실제로 놓쳤습니다** — `sourceNodeId`(customImage→node), `subnet1ID`·
`subnet2ID`(sqlDb→subnet), `VpcIID`·`KeyPairIID`(cb-spider 어휘)가 처음 규칙을 통과하지
못했습니다. 미판정을 남기지 않았다면 셋 다 조용히 빠졌을 것입니다.

---

## 2. 자원 전수 — 21종

| 종류 | 자원 |
|---|---|
| **프로비저닝 17** | `vNet` · `subnet` · `securityGroup` · `sshKey` · `infra` · `node` · `dataDisk` · `customImage` · `nlb` · `k8sCluster` · `k8sNodeGroup` · **`sqlDb`** · **`objectStorage`** · **`vpn`** · **`publicIp`** · **`vNic`** · **`globalDns`** |
| **논리 1** | `nodeGroup` — 독립 생성 엔드포인트가 없고 `CreateNodeGroupReq`의 단위로만 존재한다 |
| **카탈로그 2** | `spec` · `image` |
| **cb-spider에만 1** | `fileSystem` — 드라이버 계약에는 있는데 cb-tumblebug이 노출하지 않는다 |

굵은 여섯이 **우리 `core-graph` 13종에 없던 것**입니다.

### 2.1 자원이 두 종류라는 것은 우리 추측이 아니다

`spec`·`image`를 카탈로그로 가른 근거는 코드에 있습니다. 노드를 지울 때
`UpdateAssociatedObjectList`가 image·customImage·sshKey·vNet·securityGroup·dataDisk에는
붙는데 **`spec`만 주석으로 꺼져 있습니다**(`core/infra/control.go:1302`). 카탈로그
항목은 지워도 실물이 죽지 않으므로 참조 카운트가 필요 없습니다 — 구분이 이미 코드에
있고 우리는 그것을 읽었을 뿐입니다.

### 2.2 우리 핀이 두 단계 뒤처져 있었다

| | swagger `v0.11.8`(우리 소스) | 소스 `v0.12.25` |
|---|---:|---:|
| REST 경로 | 142 | **345** |
| 자원 | 16 | **21** |

`publicIp`·`vNic`·`globalDns`는 v0.12.25에서 생겼고, `mci`는 **`infra`로 개명**됐습니다.
`sqlDb`·`objectStorage`·`vpn`은 v0.11.8에도 있었는데 **우리 파서가 안 뽑고 있었습니다** —
버전이 아니라 파서의 결함이었습니다.

> 경계를 "파서가 뽑는 것"으로 잡았으면 이 다섯을 영영 못 봤을 자리입니다. 범위 계획의
> 결정 S3(*"판정 근거는 swagger 전수, 우리 파서가 아니라"*)이 여기서 값을 했습니다.

---

## 3. 의존성의 판정 기준

### 3.1 "의존"은 한 단어로 안 된다

같은 질문에 소스가 서로 다르게 답합니다. `securityGroup`이 `vNet`을 요구하는가?

- cb-tumblebug `SecurityGroupReq.VNetId` — **`validate:"required"`가 없다.** 주석:
  *"Optional for registration: some CSPs (e.g., Azure, Tencent, NHN) don't bind SG to VPC"*
- cb-spider `SecurityInfo.VpcIID` — **`validate:"required"`.**
- 운영 스크립트 — vNet을 먼저 만들고, 삭제는 역순이며 `DependencyViolation` 재시도
  루프가 박혀 있다.

셋 다 참입니다. 층이 다를 뿐입니다. **그래서 의존을 하나의 불리언으로 적으면 반드시
어느 층에 대해 거짓이 됩니다.** (지금 우리 `core-graph`가 `securityGroup→vNet`을
`required=true · basis=stated` 하나로 적어 둔 것이 정확히 그 상태입니다.)

### 3.2 증거 9층

각 층은 **관측 가능**하고(코드의 특정 자리), **반증 가능**합니다(그 자리가 바뀌면
판정이 바뀐다).

| 층 | 무엇을 관측하나 | 무엇을 뜻하나 | 관측 자리 |
|---|---|---|---|
| **D1** | `validate:"required"`가 붙은 참조 필드 | 그 값 없이는 요청이 **거부된다** | `core/model/*.go` |
| **D2** | 참조 필드인데 필수가 아님 | 쓸 수 있으나 없어도 된다 | 〃 |
| **D3** | REST 경로 중첩 | 부모 없이는 **주소가 없다** | `interface/rest/server/server.go` |
| **D4** | 생성 전 조회 후 실패 시 거부 | 값이 있어도 **실물이 없으면** 거부 | `core/infra/provisioning.go` |
| **D5** | 참조 카운트가 비어야 삭제 | **역방향 강제** — 시스템이 위반을 막는다 | `core/resource/common.go:417` |
| **D6** | 없으면 만들어서라도 채운다 | 필수의 다른 증거 | `provisioning.go:3335·3448·3488` |
| **D7** | cb-spider 중립 계약이 요구 | **TB 밖 CSP 층**의 요구 | `cloud-driver/interfaces/resources` |
| **D8** | 프로젝트 자신의 생성·삭제 순서 | 실제로 그 순서로만 성공한다 | `testclient/scripts/…` |
| **D9** | 프로바이더별 제약을 적어 둔 **데이터** | **의도적으로 조건부인 사실** | `assets/k8sclusterinfo.yaml` · `assets/networkinfo.yaml` |

D9는 성격이 다릅니다. 앞의 여덟은 코드에서 읽어 낸 것이지만, D9는 프로젝트가 **CSP마다
다르다는 것을 알고 표로 빼 둔 자리**입니다. 그래서 조건과 개수가 이 층에서 가장 정확하게
읽히고, 우리가 추정할 필요가 없습니다.

D4와 D6이 한 자리에서 갈립니다. 동적 경로는 없으면 만들고(D6), `onDemand`가 꺼져
있으면 *"SecurityGroup must exist when onDemand is disabled"*로 실패합니다(D4).
**의존은 어느 쪽이든 강제이고, 다른 것은 누가 그것을 충족시키는가뿐입니다.**

### 3.3 축이 셋이다 — (자원쌍 × 생성 경로 × CSP)

**경로.** 같은 자원도 만드는 방법마다 요구가 다릅니다.

| | `K8sNodeGroupReq`(명시) | `K8sNodeGroupDynamicReq`(동적) |
|---|---|---|
| `image` | 선택 | **필수** |
| `spec` | 선택 | **필수** |

동적 경로는 추천기가 고를 자리를 사용자가 정해 줘야 하므로 오히려 더 요구합니다.
경로를 뭉개면 *"필수인가"*에 답이 없습니다. 관측된 경로는 `create` · `dynamic` ·
`register` · `snapshot` · `operation` 다섯입니다.

**CSP.** `RequiredCSPResourceForSqlDB`가 이름 자체로 진술합니다.

| CSP | sqlDb가 요구하는 것 |
|---|---|
| **aws** | `vNetID` + `subnet1ID` + **`subnet2ID`(다른 AZ)** |
| **azure** | `resourceGroup`만 — **vNet을 요구하지 않는다** |
| **ncp** | `subnetID` 하나 |

같은 축의 다른 증거가 `assets/k8sclusterinfo.yaml`에 **데이터로** 있습니다.

| CSP | 서브넷 최소 | 생성 시 노드그룹 | 이미지 지정 |
|---|---:|---|---|
| **aws** | **2** | 불가 | 필요 |
| azure | 1 | 가능 | 불필요 |
| gcp | 1 | 가능 | 필요 |
| alibaba · tencent | 1 | 불가 | 필요 |
| nhn · ncp · ibm | 1 | 가능 | ncp·ibm 불필요 |

`nodeGroupsOnCreation`은 **시간 양상**입니다 — 노드그룹을 클러스터와 **함께** 만들어야
하는가, 만든 **뒤에** 붙여야 하는가. 순서가 아니라 동시성의 문제라 기존 어휘로는
적을 자리가 없었습니다.

`assets/networkinfo.yaml`이 같은 층의 다른 표입니다(CSP 10종).

| CSP | vNet prefix | subnet prefix | 서브넷 예약 IP | VPN 게이트웨이 서브넷 |
|---|---|---|---:|---|
| aws | /16–/28 | /16–/28 | (미기재) | — |
| azure | /8–/29 | /8–/29 | 5 | **필수** · 전용 이름 `GatewaySubnet` · 최소 /27 |
| gcp | — | /8–/29 | (미기재) | — |
| alibaba | /8–/28 | /16–/29 | 4 | — |
| ibm | /9–/28 | /9–/29 | 5 | — |
| ncp | /16–/28 | /16–/28 | (미기재) | — |
| nhn | /8–/24 | /8–/28 | (미기재) | — |
| tencent | /12–/28 | /16–/29 | (미기재) | — |
| kt · openstack | (미기재) | (미기재) | (미기재) | — |

**azure의 VPN 게이트웨이 서브넷이 간선 하나를 더 만듭니다** — `vpn → subnet`(azure 한정,
필수). 코드 쪽 증거와도 맞물립니다: `AzureSpecificProperty.GatewaySubnetCidr`가 azure
분기에만 있습니다. 스키마만 봤으면 *"vpn은 vNet만 참조한다"*로 끝났을 자리입니다.

미기재 칸을 빈칸으로 남긴 것은 그쪽 자산의 공백이지 우리 조사의 공백이 아닙니다. aws가
서브넷당 IP를 5개 예약한다는 것은 널리 알려진 사실인데 이 표에는 없습니다 — **원본이
비어 있으면 우리도 비워 둡니다.**

### 3.3.1 그런데 CSP 특화 데이터는 담지 않는다 (2026-07-29 결정)

위 표들은 **조사 결과이지 산출물이 아닙니다.** 기준은 cb-tumblebug의 **벤더 중립
코어**이고, 프로바이더별 상세(CIDR 범위·예약 IP·k8s 버전 목록·`nodeImageDesignation`
등)는 담지 않습니다. 담기 시작하면 코어 모델이 CSP 카탈로그가 되고, 그건 이 저장소가
범위를 좁힌 이유 그 자체입니다.

**남기는 것은 간선 다섯에 붙는 조건뿐입니다 — 빼면 간선이 거짓이 되는 것들.**

| 남긴 조건 | 빼면 무슨 거짓이 되나 |
|---|---|
| `k8sCluster→subnet` 최소 개수 | aws의 **2**가 1로 읽힌다 |
| `vpn→subnet` (azure) | 간선이 통째로 사라진다 — azure에 없다고 말하게 된다 |
| `sqlDb→vNet` (aws) | azure가 요구하지 않는 것을 요구한다고 적게 된다 |
| `sqlDb→subnet` + 최소 개수 (aws) | **서로 다른 AZ의 둘**이라는 배치 조건이 사라진다 |
| `securityGroup→vNet` | 지금 우리 KB가 이미 저지른 거짓(§6) |

경계인 **aws·azure·gcp**로 한정하고, 그 밖 프로바이더의 조건 6건은 관측했으나 담지
않았습니다(`droppedCspObservations`에 무엇을 버렸는지 남습니다). 부족분이 드러나면 그때
근거와 함께 제한적으로 되살립니다 — 원본은 캐시에 그대로 있어 복원 비용은 파싱뿐입니다.

**순서로도 맨 뒤입니다.** CSP 특화는 벤더 중립 코어가 다 선 다음에 손대는 층입니다.

### 3.4 초안에서 기각한 기준 둘

**남겨 두는 이유**: 그럴듯해서 처음에 채택했고, 실제로 틀린 답을 냈습니다.

**(가) 순서는 의존이 아니다.** 운영 스크립트의 선형 순서에서 쌍을 만들었더니
`image→vNet` · `spec→securityGroup` 같은 **거짓 간선 10개**가 나왔습니다. image·spec
등록은 네트워크와 무관한 카탈로그 작업이고 순서는 편의입니다. *A 다음에 B가 온다*가
*B가 A를 요구한다*를 함의하지 않습니다. **D8은 다른 층이 이미 제안한 간선에만 붙는
보강 층으로 내렸습니다.**

**(나) 연산의 인자는 생성 의존이 아니다.** `AttachDetachDataDiskReq.dataDiskId`가
필수라는 이유로 `node→dataDisk`를 필수로 판정했었습니다. 디스크를 붙이는 요청이 디스크
id를 요구하는 것은 **연산의 인자**이지 *"노드가 디스크를 필요로 한다"*가 아닙니다.
`operation` 경로를 필수 판정에서 제외했고, 판정이 필수 → 선택으로 바뀌었습니다.

둘 다 "관측했으니 사실이다"와 "관측한 것이 그 사실이다"의 차이입니다.

### 3.5 포화 — 미판정 29 stem 전수 분류

| 분류 | stem | 판정 |
|---|---|---|
| CSP 쪽 식별자 | `cspresource`(30) · `csp`(9) · `i`(10, `IId`) · `resource` · `parentresource` | 자원 참조가 아니다 — CSP가 부여한 id |
| 자격·테넌시 | `accesskey` · `client` · `tenant` · `subscription` · `project` · `publickeytoken` | 연결 설정 |
| 스칼라 속성 | `name` · `version` · `zone` · `region` · `os` · `sku` · `pricing` · `system` | 값이지 참조가 아니다 |
| 실행 단위 | `job` · `task` · `request` · `xrequest` | 비동기 작업 id |
| 템플릿 | `vnettemplate`(3) · `sgtemplate`(3) | **청사진**이지 자원이 아니다 |
| 사용자 | `vmuser` · `refnameor` | 계정·이름 규약 |
| **선택 포인터** | `representativenode` · `representativenodegroup` | **자원을 가리키지만 의존이 아니다** — 이미 만들어진 것 중 대표를 고르는 값 |

마지막 줄이 이 표를 남기는 이유입니다. 규칙을 느슨하게 했으면 간선으로 들어왔을
것이고, 그러면 *"infra가 node를 요구한다"*가 아니라 *"infra가 대표 node를 지목한다"*를
의존으로 적게 됩니다.

---

## 4. 전수 적용 결과 — 간선 39

층이 겹칠수록 근거가 두껍습니다.

| 층 수 | 간선 | 뜻 |
|---:|---:|---|
| 5 | 3 | `node → vNet · sshKey · securityGroup` — 스키마·런타임·삭제보호·자동생성·CSP가 **모두** 말한다 |
| 3 | 7 | |
| 2 | 7 | |
| 1 | 22 | 근거가 한 층뿐 — **여기가 다음 조사 대상이다** |

### 4.1 층이 다섯 겹인 셋

`node`(VM)를 만들려면 `vNet` · `sshKey` · `securityGroup`이 있어야 하고, 이것은
D1(스키마 필수) · D4(런타임 확인) · D5(삭제 보호) · D6(자동 생성) · D7(CSP 계약)에서
**동시에** 관측됩니다. 이 셋이 이 시스템에서 가장 단단한 사실입니다.

`CreateNodeGroupReq`의 필수 참조 전수: `specId` · `imageId` · `vNetId` · `subnetId` ·
`securityGroupIds[]` · `sshKeyId`. `dataDiskIds[]`만 선택입니다.

### 4.2 근거가 한 층뿐인 22개

`fileSystem→vNet`(D7만) · `nlb→vNet`(D7만) · `publicIp→*`(D7만) · `vNic→*`(D7만) ·
`sqlDb→vNet·subnet`(D2만) · `vpn→vNet`(D2만) · `globalDns→infra`(D1만) …

**두 부류로 갈립니다.** cb-spider에만 있는 자원(`fileSystem`)이나 cb-tumblebug이
얇게 감싼 자원(`publicIp`·`vNic`)은 층이 얕은 것이 정상입니다. 반면 `nlb→vNet`은
cb-spider가 *"Owner VPC IID"*로 **필수**라고 적었는데 cb-tumblebug의 `NLBReq`에서는
`VNetId`가 **주석 처리돼 있습니다**(경로가 `/infra/{infraId}/nlb`라 부모에서 온다).
이건 얕은 것이 아니라 **층 사이에 번역이 끼어 있는 자리**이고, 우리가 지금
`nlb→node`로 적어 둔 것이 여기서 어긋납니다.

### 4.3 나가는 의존이 없는 자원 — 넷

프로비저닝 자원 17종 중 아무것도 요구하지 않는 것은 `vNet` · `sshKey` · `infra` ·
`objectStorage`입니다(카탈로그 `spec`·`image`와 논리 단위 `nodeGroup`은 셈에서 뺍니다).

앞의 셋은 성격상 그렇습니다 — `vNet`은 네트워크의 뿌리이고, `sshKey`는 연결 설정만
받으며, `infra`는 담는 그릇이라 안에 든 노드가 요구할 뿐 자신은 요구하지 않습니다.

**`objectStorage`만 성격이 다릅니다.** 요청이 `bucketName` + `connectionName`뿐이라
네트워크에 매이지 않고, 그러면서 뿌리도 그릇도 아닌 **실물 자원**입니다. 배포
계획에서 다른 무엇도 기다리지 않고 만들 수 있는 유일한 자원이라는 뜻입니다. 빈 결과도
결과라서 적어 둡니다.

---

## 4.4 그래서 답 — 자원 하나를 고르면 무엇이 딸려오나

표를 만든 목적이 이것입니다(과제 문제 ②). 답은 목록이 아니라 **절차**이고,
`app/core/cloudkb/graphkb/tumblebug_closure.py`에 있습니다.

```
1. 앵커에서 필수 창출 간선만 따라간다.
2. `operation` 경로는 따라가지 않는다 (연산의 인자는 생성 의존이 아니다).
3. CSP 조건표가 벤더 중립 판정을 **덮는다**.
4. 카탈로그(spec·image)는 만들 것이 아니라 **고를 것**으로 분리한다.
5. D6(자동 생성)이 붙은 것은 cb-tumblebug이 채운다 → **결정 목록에서 뺀다.**
```

**5번이 이 절차의 값입니다.** 딸려오는 것을 세는 것만으로는 *"그래서 내가 무엇을
정해야 하는데"*에 답하지 못합니다.

| 앵커 | CSP | 딸려오는 것 | **사람이 정할 것** |
|---|---|---|---|
| `node`(VM) | aws | infra · vNet · subnet · securityGroup · sshKey (+ spec·image 선택) | **infra 하나** — 나머지 넷은 자동 생성 |
| `k8sCluster` | aws | vNet · **subnet ×2** · securityGroup | 셋 다 |
| `k8sCluster` | azure | vNet · subnet ×1 · securityGroup | 셋 다 |
| `sqlDb` | aws | vNet · **subnet ×2**(다른 AZ) | 둘 다 |
| `sqlDb` | azure | **없음** | 없음 |
| `vpn` | azure | infra · vNet · **subnet**(GatewaySubnet) | 셋 다 |
| `vpn` | aws | infra | infra |
| `objectStorage` | 전부 | **없음** | 없음 |

같은 `sqlDb`가 aws에서는 셋을 끌고 오고 azure에서는 빈손입니다. 같은 `vpn`이 azure에서만
서브넷을 끌고 옵니다. **연계 리소스 군은 자원의 성질이 아니라 (자원 × CSP)의
성질입니다** — 이것이 이 조사의 결론이고, 하나의 목록으로 적을 수 없는 이유입니다.

근거도 함께 나옵니다. `node`의 `vNet`이 aws에서는 *"node, securityGroup, subnet 때문에"*
딸려오는데 azure에서는 *"node, subnet 때문에"*입니다 — azure는 SG를 VPC에 묶지 않아서고,
그 차이가 출력에 그대로 보입니다.

---

## 5. 외부 표준과의 양방향 매핑

우리 어휘가 우리만의 것이 아님을 보이는 절입니다. 반대로, 기존 표준이 **못 적는 것**도
같이 적습니다.

| 우리 | TOSCA | CloudFormation | Terraform | UML | Kubernetes |
|---|---|---|---|---|---|
| D1 필수 참조 | `requirements` + `occurrences: [1,1]` | `Ref`/`GetAtt` 암시 의존 | 보간 참조(암시) | 연관 + 다중도 `1` | — |
| D2 선택 참조 | `occurrences: [0,1]` | 〃(선택 속성) | 〃 | 다중도 `0..1` | — |
| D3 경로 중첩 | `HostedOn` | **적을 자리가 없다**(평면) | — | **합성**(채운 마름모) | `ownerReferences` |
| D5 삭제 보호 | — | 스택 의존 순서 | 의존 역순 파괴 | — | `finalizers` |
| 카디널리티 `1..N` | `occurrences: [1,UNBOUNDED]` | 리스트 속성 | `list(string)` | 다중도 `1..*` | — |
| `dataDisk↔node` | `AttachesTo` | — | `aws_volume_attachment` | 연관 | — |

**어느 표준도 못 적는 것이 셋 있습니다.**

1. **생성 경로별 차이** — 같은 자원쌍이 `create`에서는 선택, `dynamic`에서는 필수.
   TOSCA의 `occurrences`는 노드 타입에 붙지 생성 방법에 붙지 않습니다.
2. **CSP 조건부** — `sqlDb→vNet`이 aws에서는 참이고 azure에서는 거짓. Terraform은
   프로바이더별로 리소스 타입 자체가 달라서 이 문제가 생기지 않고(대신 이식성이 없고),
   TOSCA는 추상 노드로 덮어 조건을 지웁니다.
3. **시간 양상** — `nodeGroupsOnCreation`(함께 만들어야 하는가). CloudFormation의
   `DependsOn`은 순서만 말하고 **동시성 요구**를 못 적습니다.

이 셋이 이 조사가 기존 표준에 더하는 부분입니다. 셋 다 **D9(자산 데이터)에서 가장
선명하게 읽힌다**는 것이 우연은 아닙니다 — 프로젝트가 "CSP마다 다르다"를 이미 알고
표로 뺀 자리이기 때문입니다. 표준이 못 적는 것을 구현체는 어차피 적어야 했습니다.

---

## 6. 우리 지식베이스와의 차이

현재 `core-graph`: 노드 13 · 간선 19. 이번 분석: 자원 21 · 간선 39.

| | 건수 | 예 |
|---|---:|---|
| **우리에게 없는 간선** | **21** | `sqlDb→vNet·subnet` · `vpn→infra·vNet·subnet(azure)` · `nlb→infra·vNet` · `vNic→vNet·subnet·securityGroup·node` · `publicIp→node·vNic` · `globalDns→infra` · `fileSystem→vNet` · `dataDisk→node` · `k8sCluster→image·spec` |
| **우리에게만 있는 간선** | 1 | `nlb→node` — §4.2의 번역 자리 |
| **필수 판정이 다른 것** | 4 | `customImage→node` · `k8sNodeGroup→image·spec·sshKey` |
| **근거 등급이 거짓인 것** | 1 | `securityGroup→vNet` = `required · stated` — 실제로는 **CSP 조건부**이고 TB 스키마에서는 선택 |

`node→dataDisk`는 우리가 **선택**으로 적어 둔 것이 맞았습니다. 이번 초안이 잠깐 필수로
뒤집었다가 §3.4(나)로 되돌려 다시 일치했고, 그래서 위 표에 없습니다. 기존 판정이 옳았던
자리도 적어 둡니다 — **차이 목록만 남기면 우리 KB가 늘 틀렸던 것처럼 읽힙니다.**

---

## 7. 타당성 위협

| | 위협 | 대응 |
|---|---|---|
| T1 | **소스를 읽은 것이지 돌려 본 것이 아니다.** 코드가 그렇게 적혀 있다고 CSP가 그렇게 동작한다는 보장은 없다 | 층을 갈라 둔 것이 대응이다 — D7(CSP 계약)과 D1(TB 스키마)이 어긋나는 자리를 지우지 않고 남겼다. 실행 검증은 이 조사의 밖이고, 그렇다고 적었다 |
| T2 | **버전에 매인다.** v0.12.25에서 참인 것이 다음 태그에서 거짓일 수 있다(실제로 v0.11.8→v0.12.25에서 자원이 5개 늘고 `mci`가 개명됐다) | 핀을 `_source`에 박았다. 자원 목록과 산출물이 어긋나면 빌드가 죽게 하는 것이 다음 단계다 |
| T3 | **규칙 기반 추출은 이름 규약에 의존한다.** `*Id` 꼴이 아닌 참조는 안 잡힌다 | 미판정을 버리지 않고 전수 분류했다(§3.5). 그럼에도 이름에 `Id`가 없는 참조는 원리적으로 못 잡는다 — `SubnetInfoList`처럼 **구조체를 통째로 안는** 형태가 그 예이고, 이건 §4의 합성 관계로 따로 잡았다 |
| T4 | **cb-spider 층을 인터페이스만 읽었다.** 드라이버 구현이 인터페이스보다 더/덜 요구할 수 있다 | 인용을 인터페이스로 한정해 적었다. 드라이버 전수는 별도 라운드 |
| T5 | **`operation`/`register` 경로의 분류가 우리 판단이다.** 구조체 이름으로 갈랐다 | 갈림의 근거를 코드 주석·경로와 함께 적었고, 갈래가 틀리면 필수 판정이 바뀌는 자리를 §3.4에 명시했다 |
| T6 | **`nodeGroup`을 자원으로 셀 것인가**가 정의 문제다 | 독립 엔드포인트가 없다는 관측을 근거로 `logical`로 분류하고 이유를 남겼다 |

---

## 8. 이 결과가 지식베이스에 뜻하는 것

1. **`core-graph`의 간선을 불리언에서 구조로 바꿔야 한다.** 지금 `required` 한 칸으로는
   §3.3의 세 축을 적을 수 없고, `securityGroup→vNet`처럼 **거짓을 stated 등급으로**
   싣게 된다.
2. **경계가 16 → 21종으로 늘고**, 그중 `publicIp`·`vNic`·`globalDns`·`fileSystem`은
   범위 계획이 세지 않았던 것이다. 범위 문서 §2.2를 이 결과로 갱신해야 한다.
3. **D9의 표 셋은 그대로 판정 규칙이 된다** — `sqlDb`의 CSP 조건부 요구(aws는 **다른
   AZ의 서브넷 2개**), k8s의 프로바이더별 서브넷 수, 그리고 vNet/subnet의 prefix 길이
   범위와 azure VPN 게이트웨이 서브넷. 우리가 "관리형은 얕게"로 정한 깊이 안에서도
   **줄 수 있는** 정보이고, 값이 우리 추정이 아니라 인용이다.
4. **연계 리소스 군(과제 문제 ②)의 답이 굳어진다.** VM 하나를 요청하면 따라오는 것이
   층 다섯이 동시에 말하는 셋(vNet·sshKey·securityGroup) + 스키마가 요구하는 셋
   (subnet·spec·image)이고, 그 근거를 이제 자리까지 짚어 인용할 수 있다.
