# 과제 세부 목표 5축별 소스 조사 재구성 (2026-07-29)

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md). 이 문서는 작성 시점의
> 스냅샷이고 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을
> 시작하지 말 것.** 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

`research.md`의 목표 2는 **"클라우드 환경 특성(클라우드 인스턴스 성능, 비용 등),
클라우드 리소스의 특성(리소스 용량, 리소스 의존성 등)을 고려한 클라우드 네이티브 환경
가이드라인 제공"**입니다. 이 한 문장이 요구하는 조사 영역은 다섯입니다.

```
① 클라우드 인스턴스 성능
② 클라우드 인스턴스 비용
③ 클라우드 리소스 용량
④ 클라우드 리소스 의존성(연결 관계)
⑤ 특정 클라우드 리소스와 연계되는 리소스 군
```

기존 두 문서는 **소스를 기준으로** 쓰여 있습니다.

- `kb-sourcebook-2026-07-28.md` — 재료 47종이 **무엇을 담았나**
- `kb-source-atlas-2026-07-29.md` — 재료 47종이 **어떻게 그 모양이 되었나**

이 문서는 같은 재료를 **과제 목표의 조사 영역을 기준으로** 다시 세웁니다. 축 하나를
답하려고 어떤 소스를 왜 찾았고, 그 소스에서 무엇을 취하고 무엇을 버렸고, 그래서 그
축이 지금 어디까지 답할 수 있는지를 축마다 끝까지 씁니다.

## 축마다 같은 순서로 씁니다

```
X.0  정의            무엇을 그 축이라 부르는가 · 레코드 하나가 무엇인가 ·
                     무엇이 그 축이 **아닌가**(경계) · 필드의 정확한 뜻
X.1  답해야 할 질문   그 정의가 실제 질문으로 어떻게 나뉘는가
X.2  시작 상태의 공백  조사가 필요했던 이유 — 실측
X.3~ 소스별           왜 필요했나 → 어떻게 쓰나(함정 포함) → 무엇이 나왔나 → 못 주는 것
X.n  커버리지         지금 답할 수 있는 것 (실측 표)
     일부러 안 하는 것
     타당성 위협
```

**정의(`X.0`)를 맨 앞에 둔 이유**: 정의 없이 커버리지 숫자만 읽으면 **무엇을 세었는지
모른 채 크기만 비교**하게 됩니다. 실제로 축마다 "레코드 하나"의 뜻이 다릅니다 — ①은
(프로바이더, 리전, 스펙)이고 ③은 (타입, 속성, 종류, 조건)입니다. **141,377과 67,034를
나란히 놓는 것은 서로 다른 단위를 비교하는 것**입니다.

## 이 문서를 쓰면서 실제로 센 것

인용한 수치는 **커밋된 산출물(`data/*.json.gz`)에서 직접 센 값**입니다. 소스 문서의
값을 옮겨 적지 않고 전부 다시 셌고, **어긋난 두 곳은 8부에 적었습니다.** 필드의 뜻은
지어내지 않고 **각 KB의 `schema.json`과 모델 docstring**에서 옮겼습니다.

**측정 대상을 어디로 잡았나.** `data/tumblebug-perf.json.gz` 하나가 이 시점에 작업
트리에서 수정 중이었고(`git status: M`) **그 변경은 되돌려지는 중**입니다. 그래서 성능
축은 **커밋된 상태(`git show HEAD:…`)를 기준으로 셌습니다** — 되돌려질 중간 상태를
적으면 문서가 나오는 순간부터 거짓이 됩니다. 나머지 산출물은 작업 트리와 커밋이
같습니다. (처음에 작업 트리를 재고 아틀라스와 어긋났다고 적었던 것을 8.2에 정정으로
남깁니다.)

---

# 0부 — 다섯 축이 공유하는 것

축별로 들어가기 전에, **다섯 축의 정의**와 **전부에 적용되는 공통 규율**입니다. 축마다
반복하지 않기 위해 여기 한 번만 씁니다.

> **번호 규약**: 각 부의 **`X.0`은 그 축의 정의**입니다 — 무엇을 그 축이라 부르는지,
> 레코드 하나가 무엇인지, 무엇이 그 축이 **아닌지**. 정의 없이 커버리지 숫자를 읽으면
> 무엇을 세었는지 모른 채 크기만 비교하게 됩니다.

## 0.1 다섯 축의 정의 — 한 표로

| 축 | 정의 | 관측 단위(레코드 하나) | 답의 성격 |
|---|---|---|---|
| **① 인스턴스 성능** | 인스턴스 한 종류(스펙)를 켰을 때 그것이 낼 수 있는 처리 능력을 이루는, **벤더가 발표했거나 실측으로 확인된 측정 가능한 속성들** | (프로바이더, 리전, 스펙) 하나 | 값 — 비교는 프로바이더 안에서만 |
| **② 인스턴스 비용** | 같은 스펙을 **어떤 조건으로 쓰느냐에 따라 달라지는 단가**. 조건이 값의 일부다 | (프로바이더, 리전, 스펙) 하나 · 관리형은 (아키타입, 리전, 미터) | 단가이지 **총액이 아님** |
| **③ 리소스 용량** | 리소스 타입의 **속성 하나에 걸린 한계** — 얼마까지 · 바꿀 수 있나 · 언제 적용되나 | (타입, 속성, 종류, 조건집합) 하나 | 판정 — 가능 / 불가 / **조건부** |
| **④ 리소스 의존성** | 리소스 타입 **둘 사이의 방향 있는 관계**. 방향은 **의존하는 쪽 → 의존 대상** | (from, to, 종류, 경유 속성) 하나 | 순서·포함·참조·동치 |
| **⑤ 연계 리소스 군** | 한 리소스를 중심으로 **함께 만들어지거나(번들) 함께 나타나는(동시 출현)** 리소스들의 묶음 | 앵커 하나 + 멤버 집합 / (앵커, 타입) 쌍 | **관찰이지 규칙이 아님** |

## 0.2 왜 다섯인가 — 축을 가른 기준

두 물음의 곱입니다. **대상이 무엇인가**와 **사실이 어디에 붙는가**.

```
                        하나에 붙는 사실     둘 사이의 사실     여럿의 묶음
인스턴스 (살 수 있는 상품)   ① 성능 · ② 비용        —                —
리소스 타입 (만들 수 있는 부품) ③ 용량            ④ 의존성          ⑤ 리소스 군
```

**인스턴스 쪽에 ④⑤가 없는 것이 우연이 아닙니다.** 인스턴스는 **카탈로그의 항목**이지
배포 그래프의 노드가 아닙니다. 인스턴스를 실제로 만들 때 생기는 관계는 그것이 속한
**리소스 타입**(`AWS::EC2::Instance`)에 붙습니다. 즉

```
t3.micro                  스펙   — ①②의 대상. 카탈로그의 상품 이름
aws::AWS::EC2::Instance   타입   — ③④⑤의 대상. 배포 그래프의 부품 종류
```

**둘은 다른 층의 이름이고 이 문서는 섞지 않습니다.** 조인 키도 다릅니다(0.3).
`t3.micro`의 의존성을 묻는 질문은 실은 `AWS::EC2::Instance`의 의존성 질문입니다.

**경계 사례 — 왜 이건 여기고 저건 저기인가.**

| 사실 | 어느 축 | 왜 |
|---|---|---|
| 이 인스턴스가 낼 수 있는 디스크 IOPS | **①** | 인스턴스가 가진 능력 |
| 이 볼륨에 넣을 수 있는 최대 IOPS | **③** | 리소스 타입 속성의 한계 |
| 리전 간 왕복 지연 | **①** | 인스턴스 하나가 아니라 **배치**의 성능 |
| 서브넷 예약 IP 개수 | **③** | 용량 계산의 상수(최소 규모) |
| 관리형 서비스 단가 | **②** | 인스턴스는 아니지만 **같은 결정의 비용 면** |
| AVM 모듈의 `dependsOn` 순서 | **④** | 무엇이 먼저인가 |
| AVM 모듈이 배포하는 집합 | **⑤** | 무엇이 함께인가 |
| 탄소 배출·지원 종료일 | **어느 축도 아님** | 리전·버전 선택이라는 별개 결정 → 6부 |

마지막에서 둘째 줄이 중요합니다 — **`avm-bicep` 한 소스가 두 파서로 갈리는 이유**가
이 경계입니다(4.7 · 5.7).

## 0.3 공통 낱말 — 이 문서에서 쓰는 뜻

| 낱말 | 정의 | 예 |
|---|---|---|
| **프로바이더**(provider) | 클라우드 사업자. **우리가 정규화하는 키라 enum으로 잠급니다** — 새 값이 오면 검증이 실패해 조용한 드리프트를 막습니다 | 비용 10곳 · 성능 4곳 |
| **스펙**(spec) | 살 수 있는 **인스턴스 종류 하나**. CSP 원본 이름을 그대로 씁니다 | `t3.micro` · `Standard_B12ms` · `n2-highmem-8` |
| **스펙 id** | `{provider}+{region}+{spec}` — **①②의 조인 키** | `aws+ap-northeast-2+t3.micro` |
| **리소스 타입** | 만들 수 있는 **부품의 종류** | `AWS::EC2::Volume` |
| **`type_id`** | `{provider}::{타입이름}` — **③④⑤의 조인 키.** 형식을 아는 곳은 `kbcommon/type_ids.py` **하나**입니다 | `aws::AWS::EC2::Volume` |
| **층**(layer) | 타입 이름이 사는 곳 — `core`(도구 중립 어휘) · `vendor`(회사 타입) · `app`(앱 개념) | `core::vNet` · `azure::Microsoft.Network/virtualNetworks` · `app::relationalDatabase` |
| **앵커**(anchor) | 리소스 군이 **무엇을 중심으로 도는가.** 대등한 여럿이면 `null` | `core::vm` |
| **리전**(region) | tumblebug 표기. **원본 표기를 남기고 조인 키만 소문자로** 맞춥니다 | `ap-northeast-2` · `KR1` |
| **핀**(pin) | 소스를 시점에 못 박는 방법 — `태그` · `커밋` · **`지문`**(고정 불가) · `동봉` | 23 · 18 · 5 · 1 |

> **`type_id`를 한 곳에서만 만드는 이유 — 실제로 조인이 깨진 적이 있습니다.** ARM 타입
> 이름은 대소문자를 구분하지 않아 **Azure 자신도 일관되게 적지 않습니다**(API 버전마다
> 표기가 다르고, `index.json` 전체에서 대소문자만 다른 타입이 **71종**입니다). graphkb와
> capacitykb가 각자 대표 표기를 고르다가 `Microsoft.Compute/...`와
> `microsoft.Compute/...`로 갈려 **조인이 실패했습니다.** 문자열만 보고는 어느 쪽이
> "옳은" 표기인지 알 수 없으므로 **모든 KB가 같은 `index.json`에서 같은 규칙으로**
> 대표를 고릅니다 — 소문자로 묶고 최신 안정 버전의 표기를 대표로. 소스에 핀이 박혀
> 있으므로 이 선택은 빌드마다 재현됩니다.

## 0.4 출처는 세 층위로 적힌다

이 문서의 모든 레코드 실물에는 **어느 산출물 파일에서 뽑았고 그 파일이 어느 소스에서
나왔는지**를 붙였습니다. 붙일 수 있는 이유는 **산출물 자신이 세 층위로 출처를 담기**
때문입니다.

| 층위 | 어디에 적히나 | 무엇을 말하나 |
|---|---|---|
| **파일** | `_source` 배열 | 이 파일이 **어느 소스에서** 나왔나 — 소스 키 · 핀 종류/값 · **sha256** · 받은 시각 |
| **레코드** | `evidence` + `basis` (Node는 `source`) | 이 한 줄이 **소스의 어느 부분**에서 나왔고 **어떻게 아는가** |
| **칸** | `sustainedCpu.evidence` · `hardwareEvidence` · `azureSizesEvidence` · `gcpSeriesEvidence` | **본체와 다른 소스에서 온 칸**의 근거 |

세 번째가 필요한 이유는 **한 레코드가 여러 소스의 병합**일 수 있기 때문입니다. 성능
레코드가 그렇습니다 — 몸통은 미러(§1)에서 오고 하드웨어 칸은 §28, azure의 두 칸은 §29,
gcp의 여러 칸은 §30에서 옵니다. **레코드 하나에 evidence 하나만 두면 어느 칸이 어느
소스인지 말할 수 없습니다.**

> **파일 단위 출처의 실물** — `_source` 한 항목은 이렇게 생겼습니다.
>
> ```
> aws-limits.json.gz  _source
>   aws-price-list | tag 20260721012550 | sha256 2212ea3e26eb…
>   botocore       | tag 1.43.52        | sha256 04223e511d44…
> ```
>
> **소스가 둘인 것이 그 자체로 사실**입니다 — 이 파일의 20건은 **두 소스가 같은 값을
> 말했을 때만** 담긴 것이라(3.4), 출처가 하나면 성립하지 않는 산출물입니다.

### 받는 주소와 보는 주소는 다릅니다

등록부(`kbcommon/sources.py`)에 적힌 URL은 **기계가 받는 주소**입니다. `codeload`의
tar.gz나 `raw.githubusercontent.com`은 브라우저로 열어도 **압축 파일이거나 평문
덩어리**라 사람이 확인하기 어렵습니다. 그래서 이 문서는 레코드마다 **사람이 눈으로 볼
수 있는 주소**를 함께 답니다.

```
받는 주소  https://codeload.github.com/cloud-barista/cb-tumblebug/tar.gz/refs/tags/v0.12.25
보는 주소  https://github.com/cloud-barista/cb-tumblebug/tree/v0.12.25/init/templates
```

**변환은 기계적이고, 핀은 그대로 유지됩니다** — 태그·커밋 SHA가 URL에 그대로 남아
있으므로 **보는 주소도 우리가 쓴 것과 같은 시점**을 가리킵니다.

| 받는 주소 모양 | 보는 주소로 바꾸는 규칙 |
|---|---|
| `raw.githubusercontent.com/{o}/{r}/{ref}/{path}` | `github.com/{o}/{r}/blob/{ref}/{path}` (디렉터리는 `tree`) |
| `codeload.github.com/{o}/{r}/tar.gz/refs/tags/{tag}` | `github.com/{o}/{r}/tree/{tag}` |
| `codeload.github.com/{o}/{r}/tar.gz/{sha}` | `github.com/{o}/{r}/tree/{sha}` |
| `github.com/{o}/{r}/archive/refs/tags/{tag}.tar.gz` | `github.com/{o}/{r}/tree/{tag}` |
| `media.githubusercontent.com/media/{o}/{r}/{ref}/{path}` | `github.com/{o}/{r}/blob/{ref}/{path}` |

**보는 주소는 각 소스를 설명하는 자리에 함께 둡니다** — 부록으로 몰면 원본 형태와
주소가 떨어져서, 읽다가 매번 뒤로 넘어가야 합니다. 소스 절마다 맨 앞에 **받는 주소 ·
보는 주소 · 핀/라이선스** 표가 있고, tar.gz로 받는 소스는 **파서가 실제로 읽는
파일·디렉터리까지 내려가는 링크**입니다(저장소 루트만 주면 결국 사람이 직접 찾아
들어가야 하므로). **문서의 주소 전부를 2026-07-29에 HTTP 200으로 확인했습니다.**

**보는 주소를 만들 수 없는 것이 5종 있고, 그게 곧 지문 핀 5종입니다.**

| 소스 | 왜 |
|---|---|
| **§8 `cfn-schema`** | 저장소가 없고 AWS가 **같은 URL에 zip을 덮어씁니다.** 스키마 안의 `sourceUrl`이 저장소를 가리키지만 **그 주소도 지금은 404**입니다(실측). 우리가 쓴 zip인지 확인하는 방법은 **sha256뿐** |
| **§26 · §31** | 살아 있는 API라 **버전 개념이 없습니다.** 열리기는 하지만 **지금 값이 우리가 받은 값이라는 보장이 없습니다** |
| **§43** | 저장소 없이 렌더링 HTML만 있습니다. **자문 전용이라서만 허용**됐습니다 |
| **§45** | PDF가 항상 **최신본**을 줍니다(`latest/`). 우리가 쓴 판은 sha256으로만 식별됩니다 |

## 0.5 레코드 모델 — 산출물의 모양은 다섯 가지뿐

소스가 47종이어도 도착점은 이 다섯입니다. 축별 상세는 각 부의 `X.0`에 있습니다.

**Constraint** (③ 용량) — 속성 하나에 걸린 제약

```json
{"type_id": "aws::AWS::EC2::Volume", "property": "Size", "kind": "max",
 "value": 16384, "value_type": null, "unit": "GiB", "conditional": false,
 "note": null, "evidence": "aws-cross-checked", "basis": "stated",
 "backend": null, "conditions": [{"property": "VolumeType", "op": "eq", "value": "gp2"}]}
```

> **출처** — `data/aws-limits.json.gz` ← §10 `botocore`(태그 `1.43.52`) **×**
> §25 `aws-price-list`(버전 URL `20260721012550`). `evidence`가 `aws-cross-checked`인
> 것이 **두 소스가 일치했다는 표식**입니다.

**Node / Edge** (④ 의존성) — 타입과 타입 사이

```json
{"id": "aws::AWS::ACMPCA::Certificate", "layer": "vendor", "provider": "aws",
 "kind": "resource_type", "display_name": "AWS::ACMPCA::Certificate",
 "source": "cloudformation-registry"}

{"from": "aws::AWS::ACMPCA::Certificate", "to": "aws::AWS::ACMPCA::CertificateAuthority",
 "type": "references", "via_property": "CertificateAuthorityArn", "required": true,
 "cardinality": "one", "evidence": "cdk-oob", "basis": "stated",
 "target_property": "Arn", "reviewed": true}
```

> **출처** — 둘 다 `data/aws-graph.json.gz`이지만 **소스가 다릅니다.** 노드는
> §8 `cfn-schema`(지문 핀, sha256 `83b88800e04b…`)에서 오고 `source` 칸이 그것을
> 밝힙니다. 엣지는 §9 `cdk-oob`(태그 `@aws-cdk/aws-service-spec@v0.1.196`)에서 오고
> `evidence` 칸이 그것을 밝힙니다. **한 파일 안에서 노드와 엣지의 출처가 갈립니다.**

**Bundle / Cooccurrence** (⑤ 리소스 군) — 함께 만들어지는 것 / 함께 나오는 것

```json
{"anchor": "azure::Microsoft.Authorization/roleAssignments",
 "typeId": "azure::Microsoft.Storage/storageAccounts",
 "hits": 50, "samples": 89, "evidence": "aqt-corpus"}
```

> **출처** — `data/aqt-cooccurrence.json.gz` ← §37 `azure-quickstart-templates`
> (커밋 `331d6f394416…`). `evidence`의 `aqt-corpus`가 **코퍼스에서 센 값**이라는
> 표식이고, 그래서 `basis`가 `observed`로 떨어집니다.

**Rule** (③의 최소 규모) — 규모 상수

```json
{"id": "tumblebug::reserved-ips/alibaba", "kind": "reserved_ips", "scope": "alibaba",
 "metric": "reservedIps", "value": 4, "unit": "IPs",
 "evidence": "tumblebug-networkinfo",
 "note": "Number of reserved IPs in the subnet (i.e., the 1st IP address and last 3 …)"}
```

> **출처** — `data/tumblebug-sizing.json.gz` ← §2 `tumblebug-src`(태그 `v0.12.25`)의
> **`assets/networkinfo.yaml`**. 같은 tarball의 다른 파일에서 온 규칙은 evidence가
> 달라집니다(`tumblebug-k8sinfo` · `tumblebug-dynamic`) — **한 소스 안에서도 어느
> 파일에서 왔는지가 라벨로 갈립니다.**

**Doc** (6.1 자문) — 검색되는 산문 한 편. **라이선스와 저작자 표시가 레코드 안에**
들어갑니다 — NOTICE에만 두면 **파일이 저장소를 떠날 때 사라지기** 때문입니다.

> **출처** — `data/pattern-corpus.json.gz` ← §36·§42·§43·§44 /
> `data/aws-pattern-corpus.json.gz` ← §45. 이 축만 레코드가 **`license` ·
> `attribution` · 원문 `url`**을 직접 들고 다닙니다.

그 밖에 **축 전용 레코드**가 있습니다 — ①②의 `specs`, ②의 `records`,
환경축의 `regions`·`pairs`·`products`·`images`·`csps`.

## 0.6 모든 사실에 붙는 두 꼬리표

```
evidence  어느 소스의 어느 부분에서 나왔나        예: cfn-schema, cdk-oob, aqt-corpus
basis     어떻게 아는가 — 셋 중 하나
```

| basis | 뜻 | 판정에 쓰나 | 유보 붙나 |
|---|---|---|---|
| `stated` | 원본이 그렇게 적어 놓았다 | ○ | × |
| `inferred` | 우리가 짐작했다 | 검수됐으면 ○ | ○ |
| `observed` | 사례 뭉치에서 세었다 | **×** | ○ |

`basis`는 `evidence`에서 기계적으로 결정됩니다(`kbcommon/basis.py`). **등록되지 않은
라벨은 `inferred`로 떨어집니다** — 새 라벨이 조용히 사실로 승격되지 않게 하는 장치입니다.

이 세 값의 구분이 다섯 축 전부의 성격을 정합니다. ①②는 대부분 `stated`(벤더가 발표한
값)이고, ③은 `stated`와 `inferred`가 섞이며, ④는 소스에 따라 `stated`/`inferred`가
갈리고, ⑤는 **거의 전부 `observed`**입니다 — 그래서 ⑤는 판정에 쓰지 않습니다.

## 0.7 다섯 단계를 모두 같은 길로 지납니다

```
① 고정  kbcommon/sources.py   "무엇을 받기로 했나" — URL에 태그·커밋이 박혀 있다
② 수집  kbcommon/fetch.py     캐시 + <파일>.provenance.json (sha256·크기·시각·ETag)
③ 파싱  <kb>/parsers/*.py     원본 → 레코드. **여기서 버릴 것을 정한다**
④ 모델  Constraint·Edge·Node·Rule·Bundle + 축 전용 레코드
⑤ 산출  kbcommon/artifact.py  data/<이름>.json.gz — _source·_coverage 동봉
```

①과 ②가 갈리는 자리가 있습니다. **고정할 수 없는 소스**(지문 핀 5종)는 재현이
원리적으로 불가능하고, 남길 수 있는 것은 ②의 sha256뿐입니다. 실제로 캐시된 AWS
zip(2,783,390 B)과 라이브(2,794,161 B)가 이미 달라진 것을 이 기록이 잡았습니다.

## 0.8 축마다 반복해서 나오는 처리 규칙 아홉

47종을 축별로 다시 읽어도 같은 규칙이 반복됩니다. 소스가 달라도 처리가 닮은 이유입니다.

1. **한 방향만 말하는 칸을 양방향으로 읽지 않는다** — `false`가 "아니다"가 아니라
   "그 칸에서 빠졌다"인 경우 (①④)
2. **부재를 주장으로 승격하지 않는다** — 빈칸을 0으로, 목록에 없음을 "최신"으로 (①②③)
3. **값이 하나일 때만 담는다** — 두 소스가 어긋나면 담지 않고 미결로 보고 (①②③)
4. **변별력 없는 칸은 채움률 100%여도 버린다** (①)
5. **원본이 스스로 단 경고는 값과 함께 옮긴다** (③⑤)
6. **전체를 파싱하지 않고 필요한 모양만 집는다 — 그게 안전장치다** (전축)
7. **다리를 건너면 등급이 떨어진다** — 서비스 이름 → 타입 id는 우리 손 검수 (④)
8. **버린 것을 세어서 밝힌다** — 조용한 누락이 구조적으로 불가능하게 (②③)
9. **고정할 수 없으면 기록이라도 남긴다** (전축)

## 0.9 축 × 소스 대응 — 한 소스가 여러 축을 떠받칩니다

47종 중 **9종이 둘 이상의 축**에 쓰입니다. 축별로 소스를 세면 합이 47을 넘는 이유입니다.

| 소스 | ① 성능 | ② 비용 | ③ 용량 | ④ 의존성 | ⑤ 리소스 군 |
|---|:-:|:-:|:-:|:-:|:-:|
| `tumblebug-dump` | ● | ● | | | |
| `tumblebug-src` | | | ● | | ● |
| `cfn-schema` | | | ● | ● | |
| `bicep-types-az` | | | ● | ● | |
| `kcc-crd` | | | ● | ● | ● |
| `aws-price-list` | | ● | ● | | |
| `cyclenerd-gcp-pricing` | ● | ● | | | |
| `avm-bicep` | | | | ● | ● |
| `cb-spider` | | | | ● | ● |

**상류 하나가 틀리면 여러 축이 같이 틀립니다.** 실제로 `tumblebug-dump`의 메모리 단위
변환 버그가 ①과 ②를 동시에 오염시킨 적이 있고(1부 참조), 그래서 그 보정 사실을 값이
아니라 데이터셋 메타데이터에 적습니다.

---

# 1부 — 클라우드 인스턴스 성능

> 과제 원문: *"클라우드 환경 특성(클라우드 인스턴스 성능, 비용 등)"*

## 1.0 정의 — 이 축은 무엇인가

> **클라우드 인스턴스 성능**이란, 인스턴스 한 종류(스펙)를 켰을 때 그것이 낼 수 있는
> 처리 능력을 이루는 속성들 중 **벤더가 발표했거나 실측으로 확인된, 측정 가능한
> 값들**을 말한다. 우리가 계산했거나 점수 하나로 접은 값은 **이 축이 아니다.**

정의의 뒷부분이 이 축의 성격을 정합니다. "성능"이라는 낱말은 자연스럽게 *"그래서 뭐가
더 빠른데?"*로 이어지는데, **그 답은 워크로드를 알아야 나오고 우리는 모릅니다.** 그래서
이 축은 **비교가 아니라 속성**을 담습니다.

### 관측 단위 — 레코드 하나가 무엇인가

**`(provider, region, specName)` 하나가 레코드 하나**이고, `id`가 그 키입니다.

```
id = "aws+ap-northeast-2+t3.micro"
```

두 가지가 스키마에 규율로 박혀 있습니다.

- **성능 신호가 하나도 없는 스펙은 레코드 자체를 만들지 않습니다.** 성능 65,032건이
  비용 73,083건보다 적은 것은 **레코드 부재가 곧 "이 스펙에 대해 아는 것이 없다"**를
  뜻하기 때문입니다.
- **필드 부재 = "모른다"입니다.** 값이 없는 칸은 `0`도 `false`도 `미지원`도 아닙니다.
  `localSsdGB`가 없으면 *"로컬 SSD가 없다"*가 아니라 *"우리가 모른다"*입니다.

### 무엇이 이 축이 **아닌가** — 경계

| 아닌 것 | 어디로 가나 |
|---|---|
| 벤치마크 점수 | **어디에도 안 담습니다** — 정규화 방식이 답을 뒤집습니다(1.4) |
| "A와 B 중 뭐가 빠른가" | 답하지 않습니다 — 워크로드 의존(1.10) |
| 프로바이더 간 성능 비교 | **구조적으로 막았습니다** — 잣대가 다릅니다(ACU는 Azure에만) |
| 이 볼륨에 넣을 수 있는 최대 IOPS | **③ 용량** — 인스턴스의 능력이 아니라 타입 속성의 한계 |
| 가격 대비 성능 | 합성하지 않습니다 — ①과 ②가 **다른 스냅샷**일 수 있습니다(2.4) |
| "동접 1만이면 몇 대" | 답하지 않습니다 — **공개 근거가 없습니다** |

### 용어 — 이 축의 필드가 정확히 무슨 뜻인가

`perfkb/schema.json`이 정의하는 그대로입니다.

| 필드 | 정의 |
|---|---|
| **`sustainedCpu`** | **상시 CPU 성능이 보장되나.** 프로바이더마다 **다른 메커니즘**에서 오므로 값과 함께 근거를 담습니다 — evidence 5종(`aws-burstable-field` · `aws-non-burstable-inferred` · `gcp-shared-cpu-field` · `gcp-dedicated-cpu-inferred` · `azure-family-name`)이고 **basis가 갈립니다** |
| **버스트**(burst) | 크레딧이 있는 동안만 기준선 위로 올라가는 것. **크레딧이 떨어지면 baseline으로 떨어집니다** |
| **`networkIsBurst`** | 광고 대역폭이 버스트인가 — 원문이 `Up to`로 시작하면 `true` |
| **`networkPerformance`** | AWS 원문 그대로 유지(`'Up to 5 Gigabit'` · `'25 Gigabit'`) — 숫자로 접으면 버스트 여부가 사라집니다 |
| **`ebsBaselineMbps`** | 지속 가능한 EBS 대역폭. **`Maximum`이 아니라 이 값이 진짜 성능입니다** |
| **`acu`** | Azure Compute Units. **Azure 내부에서만 비교 가능**하고 37.7%만 채워져 있습니다 |
| **`clockGHz` vs `cpuClockMHz`** | 앞은 미러 카탈로그 값, 뒤는 **실측 프로브 값**(§28). 소스가 다르므로 칸을 나눕니다 |
| **`cpuFamily` vs `cpuModel`** | 마이크로아키텍처 계열(`Sapphirerapids`) vs 정확한 모델명(`Intel(R) Xeon(R) Platinum 8488C`) |
| **`vcpuTenancy`** | vCPU 점유 방식(`dedicated`/`shared`). **`sustainedCpu`로 옮기지 않습니다** — 그건 우리 추론이지 원본이 한 말이 아닙니다 |
| **`gpuCount`** | 장착 GPU 개수. **`0`은 담지 않습니다**(없음과 모름이 섞입니다). GCP `g4`처럼 GPU를 나눠 쓰는 크기는 **`0.25`가 사실이라 정수로 반올림하지 않습니다** |
| **`gpuModel`** | 한 인스턴스에 모델이 섞여 있으면 **목록으로 남깁니다** — 하나로 고르면 짐작이 됩니다 |
| **`maxNics`** | 붙일 수 있는 네트워크 인터페이스 수 |
| **`networkBandwidthMbps`** | 총 네트워크 대역폭. IBM은 카탈로그의 `bandwidth`, Azure는 크기 문서 표의 `Max Network Bandwidth` |
| **`hardwareCheckedAt`** | 하드웨어 사실이 **확인된 날짜.** 잘 안 바뀌는 값이지만 **언제 확인된 것인지 없으면 신선도를 판단할 수 없습니다** |
| **`*Evidence` 3종** | `hardwareEvidence` · `azureSizesEvidence` · `gcpSeriesEvidence` — **본체(cb-tumblebug)와 다른 소스에서 온 칸의 근거를 따로 적는 규약**입니다 |

### 레코드 실물 — 필드가 어떻게 붙어 나오는가

```json
{"id": "aws+ap-northeast-2+t3.micro", "provider": "aws", "specName": "t3.micro",
 "sustainedCpu": {"value": false,
   "note": "Burstable instance — performance drops to baseline once the CPU credits run out.",
   "evidence": "aws-burstable-field", "basis": "stated"},
 "currentGeneration": true, "clockGHz": 2.5, "threadsPerCore": 2.0,
 "networkPerformance": "Up to 5 Gigabit", "networkIsBurst": true,
 "ebsBaselineMbps": 87.0, "ebsMaxMbps": 2085.0,
 "ebsBaselineIops": 500.0, "ebsMaxIops": 11800.0, "bareMetal": false,
 "cpuVendor": "GenuineIntel", "cpuModel": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
 "cpuClockMHz": 2500, "cpuCacheKB": 36608, "cpuCores": 1, "cpuThreads": 2,
 "memorySpeedMHz": 2933, "hardwareCheckedAt": "2025-12-10",
 "hardwareEvidence": "ec2-hardware-probe"}
```

> **출처** — `data/tumblebug-perf.json.gz`. **이 축의 레코드는 여러 소스의 병합**이라
> 파일의 `_source`에 소스가 넷 들어 있습니다(`tumblebug-dump` · `ec2-hardware` ·
> `azure-compute-docs` · `gcloud-machine-types`). **레코드 하나 안에서도 칸마다
> 출처가 갈립니다.**

**칸별 출처 — 위 레코드를 그대로 쪼개면.**

| 칸 | 소스 | 원본의 어느 부분 | 표식 |
|---|---|---|---|
| `id` · `provider` · `specName` | **§1 `tumblebug-dump`**(태그 `v0.12.25`) | `spec_infos` 컬럼 | — |
| `sustainedCpu` | 〃 | `details`의 `BurstablePerformanceSupported` | **`sustainedCpu.evidence`**(칸 안에) |
| `currentGeneration` · `clockGHz` · `threadsPerCore` · `bareMetal` | 〃 | `details`의 해당 키 | — (본체라 표식 없음) |
| `networkPerformance` · `networkIsBurst` | 〃 | `details`의 `NetworkInfo` | — |
| `ebs*` 4종 | 〃 | `details`의 `EbsInfo` | — |
| `cpuVendor`·`cpuModel`·`cpuClockMHz`·`cpuCacheKB`·`cpuCores`·`cpuThreads`·`memorySpeedMHz` | **§28 `ec2-hardware`**(커밋 `4ef36cd2…`) | `manually_fetched_data.json`의 `cpu`·`memory` | **`hardwareEvidence`** |
| `hardwareCheckedAt` | 〃 | 원본의 `ran_at` | 〃 |

**같은 파일의 azure·gcp 레코드는 다른 칸이 다른 소스에서 옵니다.**

| 프로바이더 | 그 소스에서 오는 칸 | 소스 | 표식 |
|---|---|---|---|
| azure | `maxNics` · `networkBandwidthMbps` | §29 `azure-compute-docs` | `azureSizesEvidence` |
| gcp | `cpuFamily` · `family` · 대역폭 · GPU · 로컬 SSD | §30 `gcloud-machine-types` | `gcpSeriesEvidence` |

**표식이 붙은 칸은 본체(미러)가 아니라는 뜻이고, 표식이 없으면 미러에서 온 것**입니다.
이 규약 덕분에 *"이 대역폭 값은 어디서 왔나"*에 **레코드만 보고** 답할 수 있습니다.

### 섞인 네 소스 — 원본 전문은 각 소스 절에

`tumblebug-perf.json.gz` 한 파일에 소스가 넷 들어갑니다. **네 원본이 서로 전혀 다르게
생겼다**는 것이 이 축의 처리가 소스마다 다른 이유입니다. **원본 전문과 보는 주소는 각
소스를 설명하는 절에 있습니다.**

| | 소스 | 원본이 어떤 물건인가 | 전문 |
|---|---|---|---|
| ① | §1 `tumblebug-dump` | **PostgreSQL 커스텀 덤프.** 안의 `details` 컬럼은 JSON처럼 보이지만 `value` 안쪽이 Go `%v` 포맷 | 1.3 |
| ② | §28 `ec2-hardware` | 평범한 JSON dict. 타입 이름이 키 | 1.4 |
| ③ | §29 `azure-compute-docs` | **마크다운 산문 사이의 표** | 1.5 |
| ④ | §30 `gcloud-machine-types` | **SQL `UPDATE` 문의 나열** | 1.6 |

> **넷의 성격 차이가 곧 basis의 차이입니다** — ①②③은 벤더/실측 값이라 `stated`이고,
> ④는 커뮤니티가 구글 문서를 옮긴 것이라 **`inferred`**입니다(1.6). **같은 파일 안에서
> 칸마다 믿을 근거의 등급이 다르고, 그 사실이 표식으로 남습니다.**

**`sustainedCpu`만 객체인 것도 같은 이유입니다** — 값 하나에 근거 셋
(note·evidence·basis)이 붙어야 하는 유일한 필드입니다. 프로바이더마다 판정 메커니즘이
다르고(evidence 5종), **AWS의 `false`는 원본이 말한 것이 아니라 그 칸에서 빠진
것**이라 basis까지 갈리기 때문입니다(1.3).

## 1.1 이 축이 답해야 하는 질문

"이 인스턴스가 얼마나 빠른가"는 한 숫자로 답할 수 없는 질문입니다. 실제로 필요한 것은
**성능을 이루는 축들의 값과, 그 값에 붙은 함정**입니다.

```
CPU        지속 성능이 보장되나(버스트형인가) · 클럭 · 스레드/코어 · 제조사·모델 · 세대
네트워크   대역폭 · NIC 최대 개수 · 가속 네트워킹 여부 · 대역폭이 버스트인가
디스크     기준/최대 처리량 · 기준/최대 IOPS · 프리미엄 IO 지원
가속기     GPU 모델 · 개수 · 로컬 SSD
지역 간    리전 쌍 왕복 지연
```

## 1.2 시작 상태의 공백 — 조사가 필요했던 이유 (실측)

미러(`tumblebug-dump`) 하나로 성능 축을 열었을 때의 상태입니다. **이 표가 조사의
출발점이자 소스 선정의 근거**입니다.

| 프로바이더 | 스펙 | 성능 필드 | 무엇이 있었나 |
|---|---:|---:|---|
| aws | 18,564 | 상당수 | `details` 컬럼이 두꺼움 |
| azure | 34,846 | **4** | sustainedCpu·diskIops·acu·acceleratedNetworking뿐 |
| gcp | 11,622 | **1** | `sustainedCpu` 하나 |
| 나머지 7곳 | 6,049 | **0** | 전무 |

세 가지가 드러납니다.

1. **가장 큰 프로바이더가 가장 비어 있었습니다** — azure는 스펙 수 1위(47.7%)인데
   네트워크 대역폭도 NIC 수도 없었습니다.
2. **gcp는 사실상 빈 축이었습니다** — 필드 하나. 기계 판독 가능한 무인증 공식
   카탈로그가 없어서입니다(Billing Catalog API는 인증 필요, 문서는 사이트 렌더링뿐).
3. **가속기 정보가 한 건도 없었습니다.**

세 번째가 조사를 촉발한 직접 계기입니다. 실측에서 *"ap-northeast-2에서 쓸 수 있는 GPU
인스턴스 알려줘"*에 모델이 표를 통째로 지어냈고(`g5g`를 AMD라고 했습니다 —
NVIDIA입니다), 그것을 **우리 지식베이스에서 조회한 결과라고 명시**했습니다.

> **빈칸이 지어내기를 부릅니다.** 이 축의 소스를 찾은 것은 "더 많이 알기 위해서"가
> 아니라 **"모르는 자리에서 모델이 지어내는 것을 막기 위해서"**입니다.

## 1.3 소스 ① — `tumblebug-dump` (§1) · 성능 축의 척추

| | |
|---|---|
| **받는 주소** | `https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/v0.12.25/assets/assets.dump.gz` |
| **보는 주소** | <https://github.com/cloud-barista/cb-tumblebug/blob/v0.12.25/assets/assets.dump.gz> — ⚠ **바이너리 덤프라 GitHub이 렌더링하지 못합니다.** 받아서 `pgdumplib`로 열어야 합니다 |
| 핀 · 라이선스 | 태그 `v0.12.25` · Apache-2.0 · 34.9 MB |

**무엇인가.** PostgreSQL 16.14 custom-format 덤프입니다(`psql`로 못 읽고 `pgdumplib`가
필요합니다). `spec_infos` 테이블 42컬럼 × 73,083행 중 이 축이 보는 것은 **`details`
컬럼 하나**입니다.

### 원본 전문 — `spec_infos` 한 행 (`aws+ap-northeast-2+t3.micro`)

**42개 컬럼 전부입니다.** 덤프에서 그대로 읽었고 줄이지 않았습니다.

```
id                       aws+ap-northeast-2+t3.micro
uid                      tbqmg2095s9jaul95to8
csp_spec_name            t3.micro
name                     aws+ap-northeast-2+t3.micro
namespace                system
connection_name          aws-ap-northeast-2
provider_name            aws
region_name              ap-northeast-2
region_latitude          37.36
region_longitude         126.78
infra_type               node
architecture             x86_64
os_type
v_cpu                    2
memory_gi_b              1
disk_size_gb             -1
max_total_storage_ti_b   0
net_bw_gbps              0
accelerator_model
accelerator_count        0
accelerator_memory_gb    0
accelerator_type
cost_per_hour            0.013000000268220901
description
order_in_filtered_result 0
evaluation_status
evaluation_score01       -1
evaluation_score02       -1
evaluation_score03       -1
evaluation_score04       -1
evaluation_score05       -1
evaluation_score06       -1
evaluation_score07       -1
evaluation_score08       -1
evaluation_score09       -1
evaluation_score10       -1
root_disk_type
root_disk_size           -1
associated_object_list   []
is_auto_generated        f
system_label             auto-gen
details                  [{"key":"AutoRecoverySupported","value":"true"},{"key":"BareMetal","value":"false"},{"key":"BurstablePerformanceSupported","value":"true"},{"key":"CurrentGeneration","value":"true"},{"key":"DedicatedHostsSupported","value":"true"},{"key":"EbsInfo","value":"{EbsOptimizedInfo:{BaselineBandwidthInMbps:87,BaselineIops:500,BaselineThroughputInMBps:10.875,MaximumBandwidthInMbps:2085,MaximumIops:11800,MaximumThroughputInMBps:260.625},EbsOptimizedSupport:default,EncryptionSupport:supported,NvmeSupport:required}"},{"key":"FreeTierEligible","value":"true"},{"key":"HibernationSupported","value":"true"},{"key":"Hypervisor","value":"nitro"},{"key":"InstanceStorageSupported","value":"false"},{"key":"InstanceType","value":"t3.micro"},{"key":"MemoryInfo","value":"{SizeInMiB:1024}"},{"key":"NetworkInfo","value":"{DefaultNetworkCardIndex:0,EfaInfo:null,EfaSupported:false,EnaSupport:required,Ipv4AddressesPerInterface:2,Ipv6AddressesPerInterface:2,Ipv6Supported:true,MaximumNetworkCards:1,MaximumNetworkInterfaces:2,NetworkCards:[{MaximumNetworkInterfaces:2,NetworkCardIndex:0,NetworkPerformance:Up to 5 Gigabit}],NetworkPerformance:Up to 5 Gigabit}"},{"key":"PlacementGroupInfo","value":"{SupportedStrategies:[partition,spread]}"},{"key":"ProcessorInfo","value":"{SupportedArchitectures:[x86_64],SustainedClockSpeedInGhz:2.5}"},{"key":"SupportedBootModes","value":"legacy-bios; uefi"},{"key":"SupportedRootDeviceTypes","value":"ebs"},{"key":"SupportedUsageClasses","value":"on-demand; spot"},{"key":"SupportedVirtualizationTypes","value":"hvm"},{"key":"VCpuInfo","value":"{DefaultCores:1,DefaultThreadsPerCore:2,DefaultVCpus:2,ValidCores:[1],ValidThreadsPerCore:[1,2]}"}]
```

전문을 봐야 보이는 것들입니다.

- **`details`의 키가 22개**인데 성능 축이 쓰는 것은 그중 일부입니다. 나머지
  (`FreeTierEligible` · `Hypervisor` · `PlacementGroupInfo` …)는 **버리는 것이 아니라
  아직 축이 없는 것**입니다.
- **`disk_size_gb`가 `-1`이고 `evaluation_score01~10`이 전부 `-1`**입니다. `-1`은
  "없음"이 아니라 **"상류가 모른다"**이고, 그래서 산출물에서 `null`로 바뀝니다.
- **`os_type`·`description`·`accelerator_*`가 빈 문자열**입니다. 빈 칸과 `0`이 섞여
  있는 것이 이 원본의 성격입니다.
- `details`의 `value` 안쪽이 **JSON이 아닙니다** — Go `%v` 포맷이라 따옴표가 없고
  `Up to 5 Gigabit`처럼 값에 공백이 들어갑니다.

**왜 필요했나.** 여러 클라우드의 인스턴스 사양을 **한 형식으로** 모아 둔 유일한 공개
자료입니다. 프로바이더별 카탈로그 API를 따로 붙이면 12개 스키마를 다뤄야 하고, 대부분
자격증명이 필요합니다.

**어떻게 쓰나 — 함정 둘을 지납니다.**

*함정 1 — `details`의 안쪽은 JSON이 아닙니다.* 바깥은 JSON 배열인데 `value` 안쪽은
Go의 `%v` 포맷입니다. 따옴표가 없고, 값에 공백이 들어가고(`Up to 5 Gigabit`),
중첩·배열이 섞입니다.

```
{"key":"NetworkInfo","value":"{…,MaximumNetworkInterfaces:2,
   NetworkCards:[{MaximumNetworkInterfaces:2,NetworkCardIndex:0,
   NetworkPerformance:Up to 5 Gigabit}], NetworkPerformance:Up to 5 Gigabit}"}
```

**통째로 파싱하지 않습니다.** 필요한 키만 정규식으로 뽑고 못 뽑으면 `None`(fail-open)
입니다. 키마다 따로 정규식을 돌려 **필드 순서에 의존하지 않게** 합니다 — 실측상 Go의
`%v`가 구조체를 선언 순서로 찍어서 azure 34,846건이 0% 정렬 상태입니다. *뽑는 키를
소수로 유지하는 것 자체가 안전장치입니다.*

*함정 2 — `false`는 "아니다"가 아닙니다.* AWS의 `BurstablePerformanceSupported`가
`true`면 직접 말한 것이지만, `false`는 **"그 칸에서 빠졌다"**는 뜻일 뿐입니다. 실제로
`t1.micro`가 `false`인데 t1은 버스트형입니다 — 크레딧 제도보다 앞선 세대라 그 칸에서
제외됐을 뿐입니다.

```json
{"sustainedCpu": {"value": false,
   "note": "Burstable instance — performance drops to baseline once the CPU credits run out.",
   "evidence": "aws-burstable-field", "basis": "stated"}}
```

`false`인 인스턴스에는 **다른 evidence**(`aws-non-burstable-inferred`,
basis=`inferred`)가 붙습니다. 그대로 옮겼다면 "t1은 성능이 보장된다"는 **정반대 답**이
나갔을 것이고, 이 규율을 지키느라 **29,395건이 `stated`에서 `inferred`로
내려갔습니다.**

**무엇이 나왔나.** `data/tumblebug-perf.json.gz` — `specs` **65,032건**.

**못 주는 것.** `details`가 없는 프로바이더는 이 소스로 채울 수 없습니다. 아래 세
소스가 필요했던 이유입니다.

## 1.4 소스 ② — `ec2-hardware` (§28) · AWS 물리 수치

| | |
|---|---|
| **받는 주소** | `https://raw.githubusercontent.com/vantage-sh/ec2instances.info/4ef36cd2c9867c1076206dcb691412ae2de7e8dd/scraper/aws/ec2/extras/manually_fetched_data.json` |
| **보는 주소** | <https://github.com/vantage-sh/ec2instances.info/blob/4ef36cd2c9867c1076206dcb691412ae2de7e8dd/scraper/aws/ec2/extras/manually_fetched_data.json> — 브라우저에서 그대로 읽힙니다 |
| 핀 · 라이선스 | 커밋 `4ef36cd2…` · MIT · 1.4 MB |

**무엇인가.** 인스턴스 타입별 CPU 벤더·모델·클럭·캐시·코어/스레드·메모리 속도·GPU.
카탈로그에 없는 **물리 수치**입니다.

### 원본 전문 — 항목 하나 (`t3.micro`)

**이 타입의 항목 전체입니다.** 줄이지 않았습니다.

```json
{
  "t3.micro": {
    "ran_at": "2025-12-10T19:42:44.436913877Z",
    "coremark": {
      "total_ticks": 20640,
      "iterations_second": 29069.767442,
      "total_time_seconds": 20.64
    },
    "ffmpeg": null,
    "nvidia_gpus": [],
    "memory": {
      "total_mb": 904,
      "speed_mhz": 2933
    },
    "cpu": {
      "vendor": "GenuineIntel",
      "model": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
      "speed": 2500,
      "cache": 36608,
      "cpus": 1,
      "cores": 1,
      "threads": 2
    },
    "numa": {
      "numa_node_count": 1,
      "is_numa": false,
      "numa_node_core_counts": [
        1
      ],
      "numa_node_thread_counts": [
        2
      ],
      "memory_per_numa_node_mb": [
        863
      ],
      "node_distances": [
        [
          10
        ]
      ],
      "max_numa_distance": 10,
      "l3_cache_per_numa_node_mb": [
        35
      ],
      "l3_shared": true,
      "is_balanced": true
    }
  }
}
```

전문을 봐야 보이는 것들입니다.

- **`coremark`가 원본에 있습니다.** 우리가 안 담는 것이지 원본에 없는 것이 아닙니다 —
  담지 않는 이유는 아래에 적었고, **원본에 있다는 사실이 보여야 그 판단을 검증할 수
  있습니다.**
- **`ffmpeg`가 `null`이고 `nvidia_gpus`가 빈 배열**입니다. t3.micro에 GPU가 없다는
  뜻인데, **다른 항목에는 여기에 목록이 들어옵니다** — GPU 정보의 출처가 이 칸입니다.
- **`memory.total_mb`가 904**입니다. 카탈로그가 말하는 1 GiB(1024 MiB)와 다릅니다 —
  **OS가 실제로 본 값**이라 그렇고, 그래서 우리는 이 칸을 메모리 크기로 쓰지 않고
  `speed_mhz`만 가져옵니다.
- `numa` 블록 전체를 안 담습니다 — **축이 없어서**이지 값이 의심스러워서가 아닙니다.

**왜 필요했나.** 1.2에서 말한 GPU 공백을 메우기 위해서입니다. 미러의 `details`에는
가속기 개수는 있어도 **모델**이 없습니다.

**어떻게 쓰나.** `perfkb/parsers/hardware.py`가 `(provider=aws, specName)`으로 미러
스펙에 붙입니다. 실측: 1,093종 중 **1,069종**이 매칭(우리 aws 스펙 1,349종의 79%),
GPU 정보 보유 50종, 모델 표기는 11종으로 이미 정규화돼 있습니다.

**무엇을 일부러 안 담나 — 벤치마크 점수.** 이 파일에는 coremark 점수가 있는데 담지
않습니다. **이유가 셋이고 전부 실측입니다.**

- 원점수는 인스턴스 크기에 거의 정비례합니다(m7i 2vCPU 47k → 48vCPU 1,068k).
  그대로 보여주면 **큰 인스턴스가 항상 이기는** 답이 나옵니다.
- vCPU당으로 나누면 세대·아키텍처 차이가 잘 보이지만(Intel 18.7k · AMD 21.1k ·
  Graviton 25.7k), **어느 쪽을 보여주느냐가 답을 뒤집습니다.**
- coremark는 정수 연산이라 메모리·I/O 중심 작업에는 안 맞습니다.

담는 것은 전부 사실입니다 — `p4d.24xlarge`에 A100이 8장 달렸다는 건 측정이 아니라
사양입니다. 저장소가 서빙하는 209MB짜리 종합 파일도 **쓰지 않습니다**(커밋돼 있지 않고
AWS Price List 파생물이라 고정도 재배포도 안 됩니다).

**한계.** **단일 소스입니다** — 교차 검증할 짝이 없습니다. 대신 원본이 측정 시점
(`ran_at`)을 적어 두어 신선도를 판단할 수 있고, 그 시각을 레코드에 그대로 싣습니다
(`hardwareCheckedAt: "2025-12-10"`).

## 1.5 소스 ③ — `azure-compute-docs` (§29) · 문서의 **표만**

| | |
|---|---|
| **받는 주소** | `https://raw.githubusercontent.com/MicrosoftDocs/azure-compute-docs/9c18d88d498d09e897edde7e2fe8483067f2556a` + 시리즈별 경로 |
| **보는 주소** | <https://github.com/MicrosoftDocs/azure-compute-docs/tree/9c18d88d498d09e897edde7e2fe8483067f2556a/articles/virtual-machines/sizes> — **GitHub이 표를 렌더링해서 보여 줍니다** |
| 핀 · 라이선스 | 커밋 `9c18d88d…` · **CC-BY-4.0**(저작자 표시 의무) · md 156편 |

**왜 필요했나.** 1.2의 azure 4필드 공백. 미러에 NIC 수도 네트워크 대역폭도 없었습니다.

**어떻게 쓰나 — 원칙의 예외를 정확히 어디까지 여는가.** 이 창고의 원칙은 *"사람이 읽는
문서를 긁지 않는다"*입니다. 여기는 예외인데, **산문이 아니라 표의 칸만** 읽습니다.

### 원본 전문 — 네트워크 탭 한 절 (`falsv6-series.md`)

**표와 그 앞뒤 산문까지 그대로입니다.** 우리가 읽는 것은 표 여덟 줄뿐입니다.

```markdown
### [Network](#tab/sizenetwork)

Network interface info for each size

| Size Name | Max NICs (Qty.) | Max Network Bandwidth (Mb/s) |
| --- | --- | --- |
| Standard_F2als_v6 | 2 | 12500 |
| Standard_F4als_v6 | 2 | 12500 |
| Standard_F8als_v6 | 4 | 12500 |
| Standard_F16als_v6 | 8 | 16000 |
| Standard_F32als_v6 | 8 | 20000 |
| Standard_F48als_v6 | 8 | 28000 |
| Standard_F64als_v6 | 8 | 36,000 |

#### Networking resources
- [Virtual networks and virtual machines in Azure](/azure/virtual-network/network-overview)
- [Virtual machine network bandwidth](/azure/virtual-network/virtual-machine-network-throughput)

#### Table definitions
- Expected network bandwidth is the maximum aggregated bandwidth allocated per VM type across all NICs, for all destinations. For more information, see [Virtual machine network bandwidth](/azure/virtual-network/virtual-machine-network-throughput)
```

전문을 봐야 보이는 것들입니다.

- **표기가 한 표 안에서도 흔들립니다** — 앞의 여섯 줄은 `12500`인데 마지막 줄만
  `36,000`으로 **천단위 콤마**가 있습니다. 파서가 콤마를 처리하지 않으면 이 한 줄만
  조용히 빠집니다.
- **헤더 문구도 문서마다 다릅니다** — 여기는 `Max Network Bandwidth (Mb/s)`인데
  다른 시리즈는 `(Mbps)`입니다. 헤더 문자열로 정확히 일치시키면 절반이 안 걸립니다.
- **탭 표기(`### [Network](#tab/sizenetwork)`)가 절의 경계**입니다. 마크다운 제목이
  아니라 이 표기를 봐야 어느 표가 네트워크 표인지 압니다.
- 아래 `#### Table definitions`의 산문은 **읽지 않습니다** — 표의 칸만 읽는다는 예외의
  범위가 여기서 끝납니다.

1. **`maxNics`·`networkBandwidthMbps` 둘만** 담습니다. 같은 표에 있는 vCPU·메모리·디스크
   IOPS는 **미러가 이미 갖고 있어** 담지 않습니다 — 두 소스의 값이 섞이면 **어느
   스냅샷의 값인지 알 수 없게 됩니다.**
2. 각주 표기(`<sup>1</sup>`)와 `Not Supported` 칸은 실측에서 확인한 형식입니다 —
   **숫자가 아닌 칸은 담지 않고 셉니다.**
3. **구세대 판정도 여기서 나옵니다.** 생애주기 문서의 시리즈 라벨 37종을 사람이 만든
   정규식 표로 옮기되, **문서 라벨과 손 표가 어긋나면 빌드가 죽는 상호 대조**를
   내장했습니다. 구세대 146종에 표시를 붙이되 **목록에 없다고 "최신"이라고 하지
   않습니다** — 부재를 최신 주장으로 승격하는 것이 침묵 오독입니다.
4. 문서라 재편될 수 있어 **최소 매칭 수**로 감지합니다.

**basis가 `stated`인 이유.** 문서가 **표로 명시한 값**을 옮긴 것이고 우리 추론이
없습니다. 같은 성능 축의 gcp 소스(§30)가 `inferred`인 것과 갈리는 지점입니다.

## 1.6 소스 ④ — `gcloud-machine-types` (§30) · GCP 시리즈 특성

| | |
|---|---|
| **받는 주소** | `https://raw.githubusercontent.com/Cyclenerd/google-cloud-compute-machine-types/add204f16413d608d35141715aef4a122b59cb96` + 시리즈별 `.sql` |
| **보는 주소** | <https://github.com/Cyclenerd/google-cloud-compute-machine-types/tree/add204f16413d608d35141715aef4a122b59cb96/instances/series> — **주석에 구글 원문 URL이 달려 있어** 거기서 한 번 더 확인할 수 있습니다 |
| 핀 · 라이선스 | 커밋 `add204f1…` · Apache-2.0 · SQL 34개 |

**왜 필요했나.** 1.2의 gcp 1필드 — 세 프로바이더 중 최악의 공백.

### 원본 전문 — `instances/series/n2.sql` **파일 전체**

**20줄짜리 파일이라 통째로 싣습니다.** 한 글자도 고치지 않았습니다.

```sql
/* N2 General-purpose */
/* https://cloud.google.com/compute/docs/machine-types#machine_type_comparison */
/* https://cloud.google.com/compute/docs/general-purpose-machines#n2_machines */
UPDATE instances SET
series      = 'n2',
family      = 'General-purpose',
cpuPlatform = 'Cascade Lake, Ice Lake',
localSsd    = '1',
sud         = '1',
spot        = '1'
WHERE name LIKE 'n2-%';
UPDATE instances SET bandwidth = '10' WHERE name LIKE 'n2-%-2';
UPDATE instances SET bandwidth = '10' WHERE name LIKE 'n2-%-4';
UPDATE instances SET bandwidth = '16' WHERE name LIKE 'n2-%-8';
UPDATE instances SET bandwidth = '32' WHERE name LIKE 'n2-%-16';
UPDATE instances SET bandwidth = '32', tier1 = '50'  WHERE name LIKE 'n2-%-32';
UPDATE instances SET bandwidth = '32', tier1 = '50'  WHERE name LIKE 'n2-%-48';
UPDATE instances SET bandwidth = '32', tier1 = '75'  WHERE name LIKE 'n2-%-64';
UPDATE instances SET bandwidth = '32', tier1 = '100' WHERE name LIKE 'n2-%-80';
UPDATE instances SET bandwidth = '32', tier1 = '100' WHERE name LIKE 'n2-%-96';
UPDATE instances SET bandwidth = '32', tier1 = '100' WHERE name LIKE 'n2-%-128';
```

전문을 봐야 보이는 것들입니다.

- **주석 두 줄이 구글 문서 URL**입니다. 이 소스가 `inferred`인 이유이자, 동시에
  **사람이 원문까지 되짚을 수 있는 이유**입니다.
- **`bandwidth`와 `tier1`이 같은 문장에 나옵니다.** `tier1`은 Tier_1 네트워킹을
  **켰을 때**의 값이라 담지 않는데, **전문을 보면 둘이 나란히 있어서 잘못 담기 쉬운
  모양**이라는 것이 드러납니다.
- **`LIKE 'n2-%-2'`와 `'n2-%-128'`이 같은 접미사 규칙**입니다. `%`는 `fnmatch`의 `*`로
  옮겨지고, `n2-%-2`가 `n2-standard-2`에는 걸리지만 `n2-standard-32`에는 안 걸립니다.
- 첫 `UPDATE`가 **시리즈 전체에 값을 깔고** 뒤의 문장들이 크기별로 덮어씁니다 —
  **문장 순서가 의미를 갖습니다.**

**어떻게 쓰나.** 문장 단위 정규식으로 `SET k='v', … WHERE name LIKE '패턴'`을 읽고 LIKE
패턴을 `fnmatch`로 옮깁니다(`%` → `*`). SQL 파싱이지만 **문법이 핀돼 있어서** 안전합니다.

**무엇을 일부러 안 담나 — `tier1`.** Tier_1 네트워킹을 **활성화했을 때**의 대역폭이라
기본 구성의 값이 아닙니다. 담으면 기본 대역폭처럼 읽힙니다.

**basis가 `inferred`인 이유.** 원문이 Google 문서 URL을 달아 두어 사람이 확인할 수
있지만, **옮긴 것은 커뮤니티**입니다. §29(문서 표를 직접 파싱, `stated`)와 등급이
다르다는 것이 두 라벨을 가른 이유입니다 — *같은 축의 값이라도 근거의 등급이 다르면
라벨을 나눕니다.*

**같은 저자의 다른 파일도 씁니다.** §27 `pricing.yml`에서 GPU 수·로컬 SSD GB를 함께
읽습니다. **모르는 GPU 키는 승격하지 않고 셉니다.**

## 1.7 소스 ⑤ — `ibm-global-catalog` (§31) · 성능 축을 넓히려는 시도의 결말

| | |
|---|---|
| **받는 주소** | `https://globalcatalog.cloud.ibm.com/api/v1?q=is.instance` |
| **보는 주소** | <https://globalcatalog.cloud.ibm.com/api/v1?q=is.instance> — **같은 주소입니다. 무인증이라 브라우저에서 그대로 열립니다.** 전체를 보려면 `&_limit=200&_offset=0` |
| 핀 · 라이선스 | **지문**(버전 없음) · 라이선스 미표기 · 재배포 문구 없음(NOTICE에 공시) |

**왜 이것만인가 — 이 항목 자체가 조사 결과입니다.** 성능 축을 12개 클라우드로 넓히려
조사했더니, aws·azure·gcp 밖 **아홉 중 공개 성능 소스가 실재하는 곳은 IBM뿐**이었습니다.
Terraform provider는 타입 축만 주고 스펙 카탈로그를 주지 않습니다. **나머지는 "부재
확정"으로 기록했습니다** — 이 문서에서 알리바바·텐센트·오라클의 성능이 0건인 것은
빠뜨린 것이 아니라 **찾아보고 없다고 확인한 것**입니다.

**어떻게 쓰나 — 담을 것을 채움률이 아니라 변별력으로 정했습니다.**

```
freqency         310건 100%  값 1가지(2000)
status           287건  93%  값 1가지(current)
vcpu_architecture / os_architecture / reservation_terms / iothreads … 전부 1가지

bandwidth         98.1%  18가지     port_speed       92.6%  3가지
max_nics          92.6%   5가지     vcpu_tenancy     92.6%  3가지
vcpu_manufacturer 82.9%   2가지     cpu_family       52.3%  3가지
```

`freqency`(원본 철자 그대로)는 **채움률 100%인데 310건 전부 값이 2000**입니다. 담았으면
**모든 IBM 스펙에 "2.0 GHz"라는 확신에 찬 오답**이 붙었을 것이고, 실제 클럭은 세대마다
다른데 우리는 그걸 모릅니다.

**추론으로 칸을 채우지 않습니다.** `vcpu_tenancy`가 `dedicated`인 걸 보고 "상시 CPU
보장"이라고 적고 싶어지지만 **그건 우리 추론이지 원본이 한 말이 아닙니다.**
`vcpuTenancy`를 그대로 담고, 조회 계층이 **"레코드는 있으나 버스트·세대 신호가 없다"**를
별도 상태로 답합니다.

**교차 확인.** 미러의 IBM 287종과 **287/287 조인**되고 cpu·ram이 287건 전부 일치합니다 —
두 독립 소스가 같은 값을 말합니다.

**함정.** 페이지네이션 파라미터가 문서와 다릅니다(`limit` 무시, `_limit`/`_offset` 필요).
첫 페이지만 보면 커버리지가 14%로 보입니다.

**버린 칸의 이름을 산출물에 적습니다** — 다음 사람이 "왜 클럭이 없냐"고 물었을 때 답이
데이터 안에 있어야 합니다.

## 1.8 소스 ⑥ — `tumblebug-latency` (§3) · 지역 간 성능

| | |
|---|---|
| **받는 주소** | `https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/v0.12.25/assets/cloudlatencymap.csv` |
| **보는 주소** | <https://github.com/cloud-barista/cb-tumblebug/blob/v0.12.25/assets/cloudlatencymap.csv> — 300×300 행렬. **GitHub이 CSV를 표로 렌더링해 줍니다** |
| 핀 · 라이선스 | 태그 `v0.12.25` · Apache-2.0 |

**왜 성능 축인가.** 인스턴스 하나의 속도가 아니라 **배치의 성능**입니다. 멀티 리전
구성에서 지연은 단일 인스턴스 성능보다 지배적입니다.

**왜 믿나.** 벤더가 광고하는 SLA가 아니라 **실제로 VM을 띄워 잰 값**입니다.

**어떻게 쓰나 — 네 가지를 검증하고 결과를 산출물에 적습니다.**

```
대칭성      양방향 4,851쌍 중 값이 다른 것 4,845(99.9%)  → 전치 복사가 아니다
전치 채움   전부 대칭인 행 98개 중 0개                    → 이 스냅샷엔 거의 안 걸림
거리 상관   r = 0.817, 거리대별 중앙값 단조 증가
물리 하한   왕복 광속(광섬유 200,000 km/s) 위반 16/10,791 (0.15%)
```

**하한 위반 16쌍을 지우지 않고 `suspect`로 표시합니다.** 좌표가 틀렸는지 값이 틀렸는지
모르므로, 조용히 버리면 다음 사람이 같은 것을 다시 발견합니다.

**못 주는 것 — 측정 시각이 원본에 없습니다.** (덤프의 `measured_at`은 적재 시각이라
10,890행이 같은 초에 찍혀 있습니다.) `_note`가 그 사실을 답변까지 끌고 갑니다.

## 1.9 이 축이 지금 답할 수 있는 것 (2026-07-29 실측)

**커밋된 상태 기준입니다**(8.2 참조).

| 프로바이더 | 스펙 | 성능 레코드 | 필드 종류 | 대표 필드 채움 |
|---|---:|---:|---:|---|
| **azure** | 34,846 | 34,846 | 12 | sustainedCpu·threadsPerCore·diskIops·acceleratedNetworking·premiumIO·family 34,846(100%) · cachedDiskIops 34,686 · **maxNics 25,385** · networkBandwidthMbps 24,108 · acu 13,135 · currentGeneration 4,822 |
| **aws** | 18,564 | 18,564 | **23** | sustainedCpu·currentGeneration·threadsPerCore·networkPerformance·networkIsBurst·bareMetal 18,564(100%) · clockGHz 18,484 · ebs 4종 18,341 · cpuThreads·hardwareCheckedAt 16,492 · cpuClockMHz 15,492 · cpuVendor·cpuModel·cpuCacheKB·cpuCores 11,499 · memorySpeedMHz 10,494 · **GPU 3종 571** |
| **gcp** | 11,622 | 11,622 | 11 | sustainedCpu·maxPersistentDisks·maxPersistentDiskGB·vendorDescription 11,622(100%) · cpuFamily·family 11,434(98.4%) · networkBandwidthMbps 11,348 · localSsdGB 1,441 · GPU 2종 258 |
| **ibm** | 2,002 | 2,002 (별도 파일) | 8 | networkBandwidthMbps·portSpeed·maxNics·vcpuTenancy |
| alibaba·tencent·kt·ncp·nhn·openstack | 6,049 | **0** | — | **공개 성능 소스 부재 확정** |

**합계 67,034건** (`tumblebug-perf` 65,032 + `ibm-perf` 2,002) · 미커버 6,049건(8.3%).

**세 프로바이더의 두꺼움이 다른 방식으로 다릅니다.**

- **aws는 필드 종류가 가장 많지만(23) 뒤로 갈수록 얇아집니다** — 하드웨어 칸들이
  §28에서 오는데 매칭이 1,069종이라 `memorySpeedMHz`는 10,494건(56.5%)까지 떨어집니다.
  **GPU가 571건 있는 것이 §28을 받은 직접 이유**입니다(1.4).
- **azure는 필드가 12종뿐이지만 앞쪽 6종이 100%입니다** — 미러의 azure 칸이 그만큼
  균일하고, §29가 더한 두 칸(`maxNics`·`networkBandwidthMbps`)이 70%대입니다.
- **gcp는 §30 없이는 `sustainedCpu` 하나였습니다** — 지금 `cpuFamily`·`family`가
  98.4%까지 찼고, 나머지 1.6%는 §30의 시리즈 표에 없는 크기입니다.

## 1.10 이 축에서 일부러 안 하는 것

| 안 하는 것 | 왜 |
|---|---|
| **"A와 B 중 뭐가 빠른가" 승자 선언** | 워크로드에 따라 갈립니다. 지속 대역폭은 t3가 높고 최대 버스트는 m5가 높은데, **한 줄로 접으면 어느 쪽으로 접든 거짓**입니다 |
| **클라우드 회사 간 성능 비교** | 잣대가 다릅니다 — ACU는 Azure에만, 클럭은 AWS에만 있습니다. **구조적으로 막아 뒀습니다** |
| **벤치마크 점수 수록** | 1.4 참조. 정규화 방식이 답을 뒤집습니다 |
| **`sustainedCpu` 추정 생성** | IBM의 `dedicated` 테넌시로 추론하지 않습니다(1.7) |
| **"동접 1만이면 서버 몇 대?"** | 그 답의 근거가 될 **공개 자료가 없습니다.** 근거 없는 규모 산정은 확신에 찬 오답의 왕도입니다 |

## 1.11 이 축의 타당성 위협

- **상류 단일 의존.** 성능 65,032건 전부가 `tumblebug-dump` 한 소스에서 나옵니다. 실제로
  상류 CB-Spider의 `ConvertMBToMiBInt64` 버그(`mb*1000/1024`를 이미 MiB인 값에 적용,
  순효과 `참값/1.024`)가 있었고, 73,083행 전수로 영향 범위를 세니 **gcp 77.6% ·
  azure 64.2%**, 나머지 8곳은 0.0%였습니다. **gcp·azure만** ×1.024 보정하고, 보정 사실은
  값이 아니라 데이터셋 메타데이터(`_corrections`)에 적습니다.
- **보강 소스 셋이 전부 단일 소스**입니다(§28 AWS·§29 Azure·§30 GCP). 교차 검증되는 것은
  IBM(§31, 287/287)뿐입니다.
- **커뮤니티 큐레이션 비중.** gcp 필드의 98%가 `inferred`(커뮤니티 전사)입니다. azure는
  `stated`(문서 표), aws는 `stated`+단일 소스 물리값입니다 — **같은 축 안에서 근거
  등급이 프로바이더마다 다릅니다.** 답변에 그 사실이 실립니다.
- **신선도.** §28은 `ran_at: 2025-12-10`으로 시점이 밝혀져 있지만, 나머지는 핀 시점이
  곧 신선도이고 **핀이 언제 낡는지 지켜보는 주기가 아직 없습니다.**
- **정확성 미측정.** 출처는 밝히지만 값이 실제와 맞는지 잰 적이 없습니다 — 정답지가
  없어서입니다(ISO 25012 매핑에서 드러난 공백).

---

# 2부 — 클라우드 인스턴스 비용

> 과제 원문: *"클라우드 환경 특성(클라우드 인스턴스 성능, **비용** 등)"*

## 2.0 정의 — 이 축은 무엇인가

> **클라우드 인스턴스 비용**이란, 같은 스펙을 **어떤 조건으로 쓰느냐에 따라 달라지는
> 단가**를 말한다. **조건이 값의 일부**이고, 이 축은 **단가만** 담는다 —
> **총액은 이 축의 산출물이 아니다.**

마지막 문장이 이 축의 가장 중요한 판단입니다. 값을 아는 항목과 모르는 항목이 섞여
있는데 합계를 내면 **모르는 것을 0으로 친 그럴듯한 거짓 총액**이 나옵니다.

### 관측 단위 — 세 종류의 레코드

이 축만 **레코드 모양이 셋**입니다. 답하는 질문이 다르기 때문입니다.

| | 관측 단위 | 무엇을 답하나 |
|---|---|---|
| **정가** | (프로바이더, 리전, 스펙) | 그냥 켜면 시간당 얼마 |
| **할인가** | (스펙, 리전) — 종류별 칸을 한 행에 | 조건을 걸면 얼마 |
| **관리형** | (아키타입, 리전, 서비스, 미터) | 인스턴스가 아닌 것의 과금 |

정가와 할인가는 **스펙 id로 조인**되고, 관리형은 **조인되지 않습니다** — 인스턴스가
아니기 때문입니다. 셋을 한 표로 합치지 않는 이유입니다.

### 무엇이 이 축이 **아닌가** — 경계

| 아닌 것 | 왜 |
|---|---|
| **총 비용 / 월 예상액** | 위 참조. 대신 *"축별로 나눠서 + 모르는 것 N건"*으로 답합니다 |
| 협상가·EA 할인·세금·환율 | 공개 자료가 없습니다 |
| 관리형의 "이 서비스 쓰면 얼마" | 수량을 알아야 하는데 그 수량이 **사이징 결과**입니다 |
| 가격 대비 성능 | ①과 ②가 다른 스냅샷일 수 있습니다(2.4) |
| 탄소 비용·지속가능성 | **어느 축도 아닙니다** → 6.2 |

### 용어 — 가격의 종류와 축

**가격의 종류(kind)** — Azure에서는 **별도 필드가 아니라 이름 안에** 들어 있습니다.

| 낱말 | 정의 | 담나 |
|---|---|---|
| **온디맨드(정가)** | 약정 없이 켠 만큼 내는 시간당 요금 | ○ |
| **스팟**(spot) | **회수될 수 있는 대신** 싼 것 | ○ |
| **예약**(Reservation) | 1년·3년 약정. **원본 `retailPrice`가 기간 총액**입니다(2.3 함정 ③) | ○ (나눠서) |
| **저축 플랜**(savings plan) | 시간당 지출을 약정. **이쪽은 진짜 시간당**입니다 | ○ (그대로) |
| **Low Priority** | 스팟과 **별개 미터**인데 어디에 적용되는지 이 데이터로는 알 수 없음 | **×** |
| **DevTest** | Dev/Test 구독에서만 적용 — 일반 사용자에게 그 값을 가격이라고 말하면 **거짓** | **×** |

**과금 축(axis)** — 관리형 서비스의 단가가 **무엇에 비례하는가**. 이 셋을 구분하지 않고
숫자 하나로 접는 것이 이 축의 대표적 실패입니다.

```
instanceHour   시간당 단가가 그대로 성립한다 — 켜 두면 그 값 (RDS 인스턴스·EKS 클러스터)
capacityRate   단가 × 수량. 수량(vCore·RU·GB)이 **사이징 결과**라 우리가 모른다
usage          부하가 소비하는 단위 — **트래픽을 알아야 비용이 생긴다** (요청 수·전송량)
```

**모르는 단위는 `usage`로 둡니다** — 시간당 단가를 지어내는 방향보다 안전합니다.

**그 밖의 낱말.**

| 낱말 | 정의 |
|---|---|
| **아키타입**(archetype) | 관리형 서비스를 **기능으로 묶은 이름**(`loadBalancer` · `objectStorage` · `relationalDatabase`). 벤더 제품명이 아니라 우리 어휘입니다 |
| **`hourlyUSD`가 `null`** | `0`과 음수는 **'가격 미상'**이므로 `null`입니다 — **`0`은 무료가 아닙니다** |
| **`matchesMirror`** | Azure API 값과 미러 값이 0.5% 안에서 일치하는가 — **일치를 가정하지 않고 확인해서 담습니다** |
| **`mirrorRatio` / `snapshotMatchesMirror`** | GCP의 두 소스가 **같은 가격 세계인가.** 다르면 레코드 스스로 그렇게 말합니다(2.4) |
| **`memGiB`** | **보정된** 실제 메모리. 상류 버그로 gcp/azure 원본이 2.4% 낮게 기록돼 있고, 무엇을 어떻게 고쳤는지는 `_corrections`에 있어 **그 식으로 원본을 되돌릴 수 있습니다** |

### 레코드 실물 — 셋이 어떻게 다른가

```json
정가   {"id": "aws+ap-northeast-2+t3.micro", "provider": "aws", "region": "ap-northeast-2",
        "specName": "t3.micro", "vCPU": 2, "memGiB": 1.0,
        "hourlyUSD": 0.013000000268220901, "architecture": "x86_64",
        "infraType": "node", "acceleratorCount": 0}

할인   {"specName": "standard_b12ms", "region": "australiacentral",
        "ondemandUSD": 0.634, "mirrorUSD": 0.6340000033378601, "matchesMirror": true,
        "spotUSD": null, "reserved1yUSD": null, "reserved3yUSD": null,
        "savings1yUSD": 0.461996, "savings3yUSD": 0.320297}

관리형 {"archetype": "loadBalancer", "service": "Load Balancer", "product": "Load Balancer",
        "sku": "Standard", "meter": "Standard Data Processed", "unit": "1 GB",
        "axis": "usage", "unitPriceUSD": 0.005, "region": "Global"}
```

> **출처** — 셋이 다른 파일이고 다른 소스입니다.
>
> | 레코드 | 산출물 | 소스 | 핀 |
> |---|---|---|---|
> | 정가 | `data/tumblebug-cost.json.gz` | **§1 `tumblebug-dump`** — `spec_infos`의 `cost_per_hour` | 태그 `v0.12.25` |
> | 할인 | `data/azure-discount-pricing.json.gz` | **§26 `azure-retail-prices`** | **지문**(고정 불가) |
> | 관리형 | `data/azure-managed-pricing.json.gz` | **§26 `azure-retail-prices`** | **지문** |
>
> **할인 레코드의 `mirrorUSD` 한 칸만 §1에서 옵니다** — 대조용으로 끌어온 값이고,
> `matchesMirror`가 그 대조 결과입니다. 즉 **레코드 안에 두 소스의 값이 나란히 놓이고
> 어느 쪽이 어느 소스인지 칸 이름이 말합니다.**

### 섞인 소스를 각각 원본에서 보기

**① §26 `azure-retail-prices`** — 할인·관리형의 본체. **살아 있는 API**

- 보는 주소: <https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview>
  **자격증명 없이 브라우저에서 그대로 열립니다.** 리전·서비스를 좁히려면
  `&$filter=serviceName eq 'Virtual Machines' and armRegionName eq 'koreacentral'`을
  붙이면 됩니다 — **우리가 받는 방식과 같은 질의**입니다.
- **버전이 없어 지문 핀입니다** — 지금 열면 우리가 받은 것과 값이 다를 수 있고, 그
  사실이 이 소스를 `digest`로 분류한 이유입니다.
- 원본 형태 — 같은 SKU에 행이 여럿이고 **판별자가 `productName`**입니다(2.3):

```json
{"retailPrice": 0.6057, "unitOfMeasure": "1 Hour", "type": "Consumption",
 "productName": "Virtual Machines Basv2 Series", "armSkuName": "Standard_B16als_v2"}
{"retailPrice": 3478.0, "unitOfMeasure": "1 Hour",
 "type": "Reservation", "reservationTerm": "1 Year", "armSkuName": "Standard_B16als_v2"}
```

**② §1 `tumblebug-dump`** — `mirrorUSD` 한 칸(대조용)

- 보는 주소: <https://github.com/cloud-barista/cb-tumblebug/blob/v0.12.25/assets/assets.dump.gz>
  (바이너리 덤프 — 1.0의 ①과 같은 파일의 다른 컬럼 `cost_per_hour`)

**③ GCP 할인은 소스가 아예 다릅니다** — `data/gcp-spot-commit.json.gz`
← **§27 `cyclenerd-gcp-pricing`**(커밋 `574d8fbb68fa`).

- 보는 주소: <https://github.com/Cyclenerd/google-cloud-pricing-cost-calculator/blob/574d8fbb68fa/pricing.yml>
  (**단일 YAML이라 브라우저에서 그대로 읽힙니다**)
- 원본 형태 — 인스턴스 × 리전 격자에 값 여섯:

```yaml
n2-highmem-8:
  cost:
    asia-east1:
      hour: 0.6068          # 온디맨드 — **미러와 다른 스냅샷**
      hour_spot: 0.214704
      month_1y: 279.07608
      month_3y: 199.3484
```

여기서는 `hourRefUSD` · `mirrorRatio` · `snapshotMatchesMirror`가 `matchesMirror`와
같은 역할을 합니다(2.4).

**할인 레코드에 `ondemandUSD`와 `mirrorUSD`가 둘 다 있는 것이 설계입니다** — 할인율을
계산하려면 **어느 온디맨드 기준인지**를 레코드 안에서 알 수 있어야 합니다. 앞은 API가
말한 값, 뒤는 미러가 말한 값이고, **둘이 같은지를 가정하지 않고 확인해서 담습니다.**

## 2.1 이 축이 답해야 하는 질문

비용은 성능보다 층이 많습니다. **"얼마인가"가 네 개의 다른 질문**입니다.

```
정가(온디맨드)   그냥 켜면 시간당 얼마
할인가           스팟 / 예약(1년·3년) / 저축 플랜 — 조건을 걸면 얼마
관리형 서비스     RDS·Cosmos·Cloud Storage 같은 것 — 인스턴스가 아닌 과금 축
비용 축의 성격    시간당 요율인가 · 용량에 비례하는가 · 트래픽에 비례하는가
```

넷째가 이 축의 핵심 판단입니다. 셋을 구분하지 않고 숫자 하나로 접으면 **그럴듯한
거짓 총액**이 나옵니다.

## 2.2 소스 ① — `tumblebug-dump` (§1) · 정가

성능 축과 **같은 행의 다른 컬럼**을 봅니다 — `cost_per_hour`·`v_cpu`·`memory_gi_b`.

**왜 필요했나.** 12개 클라우드의 온디맨드 정가를 한 형식으로 주는 유일한 공개 자료입니다.

**어떻게 쓰나 — 보정값을 한 칸에 넣습니다.** 예전엔 미러값과 보정값을 두 칸에 나눠
뒀는데, **표시는 보정값·필터는 버그값**을 쓰다가 `"16 GiB 이상"` 질의에서 실제로는
만족하는 **3,765건이 조용히 빠졌습니다.** 지금은 `memGiB` 한 칸에 보정값을 넣고 보정
사실은 `_corrections`에 적습니다.

**못 주는 것 — 할인가가 없습니다.** 온디맨드 정가뿐입니다. §26·§27이 필요했던 이유입니다.

**실측에서 드러난 것 하나 — 스펙 행 수 ≠ 가격 보유 수.**

| | 스펙 행 | 가격 있음 | 가격 **없음** |
|---|---:|---:|---:|
| 전체 | 73,083 | **68,705 (94.0%)** | **4,378 (6.0%)** |
| azure | 34,846 | 32,925 | 1,921 |
| aws | 18,564 | 18,137 | 427 |
| gcp | 11,622 | 10,626 | 996 |
| alibaba | 2,494 | 1,954 | 540 |
| tencent | 2,865 | 2,863 | 2 |
| ibm | 2,002 | 1,926 | 76 |
| ncp | 393 | 178 | 215 |
| kt | 220 | 31 | **189** |
| nhn | 71 | 65 | 6 |
| openstack | 6 | 0 | **6** |
| **oracle** | **0** | — | — |

**kt는 스펙 220건 중 31건(14%)에만 가격이 있고, openstack은 6건 전부 없습니다.**
"정가 73,083건"이라고 말하면 **가격 없는 4,378건을 가격 있는 것으로 세는 것**입니다
(8부 참조).

## 2.3 소스 ② — `azure-retail-prices` (§26) · Azure 할인가 · **함정 셋**

| | |
|---|---|
| **받는 주소** | `https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview` |
| **보는 주소** | <https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview> — **같은 주소입니다. 무인증이라 브라우저에서 그대로 열립니다.** 좁히려면 `&$filter=serviceName eq 'Virtual Machines' and armRegionName eq 'koreacentral'` |
| 핀 · 라이선스 | **지문**(버전 개념 없음) · 라이선스 미표기(금지 문구도 없어 NOTICE에 공시) |

**왜 필요했나.** "예약하면 얼마 싸지나"에 답하는 **유일한 근거**입니다. 리전마다
`$filter=serviceName eq 'Virtual Machines' and armRegionName eq '<리전>'`로 질의하고
`NextPageLink`를 따라갑니다(리전 39곳, 한 리전 8,000행 안팎).

### 원본 전문 — 한 SKU에 붙는 **13행 전부**

`armSkuName = Standard_B16als_v2` · `armRegionName = australiacentral`.
**SKU 하나에 응답이 13행 옵니다.** 아래는 그 13행을 원본 필드 그대로 투영한 것입니다
(행 순서는 응답 순서, 값은 무가공).

```
productName                            skuName                  type                reservationTerm  retailPrice  savingsPlan
Basv2 Series Cloud Services            B16als v2 Low Priority   Consumption         -                     0.299   -
Virtual Machines Basv2 Series          B16als v2 Low Priority   Consumption         -                     0.135   -
Virtual Machines Basv2 Series          B16als v2                Consumption         -                     0.673   Y
Virtual Machines Basv2 Series          B16als v2                Reservation         1 Year             3478.0     -
Virtual Machines Basv2 Series          B16als v2                Reservation         3 Years            6721.0     -
Virtual Machines Basv2 Series Windows  B16als v2                Consumption         -                     0.747   -
Virtual Machines Basv2 Series Windows  B16als v2                DevTestConsumption  -                     0.673   -
Virtual Machines Basv2 Series          B16als v2 Spot           Consumption         -                     0.6057  -
Virtual Machines Basv2 Series Windows  B16als v2 Spot           Consumption         -                     0.6723  -
Virtual Machines Basv2 Series Windows  B16als v2 Spot           DevTestConsumption  -                     0.6057  -
Basv2 Series Cloud Services            B16als v2                Consumption         -                     0.747   -
Virtual Machines Basv2 Series Windows  B16als v2 Low Priority   Consumption         -                     0.299   -
Virtual Machines Basv2 Series Windows  B16als v2 Low Priority   DevTestConsumption  -                     0.135   -
```

그중 세 행의 **JSON 전문**입니다. 한 필드도 지우지 않았습니다.

```json
{
  "currencyCode": "USD",
  "tierMinimumUnits": 0.0,
  "retailPrice": 0.299,
  "unitPrice": 0.299,
  "armRegionName": "australiacentral",
  "location": "AU Central",
  "effectiveStartDate": "2023-09-01T00:00:00Z",
  "meterId": "0080977d-4ce6-587d-9047-4ff3a1122b60",
  "meterName": "B16als v2 Low Priority",
  "productId": "DZH318Z0K9JD",
  "skuId": "DZH318Z0K9JD/00GN",
  "productName": "Basv2 Series Cloud Services",
  "skuName": "B16als v2 Low Priority",
  "serviceName": "Virtual Machines",
  "serviceId": "DZH313Z7MMC8",
  "serviceFamily": "Compute",
  "unitOfMeasure": "1 Hour",
  "type": "Consumption",
  "isPrimaryMeterRegion": true,
  "armSkuName": "Standard_B16als_v2"
}
```

```json
{
  "currencyCode": "USD",
  "tierMinimumUnits": 0.0,
  "retailPrice": 0.673,
  "unitPrice": 0.673,
  "armRegionName": "australiacentral",
  "location": "AU Central",
  "effectiveStartDate": "2023-09-01T00:00:00Z",
  "meterId": "25385797-d48b-5b47-9e0c-6ee37b6b9a8d",
  "meterName": "B16als v2",
  "productId": "DZH318Z0K9JH",
  "skuId": "DZH318Z0K9JH/00QW",
  "productName": "Virtual Machines Basv2 Series",
  "skuName": "B16als v2",
  "serviceName": "Virtual Machines",
  "serviceId": "DZH313Z7MMC8",
  "serviceFamily": "Compute",
  "unitOfMeasure": "1 Hour",
  "type": "Consumption",
  "isPrimaryMeterRegion": true,
  "armSkuName": "Standard_B16als_v2",
  "savingsPlan": [
    {
      "unitPrice": 0.34323,
      "retailPrice": 0.34323,
      "term": "3 Years"
    },
    {
      "unitPrice": 0.49129,
      "retailPrice": 0.49129,
      "term": "1 Year"
    }
  ]
}
```

```json
{
  "currencyCode": "USD",
  "tierMinimumUnits": 0.0,
  "reservationTerm": "1 Year",
  "retailPrice": 3478.0,
  "unitPrice": 3478.0,
  "armRegionName": "australiacentral",
  "location": "AU Central",
  "effectiveStartDate": "2023-09-01T00:00:00Z",
  "meterId": "25385797-d48b-5b47-9e0c-6ee37b6b9a8d",
  "meterName": "B16als v2",
  "productId": "DZH318Z0K9JH",
  "skuId": "DZH318Z0K9JH/03RM",
  "productName": "Virtual Machines Basv2 Series",
  "skuName": "B16als v2",
  "serviceName": "Virtual Machines",
  "serviceId": "DZH313Z7MMC8",
  "serviceFamily": "Compute",
  "unitOfMeasure": "1 Hour",
  "type": "Reservation",
  "isPrimaryMeterRegion": true,
  "armSkuName": "Standard_B16als_v2"
}
```

전문을 봐야 보이는 것들입니다.

- **13행 중 살아남는 것은 3행**입니다(온디맨드 0.673 · 스팟 0.6057 · 예약 2건).
  나머지 10행이 아래 함정 ①②와 `DevTest`·`Low Priority` 제외로 빠집니다.
- **`isPrimaryMeterRegion`이 세 행 모두 `true`**입니다. 처음에 이 칸을 판별자로
  짐작했다가 틀린 이유가 전문에 그대로 보입니다.
- **`savingsPlan`은 중첩 배열로 따로 붙고 단위가 진짜 시간당**입니다(0.49129/h).
  바로 아래 예약 행의 3478.0과 **같은 응답 안에서 단위가 다릅니다.**
- **구형 PaaS 행(0.747)과 Windows 행(0.747)이 값까지 같습니다** — 값만으로는 못
  가리고 `productName`을 봐야 갈립니다.

*함정 ①* — 같은 `armSkuName`에 `productName`이 `… Windows`로 끝나는 행이 **6행**
있습니다. 안 거르면 **라이선스 값이 섞여 두 배 가까이** 뜁니다.

*함정 ②* — 구형 PaaS가 같은 크기 이름을 씁니다. ①②를 안 거르면 **SKU의 93.4%가 값이
여럿**이 되고, 어느 값이 잡히는지는 응답 순서가 정합니다. 처음엔 `isPrimaryMeterRegion`이
범인이라고 짐작했는데 **틀렸습니다** — 둘 다 primary였습니다. 판별자는 `productName`입니다.

*함정 ③ — 5,165배.* 예약 가격의 `retailPrice`는 **기간 총액**인데 단위 칸에는 1,348건
전부 `'1 Hour'`라고 적혀 있습니다. 기간 시간으로 나눕니다(1년 8,760 / 3년 26,280):
`3478.00 ÷ 8760 = 0.3970/h`. 나눈 값이 온디맨드의 0.590(1년)·0.380(3년)이라 Azure가
공표하는 RI 할인율과 맞습니다. **`savingsPlan`은 반대로 진짜 시간당입니다** — 같은 응답
안에서 두 단위가 섞여 있으므로 **한 규칙으로 처리하면 한쪽이 틀립니다.**

**값이 하나일 때만 씁니다.** `_one()`이 여럿이면 무엇이 맞는지 우리가 모릅니다. 거를
것을 다 거르면 여럿인 경우가 0이라, **여럿이 나오면 그건 우리가 모르는 새 축이
생겼다**는 뜻입니다.

**미러와 대조합니다(허용 오차 0.5%).** 실측에서 API의 온디맨드가 미러와 완전히
일치했습니다 — koreasouth 겹침 551종·어긋남 0, eastus 1,219종·어긋남 0. 그래서 이건
보강인 동시에 **미러의 교차 확인**입니다. 다만 일치를 **가정하지 않고 확인해서**
`matchesMirror`에 담습니다.

**담지 않는 것.** `DevTestConsumption`(Dev/Test 구독에서만 적용 — 일반 사용자에게 그
값을 가격이라고 말하면 거짓) · `Low Priority`(스팟과 별개 미터인데 어디에 적용되는지 이
데이터로는 알 수 없음).

**버린 것을 이유별로 셉니다** — 279,234행을 버리고 32,073행을 담았습니다.

```json
"dropped": {"not-vm-or-windows": 99222, "devtest": 112026, "lowpriority": 52924,
            "not-in-mirror": 12940, "no-price": 120, "unknown-term": 2}
```

## 2.4 소스 ③ — `cyclenerd-gcp-pricing` (§27) · GCP 할인가 · **스냅샷 괴리**

`…/Cyclenerd/google-cloud-pricing-cost-calculator/574d8fbb68fa/pricing.yml`
· 커밋 핀 · **Apache-2.0**(파일 안 `about.copyright`에 명시)

**왜 필요했나.** 미러에 온디맨드뿐이라 못 답하던 GCP 스팟·약정을 메웁니다.

**핵심 판단 — 미러를 대체하지 않고 보강만 합니다.** 온디맨드는 tumblebug의
`hourlyUSD`가 그대로 남고, 스팟·약정만 별도 산출물에 담습니다. 미러 파일은 읽기만 하고
쓰지 않습니다.

**왜 그렇게 했나 — 두 소스가 다른 스냅샷이기 때문입니다.** Cyclenerd의 온디맨드는
tumblebug와 **리전×패밀리 단위로** 어긋납니다(실측: `n2d`/`asia-south1` 전 크기가 정확히
2.405배, `g4`/`asia-south2`가 0.385배). **크기마다 배율이 같으므로** 이건 가격 스냅샷
시점 차이이지 다른 스펙을 가리키는 게 아닙니다.

그래서 레코드에 **Cyclenerd 온디맨드(`hourRefUSD`)를 함께 담고** 비율(`mirrorRatio`)을
기록합니다.

```json
{"specName": "n2-highmem-8", "region": "asia-east1", "hourSpotUSD": 0.214704,
 "month1yUSD": 279.07608, "month3yUSD": 199.3484,
 "hourRefUSD": 0.6068, "mirrorRatio": 1.235, "snapshotMatchesMirror": false}
```

`snapshotMatchesMirror: false`가 **"이 스팟 값은 미러와 다른 가격 세계의 것"**이라고
레코드 스스로 말합니다. 이게 없으면 tumblebug 온디맨드 $0.17에 Cyclenerd 스팟 $0.044를
나란히 놓고 **"26%"라고 계산**하게 되는데, 그 스팟은 Cyclenerd 온디맨드 $0.147 기준입니다.

**자기정합성은 확인됐습니다** — 스팟이 온디맨드의 32%, 1년 63%, 3년 45%로 GCP 공식
할인율과 일치하고 스팟>온디맨드 이상치 0건. **값 자체는 믿을 수 있고, 문제는 두 스냅샷을
섞는 것입니다.**

## 2.5 소스 ④ — `aws-price-list` (§25) · **재배포 금지를 구조로 막았습니다**

| | |
|---|---|
| **받는 주소** | `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/20260721012550/ap-east-2/index.json` |
| **보는 주소** | <https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/20260721012550/ap-east-2/index.json> — **같은 주소이고 브라우저에서 열립니다**(43 MB). URL의 `20260721012550`이 곧 버전이라 **이 주소는 영원히 같은 파일**을 줍니다 |
| 핀 · 라이선스 | 버전 URL `20260721012550` · 라이선스 미확인 · ⚠ **재배포 거부(`denied`)** |

**두 갈래로 씁니다.**

**(a) 디스크 한도 교차 검증** — 3부(용량)에서 자세히 씁니다. 한 리전 오퍼 파일의
`products` 17,365개 안에 **디스크 한도가 가격 데이터의 속성으로** 들어 있습니다.

### 원본 전문 — `products` 항목 하나 (`6NFX9BJ63GJDFVBS`)

**항목 전체입니다.** 한 필드도 지우지 않았습니다.

```json
{
  "6NFX9BJ63GJDFVBS": {
    "sku": "6NFX9BJ63GJDFVBS",
    "productFamily": "Storage",
    "attributes": {
      "servicecode": "AmazonEC2",
      "location": "Asia Pacific (Taipei)",
      "locationType": "AWS Region",
      "storageMedia": "SSD-backed",
      "volumeType": "General Purpose",
      "maxVolumeSize": "16 TiB",
      "maxIopsvolume": "16000",
      "maxIopsBurstPerformance": "3000 for volumes <= 1 TiB",
      "maxThroughputvolume": "250 MiB/s",
      "usagetype": "APE2-EBS:VolumeUsage.gp2",
      "operation": "",
      "regionCode": "ap-east-2",
      "servicename": "Amazon Elastic Compute Cloud",
      "volumeApiName": "gp2"
    }
  }
}
```

전문을 봐야 보이는 것들입니다.

- **한 항목 안에 단위 표기가 네 가지**입니다 — `"16 TiB"`(단위 포함) ·
  `"16000"`(맨 숫자) · `"250 MiB/s"`(단위 포함) · `"3000 for volumes <= 1 TiB"`
  (**조건이 붙은 산문**). 마지막 것은 숫자로 접을 수 없어 담지 않습니다.
- **`maxVolumeSize`가 TiB이고 우리 산출물은 GiB**입니다. 16 TiB = 16,384 GiB로 맞춰야
  botocore의 `1 - 16,384 GiB`와 대조됩니다.
- **`locationType`이 여기 있습니다.** Outposts·Local Zone 행을 거르는 칸이 이것입니다.
- **가격이 이 블록에 없습니다.** `products`는 속성만 담고 값은 별도 `terms` 트리에
  있어서, **한도만 가져오고 가격은 안 가져오는 것이 구조적으로 가능**합니다.

**(b) 관리형 과금 축** — **로컬 빌드 전용**입니다. 축 판정이 **단위 문자열의 함정**을
지납니다.

```
Hrs (맨 시간)               instanceHour   RDS 인스턴스·EKS 클러스터
*CapacityUnit*-Hrs          capacityRate   DynamoDB RCU/WCU — 단위 수가 사이징 결과
GB-Mo · *-months            capacityRate   저장 용량 × 시간
LCU-Hrs · Requests · GB …   usage          부하가 소비하는 단위 — 트래픽 의존
```

DynamoDB의 `ReadCapacityUnit-Hrs`는 단위가 시간이지만 **용량 단위 수에 비례**합니다.
LCU는 반대로 이름이 용량이지만 **부하가 소비**하므로 사용량입니다. **모르는 단위는
`usage`로 둡니다** — 시간당 단가를 지어내는 방향보다 안전합니다.

**법적 처리를 구조로 했습니다.** 약관이 가격 데이터 재배포를 **명시적으로 금지**합니다
(문구가 *없는* Azure·IBM과 달리 *안 된다고 적혀 있는* 경우). 그래서

```
python -m costkb build-aws-managed          # 각자 로컬에서
data/aws-managed-pricing.json.gz            # 존재하면 안 된다 — 테스트가 막는다
```

`pack` 명령이 **파일 이름을 보고 거절**하고, `data/`에 그 파일이 있으면 **테스트가
실패**합니다. *"커밋하지 마세요"라는 주석 대신 구조로 막은 것입니다.* 클론 직후 aws
관리형 축이 비어 있는 것이 **정상**이고, 도구가 빌드 명령을 안내합니다.

## 2.6 이 축이 지금 답할 수 있는 것 (2026-07-29 실측)

| | AWS | Azure | GCP | 나머지 |
|---|---:|---:|---:|---|
| **정가** | 18,137 | 32,925 | 10,626 | ibm 1,926 · tencent 2,863 · alibaba 1,954 · ncp 178 · nhn 65 · kt 31 · openstack 0 · **oracle 0** |
| **스팟** | — | 31,693 | 11,121 | — |
| **예약 1년** | — | 28,498 | (월 단위 11,193) | — |
| **저축 플랜** | — | 31,370 | — | — |
| **관리형** | **로컬만** | 23,563 | 731 | — |

할인가 합계 **43,266건**(azure 32,073 + gcp 11,193) · 관리형 합계 **24,294건**
(azure 23,563 + gcp 731).

**AWS에 할인가가 없는 것은 의도입니다** — 예약·약정 가격 조합이 너무 커서 일부러 안
담고, 대신 **답변에 이유를 밝힙니다.**

관리형 축의 성격 분포(azure): `usage` 8,717 · `instanceHour` 6,585 · `capacityRate` 8,261.

## 2.7 이 축에서 일부러 안 하는 것

| 안 하는 것 | 왜 |
|---|---|
| **총 비용 합계** | 일부 항목만 값을 아는데 합계를 내면 **그럴듯한 거짓 총액**이 됩니다. 대신 "축별로 나눠서 + 모르는 것 N건" |
| **관리형 단가를 한 칸으로 접기** | `instanceHour`만 유효한 시간당 요율입니다. `capacityRate`는 곱할 수량(vCore·RU·GB)이 **사이징 결과**이고, `usage`는 트래픽을 알아야 비용이 생깁니다. **사용량 축에 숫자 하나를 붙이는 것이 곧 모르는 것을 채우는 실패**입니다 |
| **두 스냅샷의 가격을 섞어 비율 계산** | 2.4 참조 |
| **`Low Priority`를 스팟으로 취급** | 별개 미터인데 어디 적용되는지 이 데이터로는 모릅니다 |
| **가격 없는 스펙을 0으로 세기** | 2.2 표 참조. 4,378건은 **모르는 것이지 공짜가 아닙니다** |

## 2.8 이 축의 타당성 위협

- **정가 단일 소스.** 68,705건 전부가 `tumblebug-dump`에서 나옵니다. 다만 Azure는
  §26으로 우연히 교차 확인이 됐습니다(어긋남 0).
- **스냅샷 시점 혼재.** 미러(v0.12.25 태그 시점) · Azure API(지문 핀, 매번 다름) ·
  Cyclenerd(커밋 핀)의 **가격 기준 시점이 서로 다릅니다.** 레코드에 `matchesMirror` ·
  `mirrorRatio` · `snapshotMatchesMirror`로 그 사실을 싣지만, **한 시점으로 통일하지는
  못했습니다.**
- **AWS 할인 축 부재**는 근거 부족이 아니라 **범위 결정**입니다 — 리뷰어가 두 사건을
  구별할 수 있도록 여기 적어 둡니다.
- **관리형 축의 아키타입 경계.** `serviceName`이 경계를 안 지킵니다 —
  `Azure Database for PostgreSQL` 안에 `Azure Cosmos DB for PostgreSQL`이 섞여
  있습니다(실측). 판별자가 `productName`이고 **큐레이션 표가 그 일을 합니다** — 손
  큐레이션이라 다르게 틀릴 수 있습니다.
- **재배포 제약이 커버리지에 반영됩니다.** AWS 관리형 축이 `data/`에 없는 것은
  데이터가 없어서가 아니라 **법적 제약**입니다. 이 둘을 섞어 읽으면 안 됩니다.

---

# 3부 — 클라우드 리소스 용량

> 과제 원문: *"클라우드 리소스의 특성(**리소스 용량**, 리소스 의존성 등)"*

## 3.0 정의 — 이 축은 무엇인가

> **클라우드 리소스 용량**이란, 리소스 타입의 **속성 하나에 걸린 한계**를 말한다.
> *"얼마까지 되나"* · *"만들고 나서 바꿀 수 있나"* · *"그 한계가 언제 적용되나"*의
> 셋을 **한 모델로** 담는다. 별도로, 리소스가 아니라 **계정·구독에 걸리는 상한**은
> 다른 모델(Quota)로 담는다.

"용량"이라는 낱말이 크기만 가리키는 것처럼 들리지만, 실제로 배포를 막는 것은 **크기보다
변경 가능성**인 경우가 많습니다 — 실측상 AWS 스키마 1,628개 중 **86.5%가
`createOnlyProperties`를 명시**합니다. 그래서 이 축은 값 제약과 변경 제약을 같은 무게로
담습니다.

### 관측 단위 — **narrow 모델**

**`(type_id, property, kind, conditions)` 하나가 레코드 하나**입니다.

> **왜 프로퍼티당 1행(wide)이 아닌가.** 같은 프로퍼티라도 **제약 종류마다 근거가
> 다릅니다.** `AWS::Lambda::Function.Timeout`은 `min=1`이 스키마 필드(**stated**)인데
> `max=900`은 설명문에서 추출한 것(**inferred**)입니다. 프로퍼티당 한 행으로 접으면
> **한 행에 근거가 둘**이 되어 어느 값이 믿을 만한지 말할 수 없게 됩니다.

**조건이 키에 들어갑니다.** 안 그러면 볼륨 종류별 6개 레코드가 하나로 접혀서, 조건부
제약을 담으려던 것이 **도로 봉투가 됩니다**(아래).

### 용어 — 제약의 종류와 조건

**`kind` — 제약의 종류 12가지.**

```
값 제약    min · max · min_length · max_length · min_items · max_items
           pattern · enum · default
변경 제약  required · mutability
읽기 제약  secret
```

**`mutability`의 세 값** — *"만들고 나서 어떻게 되나"*.

| 값 | 뜻 |
|---|---|
| `create_only` | **만들 때만 정할 수 있다.** 바꾸려면 리소스를 다시 만들어야 한다 |
| `conditional_create_only` | **조건에 따라** 그렇게 된다 |
| `read_only` | 우리가 정하는 값이 아니다 — 만들고 나면 시스템이 채운다 |

**`secret`은 `mutability`와 겹치지 않습니다** — 그쪽은 *"바꾸면 재생성되나"*이고 이쪽은
*"다시 읽을 수 있나"*입니다. **두 축이 직교하므로 같은 속성에 둘 다 붙을 수 있고 그래도
중복이 아닙니다.**

**`conditions` — 이 제약이 언제 적용되는가.** 조건들의 **논리곱**(전부 성립해야 적용)
이고, 비어 있으면 무조건입니다.

```
{"property": "VolumeType", "op": "eq",      "value": "gp2"}      그 속성이 이 값일 때
{"property": "EngineVersion", "op": "matches", "value": "10\\.11.*"}  이 정규식에 걸릴 때
```

> **왜 필요한가 — 봉투 붕괴 때문입니다.** EBS 볼륨 크기 한도는 종류마다 다릅니다
> (gp2 16,384 / gp3 65,536 / standard 1,024 GiB). 이걸 min/max **한 쌍으로 뭉개면**
> 최소 중 최소·최대 중 최대를 취해 **어떤 실제 설정에도 해당하지 않는 범위**가 됩니다 —
> `standard` 볼륨에 5,000 GiB는 불가능한데 **그 봉투는 통과시킵니다.**
>
> **단일 조건에서 목록으로 넓힌 이유도 실측입니다.** 설계 문서에 *"필요해지면 그때
> 넓힌다"*고 적어 뒀는데 그때가 왔습니다 — cfn-lint의 RDS 인스턴스 클래스는 조건이
> 둘이고(`Engine` **그리고** `EngineVersion`, 938블록), 게다가 둘째는 등호가 아니라
> 패턴이라 **`op`도 함께 넓혀야** 했습니다.

**`backend` — 이 레코드를 만들어낸 상류 파이프라인.** 같은 `evidence` 라벨이라도
**신선도가 다를 수 있어서** 둡니다. GCP(KCC)가 실례입니다 — 백엔드가 셋이고 그중
`tf2crd`(우리 GCP 제약의 55%)는 **2023-09-26에 나온 4.84.0을 벤더링한 것**에서 스키마를
뽑습니다. 이게 없으면 **절반이 2년 8개월 묵었다는 걸 사용자가 알 방법이 없습니다.**
**등급(`tier`)이 아니라 사실을 적는 칸입니다** — 위아래를 이름에 박으면 나중에 판단이
바뀔 때 이름이 거짓이 됩니다.

**Quota — 별개 모델.** 계정/구독/리전 등 **스코프 단위의 상한**입니다(예: vNet당 서브넷
3,000개). `provider` · `name` · `scope` · `default` · `maximum` · `type_id`를 갖고,
**리소스 속성이 아니라 계정에 걸리므로** Constraint와 섞지 않습니다.

### 무엇이 이 축이 **아닌가** — 경계

| 아닌 것 | 왜 / 어디로 |
|---|---|
| 실제 사용량·잔여 쿼터 | 자격증명이 필요한 실시간 값입니다 |
| 쿼터를 올려 받을 수 있는가 | 판정하지 않습니다 — 지원 요청의 결과입니다 |
| 리소스 **사이의** 제약 | **④ 의존성** |
| "함께 있어야 한다"는 관행 | **⑤ 리소스 군** — 그건 제약이 아니라 관찰입니다 |
| 인스턴스가 낼 수 있는 IOPS | **① 성능** |
| 근거 없는 최소 규모 | **담지 않습니다** — 손 입력분은 `reviewed-sizing`으로 갈라 둡니다(3.6) |

### 레코드 실물

```json
{"type_id": "aws::AWS::EC2::Volume", "property": "Size", "kind": "max",
 "value": 16384, "value_type": null, "unit": "GiB", "conditional": false,
 "note": null, "evidence": "aws-cross-checked", "basis": "stated",
 "backend": null, "conditions": [{"property": "VolumeType", "op": "eq", "value": "gp2"}]}
```

> **출처** — `data/aws-limits.json.gz` ← **§10 `botocore`(태그 `1.43.52`) × §25
> `aws-price-list`(버전 URL `20260721012550`)**. 파일의 `_source`에 **소스가 둘**
> 들어 있고, 그것이 이 파일의 성격입니다 — **두 소스가 같은 값을 말한 20건만** 담겨
> 있어서 출처가 하나면 성립하지 않습니다.
>
> `evidence`가 소스 이름이 아니라 **`aws-cross-checked`인 것이 핵심**입니다. 소스
> 이름을 적으면 *"botocore가 그렇게 말했다"*가 되어 **교차 검증됐다는 사실이
> 사라집니다.** 같은 축의 다른 파일은 소스별 라벨을 씁니다 — `cfn-schema`(47,070건 중
> 46,911) · `cfn-description`(159) · `bicep-flags` · `bicep-type` · `kcc-crd-schema` ·
> `tpg-schema` · `tpcsp-schema`. **라벨 하나에 성격 하나**가 규칙이라, 근거의 성격이
> 갈리면 라벨을 쪼갭니다.

### 섞인 두 소스를 각각 원본에서 보기

이 20건은 **두 원본이 같은 값을 말했을 때만** 담긴 것이라, 두 원본을 나란히 놓고 보는
것이 곧 검증입니다.

**① §10 `botocore`** — 최솟값까지 있으나 **설명문 안에** 있음

- 보는 주소: <https://github.com/boto/botocore/blob/1.43.52/botocore/data/ec2/2016-11-15/service-2.json>
  (큰 JSON이라 GitHub에서 `CreateVolumeRequest`로 검색하면 바로 찾습니다)

**원본 전문** — `shapes.CreateVolumeRequest.members.Size`와 그것이 가리키는
`shapes.Integer`입니다. **설명문을 한 글자도 줄이지 않았습니다.**

```json
{
  "Size": {
    "shape": "Integer",
    "documentation": "<p>The size of the volume, in GiBs. You must specify either a snapshot ID or a volume size. If you specify a snapshot, the default is the snapshot size, and you can specify a volume size that is equal to or larger than the snapshot size.</p> <p>Valid sizes:</p> <ul> <li> <p>gp2: <code>1 - 16,384</code> GiB</p> </li> <li> <p>gp3: <code>1 - 65,536</code> GiB</p> </li> <li> <p>io1: <code>4 - 16,384</code> GiB</p> </li> <li> <p>io2: <code>4 - 65,536</code> GiB</p> </li> <li> <p>st1 and sc1: <code>125 - 16,384</code> GiB</p> </li> <li> <p>standard: <code>1 - 1024</code> GiB</p> </li> </ul>"
  }
}
```

```json
{
  "Integer": {
    "type": "integer"
  }
}
```

전문을 봐야 보이는 것들입니다.

- **`Size`에 `min`도 `max`도 없습니다.** 제약은 전부 `documentation` 문자열 안에
  HTML로 들어 있고, `shape`이 가리키는 `Integer`는 **아무 제약도 없는 공용 타입**입니다.
  두 블록을 나란히 놓아야 *"EBS 한도에 한해 설명문이 유일한 출처"*가 확인됩니다.
- **볼륨 종류가 여섯**입니다(gp2·gp3·io1·io2·st1/sc1·standard). 하나의 min/max로
  뭉개면 **`standard`에 5,000 GiB가 통과하는 봉투**가 되는 이유가 여기 보입니다.
- **`st1 and sc1`이 한 항목**입니다. 종류 이름을 기계로 자를 때 이 형태를 따로
  처리해야 합니다.
- **숫자에 천단위 콤마가 있습니다**(`1 - 16,384`).

**② §25 `aws-price-list`** — 최댓값만 있고 **값이 반쯤 산문**

- 보는 주소: <https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/20260721012550/ap-east-2/index.json>
  **브라우저에서 열립니다**(43 MB — 큽니다). URL의 `20260721012550`이 곧 버전이라
  **이 주소는 우리가 받은 것과 영원히 같은 파일**을 줍니다.
- ⚠ **약관이 가격 데이터 재배포를 금지합니다** — 열어 보는 것은 되지만 파생 산출물을
  커밋할 수 없고, 그 제약이 구조로 강제돼 있습니다(2.5).
- **원본 전문은 2.5에** 있습니다(`products` 항목 하나 전체).

> **`gp2`의 IOPS가 왜 안 담겼는지 두 원본을 나란히 놓으면 바로 보입니다** — ②에는
> `maxIopsvolume: "16000"`이 있는데 ①의 설명문에는 **gp2의 IOPS 목록이 아예
> 없습니다**(위 전문에 크기만 있고 IOPS가 없습니다). **한쪽에만 있으면 담지 않는다**는
> 규칙이라 빠졌고, 담았으면 **없는 제약을 만들 뻔했습니다.**

**③ 이 축의 최대 소스는 저장소가 없습니다** — §8 `cfn-schema`(46,911건)는 AWS가 같은
URL에 zip을 덮어쓰는 방식이라 **보는 주소를 만들 수 없습니다.**

- 받는 주소: <https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip>
  (브라우저로 열면 **zip이 내려받아집니다** — 풀면 타입별 JSON 1,635개)
- 개별 타입은 AWS 문서에서 볼 수 있습니다 —
  <https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-ec2-volume.html>
  다만 **문서는 zip과 1:1 대응이 보장되지 않습니다.** 우리가 쓴 zip과 같은 것인지
  확인하는 방법은 **`_source`의 sha256뿐**입니다(`83b88800e04b…`).

`type_id`는 **graphkb 노드 id와 같은 규약**입니다. 두 지식베이스는 코드가 분리돼 있지만
**이 규약 덕분에 질의 시점에 조인**됩니다 — ③과 ④를 함께 답할 수 있는 근거입니다.

## 3.1 이 축이 답해야 하는 질문

"용량"은 리소스 하나에 얼마까지 넣을 수 있는가입니다. 실제로는 **여섯 종류의 제약**이
섞여 있고, 이 구분이 답의 성격을 정합니다.

```
값 제약     min/max · minLength/maxLength · pattern · enum · default
변경 제약   required · create_only(만들고 나면 못 바꿈) · read_only
조건부 제약 "엔진이 aurora-mysql이면 이 타입만" — 조건을 알아야 판정이 갈림
지역 제약   "이 리전에서 쓸 수 있는 인스턴스 종류"
구독 한도   "구독당 가상 네트워크 1,000개" — 리소스가 아니라 계정에 붙는 한도
최소 규모   "이걸 돌리려면 최소 몇이 필요한가"
```

## 3.2 이 축의 대표 답변 — 3상태

이 축의 성격을 한 화면으로 보여 주는 실물입니다.

```
질문) AWS 디스크에 30,000 GiB 넣어도 되나? (디스크 종류 = gp2)
답)   안 됩니다. 30,000 GiB는 최대 16,384 GiB를 넘습니다
      (단, VolumeType='gp2'일 때. 공식 소스 두 곳에서 교차 확인)

질문) 같은 질문, 디스크 종류 = gp3
답)   됩니다.

질문) 같은 질문, 디스크 종류를 안 알려줌
답)   조건에 따라 갈립니다. 14가지 조건 중 9개는 허용, 5개는 불허.
```

**모르면 한쪽으로 찍지 않습니다.** "아마 되겠죠"라고 하면 gp2 디스크를 망가뜨리고,
"안 됩니다"라고 하면 gp3로 하면 되는 길을 막습니다. 그래서 **세 번째 상태**를 그대로
답합니다.

## 3.3 침묵도 다섯 종류다 — 이 축의 핵심 설계

| 상황 | 실제 답변 | 사용자가 해야 할 일 |
|---|---|---|
| 봤는데 제약이 없다 | "알려진 제약 없음 — **수집 범위 안이므로 '없음'이 답**" | 그대로 진행 |
| 아예 안 봤다 | "**수집 범위 밖**이라 모릅니다 — 없다는 뜻이 아닙니다" | 직접 확인 |
| 있는데 못 읽었다 | "제약은 있으나 그 정규식을 우리가 못 읽습니다" + 원문 | 원문 읽기 |
| 조건에 안 걸린다 | "조건부 제약 938건 중 **주신 조합에 걸리는 것이 없습니다**" | 오타 의심 |
| 그 회사는 추적 안 함 | "ncp는 그 축을 추적하지 않습니다" | 다른 곳 |

옛날에는 이 다섯이 **전부 같은 출력**이었습니다 — 함수가 넷 다 `None`을 반환해서 화면에
나오는 글자가 **바이트 단위로 똑같았습니다.** 검증된 안전한 값과 우리가 아무것도 모르는
값이 구별되지 않았다는 뜻입니다.

## 3.4 소스 그룹 A — 회사가 기계에 주라고 만든 스키마 (5종)

**공통 원칙: 사람이 읽는 문서를 긁지 않습니다.** 회사의 배포 시스템이 실제로 쓰는
스키마를 씁니다 — **틀리면 그 회사가 먼저 아프기** 때문입니다.

### `cfn-schema` (§8) — AWS · **최대 기여 소스이자 최대 약점**

`https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip`
· **지문 핀** · 라이선스 미확인 · zip 안에 JSON 1,635개

**왜 필요했나.** AWS 리소스 1,638종의 속성·타입·한도를 회사가 직접 배포하는 형식으로
줍니다. **근거 라벨 중 최대인 46,911건**이 여기서 나옵니다.

**어떻게 쓰나 — 제약 세 종류.**

1. **값 제약** — `minimum`/`maximum`/`minLength`/`pattern`/`enum`/`default`
   → evidence `cfn-schema` (**stated**)
2. **변경 제약** — `required`/`createOnlyProperties`/`conditionalCreateOnlyProperties`/
   `readOnlyProperties` → 같은 evidence. **실측상 이쪽이 훨씬 풍부합니다**
   (스키마 1,628개 중 **86.5%**가 `createOnlyProperties`를 명시).
3. **산문 제약** — 중요한 숫자가 `description` 산문 안에만 있는 경우가 많습니다.

```json
"Size": {"type": "integer",
  "description": "The size of the volume, in GiBs. … Supported volume sizes:
    \n  +  gp2: 1 - 16,384 GiB\n  +  gp3: 1 - 65,536 GiB …"}
```

`Size`는 `"type": "integer"`일 뿐 `minimum`/`maximum`이 없고, **실제 한도는 문장
속에** 있습니다. `prose.py`가 뽑되 evidence는 `cfn-description`(**inferred**)이고
**오탐 방어 셋**이 붙습니다.

- **R1** 같은 (타입, 프로퍼티, 종류)에 스키마 값이 이미 있으면 산문은 만들지 않는다
- **R2** 산문 값이 스키마 값과 모순이면(산문 max < 스키마 min 등) 버리고 경고한다
- **게이트** 산문 범위는 `type`이 integer/number인 프로퍼티에만 적용한다 —
  `Lambda.EphemeralStorage`처럼 산문이 `$ref` 래퍼에 붙고 실제 제약이 한 단계 아래에
  있는 함정을 이 규칙이 막습니다

**이 소스의 가장 큰 약점.** AWS가 **같은 URL에 zip 하나를 계속 덮어씁니다.** 버전도
태그도 아카이브도 없습니다. 재빌드했을 때 고정된 소스는 결과가 완전히 같았고 **AWS만
46,810 → 47,109로 변했습니다.** 즉 **이 창고 최대의 근거가 재현 불가능**합니다.
남길 수 있는 것은 프로버넌스의 sha256뿐이고, 실제로 그 기록이 캐시와 라이브의 차이를
잡았습니다.

**산출:** `data/aws-capacity.json.gz` — `constraints` **47,070건**
(`cfn-schema` 46,911 + `cfn-description` 159).

### `botocore` (§10) + `aws-price-list` (§25) — **교차 검증해서만 담습니다**

이 짝이 이 축의 규율을 가장 잘 보여 줍니다.

```
Price List 벌크 API   maxVolumeSize / maxIopsvolume / maxThroughputvolume
                      구조화돼 있지만 **최댓값만** 있고 값이 반쯤 산문("16 TiB")
botocore 설명문        규칙적인 목록이라 **최솟값까지** 있다(gp2: 1 - 16,384 GiB)
                      shape 자체에는 제약이 없어 산문이 유일한 출처
```

1. 두 소스에서 (볼륨 종류, 종류별 한도)를 각각 뽑습니다.
2. **10쌍 전부 일치한 20건만 담습니다.** 어긋나면 담지 않고 미결로 보고합니다.
3. `gp2`의 IOPS는 **한쪽에만 있어서 안 담았습니다** — 한쪽만 보고 담았으면 **없는 제약을
   만들 뻔했습니다.**
4. **여기가 `conditions`가 처음 쓰인 곳입니다.** 볼륨 크기 한도는 종류마다 달라서 min/max
   한 쌍으로는 못 담습니다 — 뭉개면 `standard` 볼륨 5,000 GiB처럼 **불가능한 값이
   통과하는** 봉투가 됩니다.

**정직한 정정 기록.** 예전 주석은 *"botocore shape에 min/max가 없다"*였는데
**틀렸습니다.** EC2만 봐도 shape 4,069개 중 min 183 · max 175 · enum 457개가 있습니다.
참인 것은 EBS의 `Size`·`Iops`·`Throughput`이 **제약 없는 공용 `Integer` shape을
가리킨다**는 것뿐인데 그 관찰을 전체로 일반화했습니다 — **우리가 막으려는 '확신에 찬
오답'을 우리 주석이 저지른 사례**로 남겨 뒀습니다.

**산출:** `data/aws-limits.json.gz` — `constraints` **20건**(evidence `aws-cross-checked`).
이 20건이 3.2의 3상태 답변을 만드는 데이터입니다.

### `cfn-lint` (§12) — AWS의 조건부·리전별 허용값

`…/cfn_lint-1.53.1-py3-none-any.whl` · PyPI 해시 URL(내용 주소) · **MIT-0**

**왜 필요했나 — 이 항목은 "원본에 없다"를 확인한 결과입니다.** **AWS 공식 스키마에는
조건부 표현이 아예 없습니다**(`if`/`then` 전수 **0건**). 메타스키마가 금지하고 있어
cfn-lint가 별도 파일로 관리합니다.

```json
{"if":   {"properties": {"Engine": {"enum": ["aurora-mysql", "aurora-postgresql"]}},
          "required": ["Engine"]},
 "then": {"properties": {"AllocatedStorage": false, "Iops": false,
                         "StorageType": {"then": {"enum": ["aurora", "aurora-iopt1"]}}}}}
```

**함정 — `"all"` 키를 리전으로 읽으면 안 됩니다.** `aws_ec2_instance/instancetype_enum.json`의
`"all"`은 **빈 enum**입니다(이 파일 하나만 그렇습니다). 리전인 줄 알고 담으면 "허용값이
0개"인 제약이 생기고 **모든 인스턴스 타입이 거부됩니다.** 하필 값이 가장 많은 파일입니다.
그래서 **리전 모양(`us-east-1` 꼴)에 맞는 키만** 읽습니다.

**속성 이름은 짐작하지 않고 대조합니다.** 파일명에서 후보를 만들되 **CFN 스키마에
실재하는지 확인**하고 없으면 담지 않고 셉니다.

**산출:** `aws-conditional` **966건** · `aws-regions` **385건**((리전, 값) 쌍 79,809개를
품음).

### `bicep-types-az` (§13) — Azure · **2위 기여 소스**

`…/Azure/bicep-types-az/ef7421bb…/generated` · 커밋 핀(태그가 `v0.0-test` 류뿐) · MIT

**어떻게 쓰나.**

```
IntegerType  minValue/maxValue                → min / max
StringType   minLength/maxLength/pattern      → min_length / max_length / pattern
ArrayType    minLength/maxLength              → min_items / max_items
UnionType    (전부 StringLiteralType일 때)     → enum
flags 비트    1=required · 2=read_only         → required / mutability
```

**주의 — `flags` 8(DeployTimeConstant)은 불변성이 아닙니다.** name/type/apiVersion에만
붙는 배포 시점 상수 표시라 mutability로 쓰면 안 됩니다(`flags: 10`은 `2|8`,
`9`는 `1|8`).

**못 주는 것 — 불변성 0건.** 원본에 없어서가 아니라 **변환기가 떨어뜨립니다.**
`azure-rest-api-specs`의 `x-ms-mutability: ["read","create"]`가 생성 불변성인데, bicep
생성기가 이를 writable&readable로 접어 `flags: None`으로 만듭니다
(`ObjectTypePropertyFlags`에 `Immutable` 멤버 자체가 없습니다). 우리 캐시에서
`x-ms-mutability` 출현은 **0건**입니다.

> 다만 *"bicep이 제약을 잃는다"*는 일반화는 **틀렸습니다.** `pattern` 920 ·
> `maxLength` 827 · `minValue` 446 · `maxValue` 337은 그대로 있고 이 파서가 전부
> 소비합니다. **잃는 건 불변성 하나**입니다.

**산출:** `data/azure-capacity.json.gz` — `constraints` **42,831건**
(`bicep-flags` 36,724 + `bicep-type` 6,107) · 타입 3,371종.

### `azure-rest-api-specs` (§14) — 필드 **셋만** 캐냅니다

`…/Azure/azure-rest-api-specs/tar.gz/76ca9f3e…` · 커밋 핀 · tarball 200.5 MB

**왜 이것만인가.** 191MB 저장소에서 필드 셋을 캐는 것이라, *"왜 이것만 쓰나"*가 기록돼
있지 않으면 다음 사람이 헷갈립니다. 나머지 제약은 §13이 이미 담고 있습니다. 세 파서 모두
**네임스페이스별 최신 stable 하나**만 봅니다(stable 스펙 6,842개 → 최신만 1,422개).

**① `x-ms-mutability`** — `create`가 있으면 담되 `update` 유무로
`create_only`/`updatable`을 가릅니다. 예전엔 `update`가 있으면 그냥 버렸는데, 그러면
**원본이 명시한 사실이 "우리가 모른다"와 같은 모양**이 됐습니다.

> **ARM 규약을 일반화하지 않은 이유 — 반례를 셌습니다.** *"ARM은 이름과 리전을 못
> 바꾼다"*를 규약으로 선언해 4,358건을 채우자는 안이 있었습니다. 원본을 세어 보니
> `name`은 반례 0건이지만 `location`은 **반례 2건**이 있었습니다
> (`Microsoft.DocumentDB/cassandraClusters`, `Microsoft.Capacity/reservationOrders`가
> `[create,read,update]`). 규약으로 채웠다면 **최소 2종에서 거짓을 단언**했을 것이고,
> 어느 2종인지 알 방법도 없었을 것입니다. **표시가 붙은 것만 담습니다.**

**② `x-ms-long-running-operation`** — **모순은 담지 않습니다.** 같은 타입·같은 메서드를
파일마다 다르게 말하는 것이 15종 있습니다. 어느 쪽이 맞는지 모르는데 하나를 고르면 그건
우리 짐작입니다. **POST 액션 경로는 마지막 마디를 떼고** 타입을 구합니다 —
`/virtualMachines/{vm}/start`에서 `start`는 액션이지 타입이 아닙니다.

**③ `x-ms-secret`** — **PUT 본문에 있는 것만** 담습니다. 응답에만 나오는 secret은 오히려
**읽을 수 있는** 것이라 "다시 못 읽는다"는 이 축의 뜻과 어긋납니다.

**고친 문제의 크기.** ①이 없을 때 **Azure 타입 3,371종 전부가 "변경 불가로 알려진
속성이 없습니다"**라고 답하고 있었습니다 — **데이터 부재가 사실 부재로 읽히는 최대 규모
사례**였습니다.

**산출:** `azure-mutability` **1,275건** · `azure-operations` **1,839건**(conflicting 15) ·
`azure-secret` **230건**.

### `kcc-crd` (§15) — GCP

`…/GoogleCloudPlatform/k8s-config-connector/v1.153.0` · 태그 핀 · Apache-2.0

**먼저 알아야 할 것 — 여기서 수치 한도는 나오지 않습니다.** CRD 510개의 `spec` 서브트리
전수 집계입니다.

```
required            2,631건 / 474종
Immutable. 접두사   2,187건 / 363종
enum                   17건 /  12종
pattern                 6건 /   5종
default                 7건 /   2종
maxLength               1건 /   1종
minimum · maximum       0건 /   0종
```

이 파서가 메우는 것은 **커버리지**이지 "얼마까지 되나"가 아닙니다. 나중에 *"GCP 제약이
왜 이것뿐이냐"*는 질문에 **안 뽑아서가 아니라 원본에 없어서**라고 답할 수 있어야 합니다.

**불변성은 두 표기를 합집합으로 읽습니다** — 둘이 일치하지 않기 때문입니다(실측):

```
둘 다                55건
CEL만 (접두사 없음)  19건
접두사만          2,132건
```

접두사가 있는데 CEL이 변경을 허용하는 **모순은 0건**입니다. 즉 접두사는 과다 보고를
하지 않고 **누락만** 합니다 — 그래서 짐작이 아니라 명시로 취급하고, 어느 쪽이 근거인지를
evidence로 남깁니다(`kcc-immutable-prefix` vs `kcc-cel-immutable`).

**`backend` 칸이 이 소스의 함정을 드러냅니다.** 하나처럼 보이지만 내부적으로 **세
파이프라인**(`tf2crd`·`dcl2crd`·`direct`)이 섞여 있고, `tf2crd`는 vendoring된
terraform-provider-google **4.84.0(2023-09-26)** 기준입니다. 그래서 레코드마다 어느
파이프라인에서 왔는지를 **등급이 아니라 사실로** 적습니다 — *위아래를 이름에 박으면
나중에 판단이 바뀔 때 이름이 거짓이 됩니다.*

## 3.5 소스 그룹 B — 커뮤니티가 만든 스키마: Terraform provider (9종)

공개 스키마가 없는 클라우드가 많습니다. **그런 곳은 Terraform provider가 유일한
경로**입니다. 전부 MPL-2.0이고 원본이 **Go 소스 코드**라는 점이 공통입니다.

> **아홉 다 같은 경고를 답니다.** `ForceNew`는 *"Terraform이 재생성한다"*이지 *"API가
> 거부한다"*가 아니고, `validation.IntBetween`은 **프로바이더 작성자의 주장**입니다.
> 그래서 표시 문구는 반드시 `"바꾸면 리소스 재생성"`이어야 하고 `"API가 거부한다"`면
> **거짓이 됩니다.**

### `tpaws-provider` (§16) — AWS 교차 필드 조건

**왜 필요했나.** CloudFormation은 조건부를 표현할 방법이 없습니다(메타스키마가
`if`/`then`을 금지하고 `oneOf`는 값이 아니라 존재 조건만 말합니다). 그래서 우리 AWS
데이터에는 **교차 필드 조건이 0건**이고 **조건부 불변도 0건**이었습니다. 프로바이더에는
둘 다 있습니다 — 실측 v6.55.0: 교차 조건 1,219 · `ForceNewIf` 56.

**`tpg`와 성격이 다릅니다.** 생성 코드 비율이 google 100% / **aws 19%**입니다. google은
"선언은 있는데 생성 중 증발"이 문제였지만(빈 목록 194건), aws는 사람이 쓴 Go라 빈 목록이
**0건**입니다. 대신 **손 큐레이션이라 다르게 틀릴 수 있어서** 근거 라벨을 나눕니다
(`tpaws-schema` vs `tpg-schema`).

**이름 잇기.** TF 리소스 이름과 CFN 타입 이름은 규칙이 다릅니다
(`aws_prometheus_scraper` → `AWS::APS::Scraper`). `names_data.hcl`의 `arn_namespace`로
서비스를 잇고 나머지는 이름 후보로 맞춥니다. **실측 매칭률 50%**, 못 맞춘 것은 버리되
셉니다 — 상당수는 매핑 실패가 아니라 **CFN에 그 리소스가 아예 없는 것**입니다.

**산출:** `data/aws-tf.json.gz` — **2,756건** / 338종.

### `tpg-provider` (§17) — GCP 최신값

**왜 Magic Modules YAML이 아니라 생성된 프로바이더인가 — 셋입니다.**

1. **핀을 박을 수 있습니다.** MM 저장소는 태그가 **0개**이고 하루 3.6건씩 바뀝니다.
   프로바이더는 주간 릴리스 태그가 417개 있습니다.
2. **선언과 현실이 다릅니다.** MM이 적어 놓은 교차 필드 조건 중 상당수가 생성 과정에서
   **빈 목록으로 증발합니다**(중첩 객체의 형제 이름이 루트 기준 해석에 실패하는데 조용히
   버려집니다). **출력을 읽으면 실제로 강제되는 것만 담기고**, YAML을 읽으면 현실보다
   엄격한 KB가 됩니다.
3. **YAML에 없는 축이 출력에는 있습니다.** `customdiff.ForceNewIfChange`가 그것으로
   `compute_disk.size`·`subnetwork.ip_cidr_range`가 걸립니다. 뜻은 **"늘리는 건 되고
   줄이면 재생성"** — 불변/가변 이분법으로는 안 담기는 축이고 MM YAML에는 흔적조차
   없습니다.

**정체성은 KCC를 따릅니다.** 여기서 뽑은 것은 KCC가 아는 속성에만 붙이고, 못 붙인 것은
버리지 않고 셉니다. 그래서 §15의 낡음(2023-09 vendoring)을 **덮어쓰지 않고 보강**합니다.

**산출:** `gcp-capacity` 안의 `tpg-schema` **3,383건**(같은 파일에서 `kcc-*` 3,540건과
나란히).

### `tp-alicloud`·`tp-tencent`·`tp-oracle`·`tp-ibm`·`tp-nhn`·`tp-ncp`·`tp-openstack` (§18–24)

**한 파서가 일곱을 처리합니다**(`capacitykb/parsers/tpcsp.py`). **타입 축 자체를 여는**
작업입니다 — 실측으로 graphkb 노드가 alibaba 0개, tencent 0개였습니다.

| 키 | 태그 | 타입 | 제약 |
|---|---|---:|---:|
| `tp-alicloud` | `v1.285.0` | 1,134 | 9,167 |
| `tp-tencent` | `v1.83.13` | 1,320 | 11,222 |
| `tp-oracle` | `v8.23.0` | 986 | 14,614 |
| `tp-ibm` | `v2.4.0` | 558 | 1,987 |
| `tp-nhn` | `v1.0.9` | 110 | 779 |
| `tp-ncp` | `v4.0.6` | 33 | 427 |
| `tp-openstack` | `v3.4.0` | 108 | 725 |

**보는 주소** — 전부 `codeload`로 tar.gz를 받지만, 아래는 브라우저로 열리는 주소이고
**파서가 실제로 읽는 파일**까지 내려갑니다.

| 소스 | 등록표 (타입 목록) | 리소스 스키마 (예) |
|---|---|---|
| `tp-alicloud` | [alicloud/provider.go](https://github.com/aliyun/terraform-provider-alicloud/blob/v1.285.0/alicloud/provider.go) | [resource_alicloud_instance.go](https://github.com/aliyun/terraform-provider-alicloud/blob/v1.285.0/alicloud/resource_alicloud_instance.go) |
| `tp-tencent` | [저장소](https://github.com/tencentcloudstack/terraform-provider-tencentcloud/tree/v1.83.13) | 〃 구조 |
| `tp-oracle` | [internal/provider/register_resource.go](https://github.com/oracle/terraform-provider-oci/blob/v8.23.0/internal/provider/register_resource.go) — **혼자 함수 호출 형태** | 〃 |
| `tp-ibm` | [저장소](https://github.com/IBM-Cloud/terraform-provider-ibm/tree/v2.4.0) | 〃 |
| `tp-nhn` | [저장소](https://github.com/nhn-cloud/terraform-provider-nhncloud/tree/v1.0.9) | 〃 |
| `tp-ncp` | [저장소](https://github.com/NaverCloudPlatform/terraform-provider-ncloud/tree/v4.0.6) | 〃 |
| `tp-openstack` | [저장소](https://github.com/terraform-provider-openstack/terraform-provider-openstack/tree/v3.4.0) | 〃 |

> **§22와 §24를 나란히 열어 보면 겹침이 교차 검증이 아니라는 것이 눈으로 보입니다** —
> nhn의 구현 파일 111개 중 90개가 `resource_openstack_*.go` 이름 그대로입니다.

**등록표가 두 모양입니다** — 여섯은 맵 리터럴(`ResourcesMap: map[string]*schema.Resource{…}`),
**oracle 혼자** 함수 호출(`RegisterResource("oci_x", …)` 996건)이라 파서가 다른 정규식을
씁니다.

**다섯 축을 읽습니다.**

```
ForceNew: true        → mutability = create_only     Required: true → required
MaxItems / MinItems   → max_items / min_items        StringInSlice([…]) → enum
IntBetween(a, b)      → min / max
```

**타입 이름은 Terraform 것을 그대로 씁니다.** `alibaba::alicloud_instance`처럼 id에
`alicloud_`가 그대로 보이는 것이 **의도**입니다 — 이게 Terraform의 이름이라는 사실이
id에 드러나야 **나중에 공식 스키마가 생겼을 때 무엇을 바꿔야 하는지** 압니다.

**대조할 짝이 없다는 사실을 산출물에 적습니다.** aws는 CFN, gcp는 KCC라는 독립 소스가
있어 어긋남을 셀 수 있었지만 **이 일곱은 단일 소스**입니다.

**소스별 정직한 기록 셋.**

- **`tp-nhn`** — *"NHN은 공개 프로바이더가 없다"*고 적어 뒀던 것이 **틀렸습니다**(받아
  보니 110종). 또한 **OpenStack 프로바이더를 리브랜딩한 것**입니다 — 구현 파일 111개 중
  90개가 `resource_openstack_*.go` 그대로입니다. 그래서 §24와 이름이 **84/110 겹치는
  것은 교차 검증이 아니라 같은 코드**입니다. **독립된 두 소스로 세면 안 됩니다.**
- **`tp-ncp`** — 절반이 Plugin Framework로 이전해 우리 파서가 못 읽습니다. 타입이
  33종뿐인 이유가 여기 적혀 있습니다 — **구현 범위이지 근거 부재가 아닙니다.**
- **`tp-oracle`** — 태그 API가 개발용 버전을 먼저 주므로 releases를 봐야 진짜 최신을
  압니다.

## 3.6 소스 그룹 C — 구독 한도와 최소 규모 (4종)

### `azure-limits-doc` (§35) — 문서의 표만

**왜 Azure만인가 — 이 항목 자체가 조사 결과입니다.** **세 클라우드 중 Azure만
자격증명 없이 기계 판독이 가능합니다.**

```
AWS Service Quotas API   자격증명 필요 (대안 awslimitchecker는 AGPL + 2021년 이후 정체)
GCP Cloud Quotas         문서 저장소 자체가 비공개(HTML만)
Azure                    문서 저장소가 공개 md + 표
```

**실측 함정을 전부 처리합니다** — 천단위 콤마(`1,000`), 각주(`<sup>1</sup>`), 셀 안의
마크다운 링크, 비수치 값(`Contact support`, `/28`, `256 * N (N is number of NICs on VM)`).

**라벨이 갈리면 라벨을 쪼갭니다.** 표의 숫자는 `azure-limits-doc`(**stated**),
각주·`varies` 같은 비수치 표현은 `azure-limits-note`(**inferred**)로 나눕니다 — *한
evidence 라벨은 성격이 하나여야 한다*는 규칙입니다.

**산출:** `data/azure-quota.json.gz` — `quotas` **542건**(stated 321 + inferred 221).

### `tumblebug-src` (§2) · `bitnami-charts` (§47) · `reviewed-sizing` — 최소 규모

**`tumblebug-src`의 `networkinfo.yaml`** — CSP별 서브넷 예약 IP. **비어 있는 칸으로
규칙을 만들지 않습니다.** CSP 10곳 중 3곳(alibaba·azure·ibm)만 예약 IP를 적어 뒀는데,
**AWS 칸이 비어 있지만 AWS는 실제로 5개를 예약합니다.** 빈칸을 0으로 읽으면 251대 자리에
**256대라고 답하게 됩니다.**

**`bitnami-charts`** — 컨테이너 규모 프리셋 28건(nano~2xlarge → CPU/메모리).
**원본이 스스로 *"These presets are for basic testing and not meant to be used in
production"*이라고 적어 두었고, 그 문장을 값과 함께 담습니다** — 떼면 **테스트용 숫자가
권장값으로 둔갑**합니다. 컨테이너 규모이지 인스턴스 규모가 아닙니다.

**`reviewed-sizing`(소스가 아닌 산출물)** — 위의 빈 7곳을 사람이 손으로 채운 것입니다.
현재 **6건**(aws·gcp·ncp·nhn·oracle·tencent의 예약 IP).

**기계 판독 소스를 찾았고 없었습니다** — 그 조사 결과를 레코드에 적습니다.

```
awsdocs/amazon-vpc-user-guide       2023-06-15 아카이브 · 파일 7개로 비워짐
hashicorp/terraform-provider-aws    subnet/vpc 문서에 '예약' 언급 0건
GoogleCloudPlatform/compute-docs    404
tumblebug networkinfo.yaml          해당 칸이 빈칸
```

**값이 의심스러워서가 아니라 출처가 기계 판독이 아니라서** 따로 담습니다. 산출물이 갈려
있으면 **나중에 진짜 소스가 생겼을 때 이 파일만 지우면 됩니다.**

## 3.7 이 축이 지금 답할 수 있는 것 (2026-07-29 실측)

| 산출물 | 건수 | 소스 |
|---|---:|---|
| `aws-capacity` | 47,070 | §8 cfn-schema |
| `azure-capacity` | 42,831 | §13 bicep-types-az |
| `oracle-capacity` | 14,614 | §20 tp-oracle |
| `tencent-capacity` | 11,222 | §19 tp-tencent |
| `alibaba-capacity` | 9,167 | §18 tp-alicloud |
| `gcp-capacity` | 6,923 | §15 kcc-crd + §17 tpg-provider |
| `aws-tf` | 2,756 | §16 tpaws-provider |
| `ibm-capacity` | 1,987 | §21 tp-ibm |
| `azure-mutability` | 1,275 | §14 azure-rest-api-specs |
| `aws-conditional` | 966 | §12 cfn-lint |
| `nhn-capacity` | 779 | §22 tp-nhn |
| `openstack-capacity` | 725 | §24 tp-openstack |
| `azure-quota` | **542** (quotas) | §35 azure-limits-doc |
| `ncp-capacity` | 427 | §23 tp-ncp |
| `aws-regions` | 385 | §12 cfn-lint |
| `azure-secret` | 230 | §14 azure-rest-api-specs |
| `aws-limits` | **20** (교차 검증) | §10 botocore × §25 aws-price-list |
| **제약 합계** | **141,377** | |
| **+ 구독 한도** | **542** | |
| **총계** | **141,919** | |

**오라클이 가장 오해하기 쉽습니다** — 규격이 14,614건(3위)이나 있는데 **가격도 성능도
관계도 0**입니다. 축마다 커버리지가 다르다는 것을 이 프로바이더 하나가 잘 보여 줍니다.

## 3.8 이 축의 타당성 위협

- **최대 근거가 재현 불가능**합니다(§8 cfn-schema 46,911건, 지문 핀). 바뀐 사실은 알 수
  있지만 **옛 상태로 되돌릴 수는 없습니다.**
- **일곱 CSP는 단일 소스**입니다(§18–24). 교차 검증할 짝이 없고, 그중 nhn↔openstack의
  84종 겹침은 **같은 코드라 교차 검증이 아닙니다.**
- **Terraform 판단과 API 판단이 섞일 위험.** `ForceNew`는 "Terraform이 재생성한다"입니다.
  표시 문구가 규율로 고정돼 있지만, **답을 읽는 사람이 그 차이를 놓칠 수 있습니다.**
- **GCP에 수치 한도가 없는 것은 원본의 성질**입니다(3.4). 커버리지 숫자만 보면 GCP가
  약해 보이지만 **성격이 다른 것**입니다.
- **구독 한도는 Azure에만** 있습니다 — 근거 부재가 아니라 **접근 제약**입니다.
- **손 큐레이션 지점 셋**: `azure_quota_types.json`(타입 연결) · `reviewed-sizing`(6건) ·
  §29의 구세대 정규식 표. 앞의 둘은 단일 코더이고, 셋째만 **문서 라벨과 상호 대조**가
  걸려 있습니다.

---

# 4부 — 클라우드 리소스 의존성(연결 관계)

> 과제 원문: *"클라우드 리소스 간 의존 관계성이 존재하며, 이에 따른 리소스 선택의
> 제약사항이 존재한다"*

## 4.0 정의 — 이 축은 무엇인가

> **클라우드 리소스 의존성**이란, 리소스 **타입 둘 사이의 방향 있는 관계**를 말한다.
> 방향은 **의존하는 쪽 → 의존 대상**이다(`Subnet → VPC`). 관계에는 **종류가 있고**,
> 종류를 구분하지 않으면 *"A가 B를 필요로 한다"*가 **배포 순서인지 논리적 포함인지 알
> 수 없게 된다.**

### 관측 단위 — 노드와 엣지

**노드(Node)** 는 리소스 타입 하나, **엣지(Edge)** 는 타입 둘 사이의 관계 하나입니다.
엣지의 정체성은 **`(from, to, type, via_property)`** 입니다 — 같은 두 타입 사이라도
**경유 속성이 다르면 다른 관계**입니다.

### 용어 — 관계의 종류와 엣지의 칸들

**`type` — 관계의 종류 셋.**

| 값 | 정의 | 예 |
|---|---|---|
| **`references`** | **내 속성이 저쪽을 가리킨다** | `Certificate.CertificateAuthorityArn → CertificateAuthority` |
| **`contained_in`** | **내가 저쪽 안에 산다** | `Microsoft.Network/virtualNetworks/subnets → virtualNetworks` |
| **`equivalent_to`** | **다른 이름의 같은 것** | `core::vNet → aws::AWS::EC2::VPC` · `app::relationalDatabase → aws::AWS::RDS::DBInstance` |

**`layer` — 타입 이름이 사는 층 셋.**

| 값 | 정의 | 어디서 오나 |
|---|---|---|
| **`core`** | **도구 중립 어휘.** 프로바이더를 정하기 전에 말할 수 있는 부품 이름 | cb-tumblebug 스웨거(§5) — 13종 |
| **`vendor`** | 회사가 정의한 실제 타입 이름 | 각 프로바이더 스키마 |
| **`app`** | **애플리케이션 개념 층**(관계형 DB·큐·객체 스토리지) | svcmap(§36·§46) — 13종 |

> **`app`을 따로 둔 이유.** core 층은 **cb-tumblebug 스웨거의 미러**입니다. 여기에 우리
> 개념을 섞으면 **미러가 오염됩니다.** `provider`도 `"app"`이라 `core_concept`
> (provider=="common" 검색)에 걸리지 않습니다.

**엣지의 나머지 칸.**

| 칸 | 정의 |
|---|---|
| **`via_property`** | **내 어느 칸에 적나** |
| **`target_property`** | **거기에 무슨 값을 적나** — 대상이 돌려주는 값 중 어느 것을 가져다 쓰는가 |
| **`required`** | 그 속성이 **필수인가** |
| **`cardinality`** | `one` / `many` — 하나를 가리키나 여럿을 가리키나 |
| **`reviewed`** | **사람이 눈으로 보고 맞다고 확인했는가.** 소스에 핀이 박혀 입력이 얼어 있으므로 **손 검수 결과는 다음 빌드에서도 유효합니다** |

> **`via_property`와 `target_property`가 둘 다 있어야 실제로 조립할 수 있습니다.**
>
> ```
> VPCEndpoint → VPC   via_property   = VpcId
>                     target_property = DefaultSecurityGroup
> ```
>
> *"네트워크를 가리킨다"*까지는 `via_property`로 알지만, 정작 **복사해 넣을 값이
> 네트워크 번호가 아니라 그 네트워크의 기본 방화벽**이라는 건 `target_property`에만
> 있습니다. **방향도 대상도 맞고 결합 지점만 틀린 형태**라 원본 대조로도 안 잡히던
> 누락이었습니다.
>
> 소스마다 이름이 다릅니다 — AWS는 `propertyPath`, GCP servicemapping은 `targetField`,
> Azure는 ARM 관례상 **항상 `id`**입니다. **모르면 빈 문자열**이고, 그건 *"이 관계에
> 결합 지점이 없다"*가 아니라 ***"우리가 모른다"***는 뜻입니다.

### 무엇이 이 축이 **아닌가** — 경계

| 아닌 것 | 왜 / 어디로 |
|---|---|
| **인스턴스 사이의 관계** | 스펙(`t3.micro`)은 **그래프의 노드가 아닙니다**(0.2). 관계는 타입에 붙습니다 |
| 실행 시점 네트워크 의존 | 배포 그래프가 아니라 런타임 토폴로지입니다 |
| 앱 코드의 라이브러리 의존 | 클라우드 리소스가 아닙니다 |
| **함께 나오는 빈도** | **⑤ 리소스 군** — 관계가 아니라 관찰입니다 |
| 속성 하나에 걸린 한계 | **③ 용량** |
| "이 순서로 배포하라"는 권고 | 담되 **`avm-dependson`으로 라벨을 갈라** 모듈 저자의 설계임을 밝힙니다(4.7) |

### 레코드 실물

```json
{"id": "aws::AWS::ACMPCA::Certificate", "layer": "vendor", "provider": "aws",
 "kind": "resource_type", "display_name": "AWS::ACMPCA::Certificate",
 "source": "cloudformation-registry"}

{"from": "aws::AWS::ACMPCA::Certificate", "to": "aws::AWS::ACMPCA::CertificateAuthority",
 "type": "references", "via_property": "CertificateAuthorityArn", "required": true,
 "cardinality": "one", "evidence": "cdk-oob", "basis": "stated",
 "target_property": "Arn", "reviewed": true}
```

> **출처** — 둘 다 `data/aws-graph.json.gz`인데 **소스가 다릅니다.**
>
> | | 소스 | 핀 | 어디에 적히나 |
> |---|---|---|---|
> | 노드 | **§8 `cfn-schema`** | **지문**(sha256 `83b88800e04b…`) | `source: "cloudformation-registry"` |
> | 엣지 | **§9 `cdk-oob`** | 태그 `@aws-cdk/aws-service-spec@v0.1.196` | `evidence: "cdk-oob"` |
>
> **노드와 엣지가 다른 칸 이름으로 출처를 말합니다** — 노드는 `source`, 엣지는
> `evidence`+`basis`입니다. 노드는 "이 타입이 존재한다"는 목록이라 근거의 등급이 갈릴
> 일이 없지만, 엣지는 **같은 파일 안에서 등급이 셋으로 갈립니다.**
>
> ```
> aws-graph.json.gz 의 엣지 2,391건
>   cdk-oob         1,191  §9   stated     회사가 선언한 표
>   heuristic       1,113  §8   inferred   이름 규칙으로 우리가 짐작
>   relationshipRef    87  §8   stated     스키마가 직접 말한 것
> ```
>
> **같은 두 타입 사이에 엣지가 여럿일 수 있습니다** — 위 레코드 외에
> `via_property = CertificateSigningRequest`인 엣지가 따로 있습니다(실측). **경유
> 속성이 다르면 다른 관계**라는 정의(위)가 여기서 드러납니다.

### 섞인 두 소스를 각각 원본에서 보기

**① §8 `cfn-schema`** — 노드 1,638개 + 엣지 1,200건(`relationshipRef` 87 · `heuristic` 1,113)

- **보는 주소가 없습니다**(3.0 ③ 참조) — zip을 받아 풀어야 하고, 개별 타입은 AWS
  문서에서만 볼 수 있습니다.
- 원본 형태 — `AWS::ACMPCA::Certificate`의 스키마에는 **관계를 말하는 칸이 거의
  없습니다.** 그래서 이 소스만으로는 엣지의 절반이 이름 휴리스틱이 됩니다.

**② §9 `cdk-oob`** — 엣지 1,191건. **AWS CDK 팀이 손으로 정리해 배포하는 표**

- 보는 주소: <https://github.com/cdklabs/awscdk-service-spec/blob/@aws-cdk/aws-service-spec@v0.1.196/sources/OobRelationships/relationships.json>
  (태그에 `@`와 `/`가 들어가지만 **그대로 열립니다** — 확인함)
- ⚠ **받을 때는 `media.githubusercontent.com`을 써야 합니다** — Git LFS 파일이라
  `raw`는 포인터 텍스트를 줍니다. **브라우저로 보는 것과 받는 것의 주소가 다른 유일한
  소스**입니다.
- **원본 전문은 4.3에** 있습니다(이 타입의 항목 전체). 위 레코드가 그대로 거기서
  나오고, **전문에는 항목이 넷인데 우리 그래프에 남는 것은 셋**입니다.

> **두 원본을 나란히 놓으면 왜 §9를 따로 받았는지가 한눈에 보입니다** — ①에는 이
> 삼중항이 없고, ②는 `(속성, 대상 타입, 대상 속성)`을 **그대로 말합니다.** 그래서
> ②에서 온 엣지만 `basis=stated`이고, 이 소스 덕분에 AWS 관계의 49.8%가 짐작이
> 아닙니다.

**③ 다른 그래프의 소스도 같은 방식으로 볼 수 있습니다.**

| 그래프 | 소스 | 보는 주소 |
|---|---|---|
| `azure-graph` | §13 `bicep-types-az` | <https://github.com/Azure/bicep-types-az/tree/ef7421bbfef762f59292e253701a9859af32fc2c/generated> |
| `gcp-graph` | §15 `kcc-crd` (관계는 servicemappings) | <https://github.com/GoogleCloudPlatform/k8s-config-connector/tree/v1.153.0/config/servicemappings> |
| `azure-deploy-graph` | §41 `avm-bicep` | <https://github.com/Azure/bicep-registry-modules/tree/b7c2b1a25b334fe260c5347f70468e47c7dfeef4/avm/res/storage/storage-account> |
| `core-graph` | §5 `tumblebug-swagger` | <https://github.com/cloud-barista/cb-tumblebug/blob/v0.11.8/src/interface/rest/docs/swagger.json> |
| `mapping-graph` | §7 `cb-spider-map` (**우리 파일**) | `graphkb/parsers/core_vendor_map.json` — 이 저장소 안 |
| `svcmap-graph` | §36 + §46 (**두 소스**) | <https://github.com/MicrosoftDocs/architecture-center/tree/11c3681605cfeb209ddbac372a53d8931696d0cd/docs/aws-professional> · <https://github.com/mingrammer/diagrams/blob/v0.24.4/diagrams/aws/database.py> |

## 4.1 이 축이 답해야 하는 질문

```
"이걸 만들려면 무엇이 먼저 있어야 하나"       — 배포 순서
"이 리소스는 어느 리소스 안에 들어가나"        — 담김(contained_in)
"이 속성이 가리키는 다른 리소스는 무엇인가"    — 참조(references)
"이 개념은 다른 클라우드에서 무엇인가"         — 동치(equivalent_to)
```

관계의 **종류**를 구분하는 것이 이 축의 첫 설계 판단입니다. 넷을 하나로 뭉개면 "A가 B를
필요로 한다"가 배포 순서인지 논리적 포함인지 알 수 없게 됩니다.

## 4.2 질문이 엣지 조회로 번역되는 방식

4.1의 네 질문은 각각 **엣지의 어느 칸을 보느냐**로 갈립니다. 정의(4.0)가 실제 답변으로
이어지는 지점입니다.

| 질문 | 무엇을 조회하나 | 답에 함께 나가는 것 |
|---|---|---|
| "이걸 만들려면 뭐가 먼저" | `from = X` 이고 `required = true` 인 엣지 | `via_property` · `basis` |
| "무엇 안에 들어가나" | `type = contained_in` | 계층 경로 |
| "이 속성이 가리키는 것" | `via_property = <속성>` | `target_property` |
| "다른 클라우드에서는" | `type = equivalent_to` (층을 건너뜀) | **실행 가능 여부**(4.10) |

**`via_property`가 있어야 답이 검증 가능합니다** — *"왜 그렇게 생각하나"*에 *"이 속성
때문"*이라고 답할 수 있고, 읽는 쪽이 **원본 스키마에서 그 속성을 찾아 확인**할 수
있습니다. 근거를 못 대는 관계는 담지 않는다는 규율이 이 칸 하나로 강제됩니다.

## 4.3 소스 ① — `cdk-oob` (§9) · AWS가 직접 말한 관계

| | |
|---|---|
| **받는 주소** | `https://media.githubusercontent.com/media/cdklabs/awscdk-service-spec/@aws-cdk/aws-service-spec@v0.1.196/sources/OobRelationships/relationships.json` |
| **보는 주소** | <https://github.com/cdklabs/awscdk-service-spec/blob/@aws-cdk/aws-service-spec@v0.1.196/sources/OobRelationships/relationships.json> — 태그에 `@`와 `/`가 들어가지만 **그대로 열립니다** |
| 핀 · 라이선스 | 태그 `@aws-cdk/aws-service-spec@v0.1.196` · 라이선스 미확인 |

> ⚠ **받는 주소와 보는 주소의 호스트가 다른 유일한 소스입니다.** Git LFS 파일이라
> `raw.githubusercontent.com`은 **포인터 텍스트**를 주고 `media.githubusercontent.com`이
> 실물을 줍니다.

**왜 필요했나.** CloudFormation 스키마의 `relationshipRef`는 **87건뿐**입니다. 나머지를
이름 휴리스틱으로 채우면 AWS 관계 답변이 전부 짐작이 됩니다.

**무엇인가.** **AWS CDK 팀이 손으로 정리해 배포하는 표**입니다. 타입 353개, 관계 1,191건.

### 원본 전문 — 타입 하나 (`AWS::ACMPCA::Certificate`)

**이 타입의 항목 전체입니다.** 줄이지 않았습니다.

```json
{
  "AWS::ACMPCA::Certificate": {
    "relationships": {
      "CertificateAuthorityArn": [
        {
          "cloudformationType": "AWS::ACMPCA::CertificateAuthority",
          "propertyPath": "/properties/Arn"
        }
      ],
      "CertificateSigningRequest": [
        {
          "cloudformationType": "AWS::ACMPCA::CertificateAuthority",
          "propertyPath": "/properties/CertificateSigningRequest"
        }
      ],
      "TemplateArn": [
        {
          "cloudformationType": "AWS::ACMPCA::CertificateAuthority",
          "propertyPath": "/properties/Arn"
        }
      ],
      "Arn": [
        {
          "cloudformationType": "AWS::ACMPCA::CertificateAuthority",
          "propertyPath": "/properties/Arn"
        }
      ]
    }
  }
}
```

**전문에만 나오는 것이 이 소스를 이해하는 열쇠입니다.**

- **항목이 넷인데 우리 그래프에 남는 것은 셋**입니다. 마지막 `"Arn"`이 걸러집니다 —
  `Arn`은 `AWS::ACMPCA::Certificate`의 **`readOnly` 속성**, 즉 **생성 결과**입니다.
  그대로 옮기면 *"인증서의 Arn이 인증기관을 가리킨다"*가 되어 **방향이 뒤집힌
  엣지**가 생깁니다. 발췌만 보면 이 항목이 있는지조차 알 수 없습니다.
- **같은 대상을 가리키는 항목이 셋**입니다(`CertificateAuthorityArn` ·
  `TemplateArn` · `Arn` 전부 `/properties/Arn`). **경유 속성이 다르면 다른 관계**라는
  규약(4.0)이 실제로 필요한 이유가 여기 있습니다.
- **값이 배열**입니다. 한 속성이 여러 타입을 가리킬 수 있다는 뜻이고, 실제로 그런
  타입이 있습니다.

**어떻게 쓰나.** `(속성, 대상 타입, 대상 속성)` 삼중항을 그대로 엣지로 옮깁니다 —
**짐작이 없으므로 `basis=stated`**입니다. `readOnly` 속성은 생성 출력이라 생성 순서와
무관하므로 **모든 소스에서 제외**합니다.

**효과.** AWS 엣지 2,391건 중 **1,191건(49.8%)이 `stated`**이고 `heuristic`은 46.5%입니다.

## 4.4 소스 ② — `cfn-schema` (§8) · **담김을 지어내지 않은 기록**

> **담김(`contained_in`) 관계는 AWS에서 안 나옵니다 — 지어내지 않기로 한 결과입니다.**
> CloudFormation 스키마에는 담김을 말하는 어휘가 **아예 없습니다**(전수 확인). *"필수
> 참조를 담김으로 치면 되지 않나"* 싶지만 실측상 aws 엣지 2,391건 중 **744건이
> 필수**인데, `Certificate → CertificateAuthority`처럼 담김이 맞는 것과
> `Instance → Subnet`처럼 애매한 것이 섞여 있습니다. **비워 두고 그 사실을 코드에
> 적습니다.**

세 근거를 병합하되 **같은 엣지는 사실인 쪽을 유지**합니다:
`relationshipRef`(87) → `cdk-oob`(1,191) → `heuristic`(1,113).

## 4.5 소스 ③ — `bicep-types-az` (§13) · Azure · **이름이 곧 계층**

**핵심 통찰.** ARM 타입명이 계층을 그대로 말합니다 —
`Microsoft.Network/virtualNetworks/subnets`는 `virtualNetworks`의 자식입니다. **이름만으로
`contained_in` 엣지가 나옵니다.**

**왜 `stated`인가.** 규칙 위반을 전수로 셌고 **0/2,223**이었습니다. 이름 규약이 아니라
**검증된 구조**라는 뜻입니다.

**참조 엣지는 다릅니다.** swagger의 arm-id 참조 메타데이터가 bicep 생성 과정에서
소실되므로, **ObjectType 이름을 정규화**(Common 접두사 제거, 단수/복수 보정)해 리소스
타입과 **유일 매칭**되면 참조로 봅니다(`bicep-ref`, 248건, **inferred**).

**용량 때문에 범위를 좁혔습니다.** 노드·계층은 `index.json` 한 파일로 커버하고, **참조
엣지는 선택된 프로바이더**(기본 network/compute/containerservice)의 상세만 받습니다 —
**구현 범위이지 근거 부재가 아닙니다.**

## 4.6 소스 ④ — `kcc-crd` (§15) · GCP · **3단계 해석**

**문제.** CRD의 `networkRef`가 어느 kind를 가리키는지 **CRD 안에는 구조화 메타데이터가
없습니다.**

**답이 같은 저장소의 다른 디렉터리에 있었습니다** — `config/servicemappings/`:

```yaml
resourceReferences:
- key: bucketRef
  gvk: {kind: StorageBucket, version: v1beta1, group: storage.cnrm.cloud.google.com}
```

**3단계로 해석하고 단계마다 evidence가 갈립니다.**

```
① servicemappings의 (kind, key) → gvk.kind      evidence=kcc-ref          stated
② description 정규식                             evidence=kcc-description  inferred
   "Allowed value: The `selfLink` field of a `ComputeNetwork` resource."
③ 필드명 휴리스틱 (networkRef → *Network 유일 매칭)  evidence=heuristic       inferred
```

DCL 기반 CRD는 description이 generic이라 **①번 없이는 대상을 알 수 없습니다.**
`projectRef`/`folderRef`/`organizationRef`/`billingAccountRef`는 GCP 자원 계층이라 별도
라벨(`kcc-hierarchy`, **stated** — 설명문이 "belongs to"라고 말하고 projectRef 273/296).

## 4.7 소스 ⑤ — `avm-bicep` (§41) · **실무 배포 순서** · 성격이 다릅니다

`…/Azure/bicep-registry-modules/tar.gz/b7c2b1a2…` · 커밋 핀(저장소 전체 태그가 없음) · MIT

**왜 필요했나.** 우리 Azure 그래프는 이름 계층(2,223)과 스키마 참조(248)로 되어 있는데
**둘 다 "구조가 그렇다"는 사실**입니다. AVM은 **"실제로 배포할 때 무엇을 먼저
만드는가"**를 줍니다.

**실측이 그 차이를 보여 줬습니다** — storage-account 모듈 하나에서 타입 쌍 6개 중
**5개가 우리 그래프에 없던 새 관계**였습니다(겹침 0).

```
Microsoft.Insights/diagnosticSettings   → Microsoft.Storage/storageAccounts
Microsoft.Authorization/roleAssignments → Microsoft.Storage/storageAccounts
Microsoft.Storage/storageAccounts       → Microsoft.KeyVault/vaults
```

마지막 것이 이 소스의 성격입니다 — 스토리지 계정이 KeyVault를 **스키마상 요구하지는
않습니다.** 고객 관리 키를 쓸 때만 필요하고, AVM은 그 **실무 구성**을 담고 있습니다.

**걸러내는 것 — 가짜 허브.** `Microsoft.Resources/deployments`(AVM이 사용량 집계용으로
넣는 텔레메트리 배포)는 **모든 모듈에 있어서** 담으면 **모든 타입이 여기 의존하는 가짜
허브**가 생깁니다.

> **경계를 명시합니다.** `avm-dependson`은 **"AVM 모듈이 이 순서로 배포한다"**이지
> **"API가 이 순서를 강제한다"**가 아닙니다 — `ForceNew`와 같은 구분입니다. 다만 검증되는
> 두 사례(virtual-machine → networkInterfaces, virtual-network-gateway →
> publicIPAddresses)는 클라우드 사실과도 일치합니다. **일치를 확인한 것과 일치한다고
> 가정하는 것은 다릅니다.**

## 4.8 소스 ⑥ — `tumblebug-swagger` (§5) + `cb-spider-map` (§7) · 벤더 중립 층

**왜 필요했나.** 프로바이더별 그래프만 있으면 *"VM을 만들려면 무엇이 필요한가"*를
프로바이더를 정하기 **전에는** 물을 수 없습니다.

**`tumblebug-swagger`** — Swagger 2.0 `definitions` 262개 중 **생성 요청 스키마(`model.Tb*Req`)만**
씁니다. `required` 배열이 생성 시점 제약을 표현하고, 응답 스키마는 **서버 생성 필드
노이즈**가 많기 때문입니다. `$ref`로 딸린 배열 프로퍼티를 **담김 관계**로 읽습니다.

**산출:** `core-graph` — 노드 13 · 엣지 19. **이 13개가 벤더 중립 어휘**입니다.

**`cb-spider-map`** — 그 13개에 벤더 타입을 잇습니다. 네트워크 소스가 아니라 **cb-spider
드라이버를 사람이 읽고 검수해 만든 파일**입니다.

```json
{"core": "vNet", "provider": "aws", "target": "AWS::EC2::VPC", "confidence": 0.95,
 "note": "aws VPCHandler.go: ec2.CreateVpc (IGW/RouteTable 번들 생성 포함)",
 "status": "confirmed"}
```

**`status: "confirmed"`인 것만** 그래프에 넣습니다. `suggest()`가 이름 유사도로 후보
(`candidate`)를 만들어 **사람 검수용 파일**을 뽑고, 검수 후 `confirmed`로 바꿔 넘기면
반영되는 **반자동 파이프라인**입니다. **손으로 고치는 파일이라 오히려 해시 추적이
필요합니다.**

**`basis`가 `inferred`인 것이 핵심** — 드라이버 코드를 **사람이 읽고 만든** 매핑이고,
검수됐으니 판정에는 쓰되(`is_fact`) 단언하지는 않습니다(`needs_hedge`).

**등가물이 없는 조합은 항목을 만들지 않습니다**(`sshKey/gcp`, `customImage/aws·gcp` 등).
`ibm`·`ncp`·`openstack`·`oracle`은 **근거가 한 단계 얕다**는 사실이 파일의 `description`에
적혀 있습니다(confidence 0.9).

## 4.9 소스 ⑦ — `ms-architecture-center` (§36) + `mingrammer-diagrams` (§46) · 동치 관계

**왜 필요했나.** core 층 13개는 전부 인프라(vm·vNet·subnet…)라 **앱 설계도가 말하는
것들 — DB·큐·캐시·객체 스토리지 — 에 대응이 없었습니다.**

**둘이 한 산출물을 만듭니다.** 어떤 독립 근거가 뒤에 있는가로 라벨이 갈립니다.

```
MS 표 + diagrams 둘 다  →  svcmap-cross-checked   31건
diagrams만              →  mingrammer-taxonomy    17건
손 검수만               →  svcmap-reviewed        16건
MS 표만                 →  ms-learn-comparison     5건
```

**왜 `stated`가 아닌가.** 표가 말하는 건 **서비스 이름 수준의 대응**('Amazon RDS ↔ Azure
SQL')이고, 그걸 구체적 타입 id(`AWS::RDS::DBInstance`)에 붙이는 것은 **우리 손
검수**입니다. **다리를 건너면 등급이 떨어진다**는 규칙 그대로 `inferred(검수됨)`입니다.

**겪은 문제 — 문서라 표가 재편됩니다.** `aws-professional/services.md` 하나였던 것이
카테고리별 파일 6개로 쪼개져 **404를 직접 겪었습니다.** 지금은 행 수가 급감하면 알립니다.

**담지 않은 것도 적어 둡니다**: tencent(MS 표에도 diagrams에도 없음) · GCP CDN(Cloud
CDN은 백엔드 서비스에 붙는 **플래그**라 1:1 타입이 없음 — 억지로 `ComputeBackendBucket`을
대면 *"CDN을 만들었다"*로 읽힘).

> **실행 경계.** 이 대응은 **안내이지 배포 가능이 아닙니다.** cb-tumblebug 실행 경로는
> VM·k8s까지라 관리형 서비스를 만들지 못하고, `agent_api.equivalent_types`가 app:: 층을
> 지나면 **그 사실을 답에 붙입니다.**

## 4.10 소스 ⑧ — `cb-spider` (§6) · **우리가 실제로 만들 수 있는 것**

**왜 이 축인가.** 다른 재료가 *"클라우드가 허용한다"*고 해도 **여기 없으면 우리 도구로는
못 만듭니다.** 그 차이를 답할 수 있게 하는 유일한 근거입니다.

**경로 자체가 데이터입니다** —
`cloud-driver/drivers/<csp>/resources/<X>Handler.go`.

**함정 둘.**

1. **"파일이 있다"와 "구현됐다"는 다릅니다.** cb-spider는 미지원 기능을 "not supported"
   에러를 던지는 스텁으로 두기도 합니다. **주 생성 메서드가 실제로 있는지**까지 봅니다.
2. **메서드 이름이 핸들러마다 다릅니다.** 인터페이스를 직접 읽고 확인했습니다 —
   `VMHandler → StartVM`(CreateVM이 아님), `MyImageHandler → SnapshotVM`(CreateImage가
   아님). **`Create`로만 찾으면 이 둘이 전부 미구현으로 잡힙니다**(처음에 그 상태를
   만들었습니다).

**매트릭스가 곧 지식입니다.** VPC·Security·KeyPair·VM·Disk·MyImage **12/12** ·
NLB **11/12**(oracle 없음) · Cluster(k8s) **8/12**(kt·ktclassic·openstack·oracle 없음).

`_note`가 경계를 못 박습니다: *"This is the tool's coverage, not a fact about the cloud —
unsupported here does not mean 'the CSP lacks that feature'."*

## 4.11 이 축이 지금 답할 수 있는 것 (2026-07-29 실측)

| 그래프 | 노드 | 엣지 | evidence 분포 |
|---|---:|---:|---|
| `azure-graph` | 3,382 | **2,514** | arm-hierarchy 2,223 · bicep-ref 248 · heuristic 42 · human-review 1 |
| `aws-graph` | 1,638 | **2,391** | cdk-oob 1,191 · heuristic 1,113 · relationshipRef 87 |
| `gcp-graph` | 527 | **1,052** | kcc-hierarchy 375 · kcc-ref 292 · kcc-description 255 · heuristic 112 · human-review 18 |
| `azure-deploy-graph` | 175 | **421** | avm-dependson 421 |
| `mapping-graph` | 92 | **82** | cb-spider-driver 82 |
| `svcmap-graph` | 81 | **69** | cross-checked 31 · mingrammer 17 · reviewed 16 · ms-learn 5 |
| `core-graph` | 13 | **19** | swagger-field 19 |
| alibaba·tencent·oracle·ibm·nhn·ncp·openstack | 4,249 | **0** | — |
| **합계** | | **6,548** | |

> **"연결 관계"는 사실상 3개 클라우드뿐입니다.** 일곱 CSP는 **노드만 있고 관계가
> 0건**입니다. 스키마에 참조 메타데이터가 없어서이지 우리가 빠뜨린 것이 아닙니다.
> 즉 *"이걸 만들려면 뭐가 먼저 필요한가?"*는 **AWS·Azure·GCP에서만** 답할 수 있습니다.
> 결함이 아니라 **아직 안 만든 축**이고, 이렇게 적어 두지 않으면 "12개 클라우드 그래프"가
> 12곳 전부에서 관계를 답한다는 뜻으로 읽힙니다.

## 4.12 이 축의 타당성 위협

- **근거 등급이 프로바이더마다 다릅니다.** Azure 엣지는 **88.4%가 이름 계층**이고
  AWS는 **49.8%가 회사가 선언한 표**입니다. 같은 기능인데 소스가 달라 신뢰도가 다르고,
  **답에 그 비율이 적혀 나갑니다.**
- **Azure 관계를 단정으로 쓰면 안 됩니다.** 이름 계층은 규칙 위반 0/2,223으로 검증됐지만
  **원본이 선언한 적은 없습니다.** 삭제 계획 같은 걸 세울 땐 실제 참조를 확인해야 합니다.
- **AWS에 담김 관계가 없는 것은 의도**입니다(4.4). 커버리지 표만 보면 결함처럼 보입니다.
- **`heuristic` 비중.** AWS 46.5% · GCP 10.6% · Azure 1.7%. 이름 휴리스틱은 **오탐과
  누락이 둘 다** 가능하고, 검수 표(`human-review` aws 0 · gcp 18 · azure 1)가 아직 얇습니다.
- **AVM은 모듈 저자의 설계**이지 API의 강제가 아닙니다(4.7). 두 사례만 클라우드 사실과
  대조됐습니다.
- **동치 매핑은 단일 코더 손 검수**입니다(§36·§46·§7). 교차 확인된 것은 31건뿐입니다.

---

# 5부 — 특정 클라우드 리소스와 연계되는 리소스 군

> 과제 원문: *"클라우드 리소스를 단순히 선택하는 것이 아닌, 특정 리소스를 선택하는 경우
> 연계되는 다양한 리소스 군을 획득할 수 있어야 하며"*

## 5.0 정의 — 이 축은 무엇인가

> **연계 리소스 군**이란, 한 리소스(**앵커**)를 중심으로 **함께 만들어지거나(번들)
> 함께 나타나는(동시 출현)** 리소스들의 묶음을 말한다. 이 축의 값은 **"클라우드가 그걸
> 강제한다"가 아니라 "이 사례 뭉치에서 그렇게 나왔다"**이다.

정의의 뒷부분이 이 축 전체를 규정합니다. **④가 "구조가 그렇다"는 사실이라면 ⑤는 "실제로
그렇게 하더라"는 관찰**입니다. 이 경계를 흐리면 **표본 편향이 곧 사실이 됩니다.**

### 관측 단위 — 두 종류의 레코드

이 축은 **강도가 다른 두 신호**를 담고, 모양도 다릅니다.

| | 관측 단위 | 무엇을 말하나 |
|---|---|---|
| **번들**(Bundle) | 앵커 하나 + **멤버 집합** | *"이걸 만들면 **이것들이 생긴다**"* — 출처가 특정 도구·모듈·샘플 |
| **동시 출현**(Cooccurrence) | (앵커, 타입) **쌍** + hits/samples | *"이걸 쓴 템플릿 N개 중 M개에 **이것도 있었다**"* — 코퍼스 통계 |

### 용어

**`tier` — 멤버가 앵커와 얼마나 단단히 묶여 있는가.**

| 값 | 정의 |
|---|---|
| **`always`** | **반드시 생긴다** — 사용자가 고를 여지가 없다 |
| **`required`** | **값을 반드시 줘야 한다** — 안 주면 배포가 실패한다 |
| **`optional`** | 붙일 수 있다 — 파라미터에 따라 갈린다 |

`always`와 `required`의 차이가 §41에서 **판별자를 두 번 틀리게 만든 자리**입니다(5.7).

**그 밖의 칸.**

| 칸 | 정의 |
|---|---|
| **`anchor`** | 이 번들이 **무엇을 중심으로 도는가.** **대등한 여럿이면 `null`** — 중심을 억지로 고르지 않습니다 |
| **`caveat`** | **원본이 스스로 단 경고.** **값과 떼어 놓으면 안 됩니다** — 떼면 테스트용 구성이 권장 구성이 됩니다 |
| **`count`** | 이 번들이 그 타입을 **몇 개** 만드는가. **`1`이면 적지 않습니다** — 대부분이 1이라 다 적으면 **1이 아닌 곳이 안 보입니다** |
| **`hits` / `samples`** | 앵커를 담은 템플릿 `samples`개 중 `hits`개에 그 타입이 함께 있었다 |

> **비율이 아니라 두 수를 담습니다.** 비율만 담으면 **숫자에 없는 확신**을 줍니다 —
> **6/17을 35.3%로 읽게 됩니다.** `samples`가 함께 있어야 그 35.3%가 6건이라는 것을
> 읽는 쪽이 압니다.

### 무엇이 이 축이 **아닌가** — 경계

| 아닌 것 | 왜 / 어디로 |
|---|---|
| **"이게 필수다"라는 판정** | **판정에 쓰지 않습니다** — 100%가 *"없으면 안 된다"*를 증명하지 못합니다 |
| 배포 **순서** | **④ 의존성** — `dependsOn`은 순서이지 군이 아닙니다 |
| 권장 아키텍처·설계 지침 | **6.1 자문 축** — 산문이지 사실이 아닙니다 |
| 번들의 총 비용 | **② 비용** — 그리고 합계는 어디서도 내지 않습니다 |
| 표본 20 미만 앵커 (`MIN_SAMPLES`) | **담지 않습니다**(사전 고정 문턱) — Azure 487개 앵커가 여기서 빠집니다 |
| 3회 미만 동시 출현 (`MIN_HITS`) | **담지 않습니다** — 한두 번 같이 나온 것은 신호가 아닙니다 |

### 레코드 실물 — 둘의 성격 차이

```json
번들   {"id": "tumblebug::dynamic-vm", "name": "tumblebug dynamic VM creation",
        "provider": "core", "evidence": "tumblebug-dynamic", "anchor": "core::vm",
        "caveat": "**This is what this tool creates, not what the cloud requires.** …",
        "members": [{"typeId": "core::securityGroup", "tier": "always",
                     "note": "shared per connection. A template can change the policy"}, …]}

동시   {"anchor": "azure::Microsoft.Authorization/roleAssignments",
 출현   "typeId": "azure::Microsoft.Storage/storageAccounts",
        "hits": 50, "samples": 89, "evidence": "aqt-corpus"}
```

> **출처** — 둘이 다른 파일이고 다른 소스입니다.
>
> | 레코드 | 산출물 | 소스 | 핀 | 원본의 어느 부분 |
> |---|---|---|---|---|
> | 번들 | `data/tumblebug-bundles.json.gz` | **§2 `tumblebug-src`** | 태그 `v0.12.25` | `src/core/infra/provisioning.go` (**사람이 읽음**) |
> | 동시 출현 | `data/aqt-cooccurrence.json.gz` | **§37 `azure-quickstart-templates`** | 커밋 `331d6f394416…` | 템플릿 1,152개의 `resources[].type` |
>
> **`evidence`가 같은 소스 안에서도 갈립니다.** `tumblebug-bundles` 23건은 한 소스에서
> 나오지만 `tumblebug-dynamic`(Go 소스를 읽어 확정, 1건)과 `tumblebug-template`
> (JSON 템플릿을 기계로 읽음, 22건)으로 나뉩니다 — **추출 방법이 다르면 라벨이
> 다릅니다.** 사람이 읽은 쪽은 **언제 읽었는지가 `_READ_AT_PIN`에** 적혀 있습니다.
>
### 원본에서 직접 보기 — 이 축은 **원본이 곧 사람이 읽는 파일**입니다

다른 축과 달리 이 축의 원본은 **템플릿·샘플·모듈**이라 브라우저에서 그대로 읽힙니다.
*"이 동시 출현 수치가 어디서 나왔나"*를 **템플릿을 직접 열어** 확인할 수 있습니다.

| 산출물 | 소스 | 보는 주소 | 무엇을 보게 되나 |
|---|---|---|---|
| `tumblebug-bundles` (23) | §2 | <https://github.com/cloud-barista/cb-tumblebug/tree/v0.12.25/init/templates> | 템플릿 22개 JSON |
| 〃 (동적 1건) | 〃 | <https://github.com/cloud-barista/cb-tumblebug/blob/v0.12.25/src/core/infra/provisioning.go> | **3216~3529행을 사람이 읽었습니다** |
| `aqt-cooccurrence` (1,253) | §37 | <https://github.com/Azure/azure-quickstart-templates/tree/331d6f394416122008f71342d20c8a2ba8d9b24a/quickstarts> | ARM 템플릿 1,152개 |
| `awscfn-cooccurrence` (1,147) | **§38** | <https://github.com/aws-cloudformation/aws-cloudformation-templates/tree/a0f43bc6d20813052892546f445037cf84c75b54> | AWS 공식 샘플 299개 |
| 〃 | **§39** | <https://github.com/widdix/aws-cf-templates/tree/1a9f04f934179975a3a56c2496d2ed2b27598bd8> | 운영용 스택 63개 |
| `avm-bundles` (207) | §41 | <https://github.com/Azure/bicep-registry-modules/tree/b7c2b1a25b334fe260c5347f70468e47c7dfeef4/avm/res/storage/storage-account> | 컴파일된 `main.json` |
| `kcc-bundles` (296) | §15 | <https://github.com/GoogleCloudPlatform/k8s-config-connector/tree/v1.153.0/config/samples/resources> | 시나리오 443개 |
| `aws-pattern-bundles` (52) | §40 | <https://github.com/awslabs/aws-solutions-constructs/tree/v2.103.0/source/patterns/%40aws-solutions-constructs> | **디렉터리 이름이 곧 데이터** |

**`awscfn-cooccurrence` 하나에 코퍼스 둘이 섞여 있고, 두 주소를 열어 보면 그 차이가
바로 보입니다** — §38은 서비스별 데모, §39는 운영용 스택입니다. **편향의 방향이 달라서
섞으면 어느 쪽 편향인지 알 수 없으므로** `_coverage`에 둘의 규모를 따로 적습니다(5.5).

각 소스의 원본 형태는 이렇게 다릅니다.

```
§37 ARM 템플릿      resources[].type 을 셉니다      "type": "Microsoft.Compute/virtualMachines"
§38·39 CFN YAML     Type: 한 줄만 집습니다          Type: AWS::EC2::Instance
§40 디렉터리 이름    이름을 '-'로 쪼갭니다           aws-cloudfront-s3/
§41 컴파일된 ARM    dependsOn + copy.count 를 봅니다 "dependsOn": ["cMKKeyVault"]
§15 KCC 샘플 YAML   kind 가 2개 이상인 것만          kind: AlloyDBCluster
§2 Go 소스          **사람이 읽습니다**              getNodeGroupReqFromDynamicReq()
```

**번들에는 `caveat`가 있고 동시 출현에는 없습니다.** 번들은 특정 출처의 구성이라 그
출처가 스스로 단 경고를 옮길 수 있지만, 동시 출현은 **코퍼스 통계라 경고가 레코드가
아니라 `_coverage`(표본 편향 고지)에** 붙기 때문입니다.

## 5.1 이 축이 4부와 다른 이유

4부(그래프)는 **스키마의 참조를 따라가므로 "가능한 것"은 다 줍니다.** 문제는 **"실제로
필요한 것"을 못 가린다**는 것입니다 — `EC2::Instance`에서 `KMS::ReplicaKey`까지 이어
줍니다.

**실측이 판별자를 줬습니다.**

```
VM이 있는 ARM 템플릿 330개 중
  100.0%  networkInterfaces
   92.4%  virtualNetworks
   72.4%  networkSecurityGroups
    5.8%  routeTables
    5.5%  bastionHosts
```

**분포에 큰 골이 있습니다.** 100%·92% 무리와 5~7% 꼬리가 뚜렷이 갈립니다. 그래프만으로는
이 둘이 구별되지 않습니다.

## 5.2 이 축 전체에 걸린 규율 — `observed`

여기서 나오는 값은 전부 **`observed`**입니다. 뜻은 **"이 사례 뭉치에서 그렇게 나왔다"**
이지 **"클라우드가 그렇게 강제한다"가 아닙니다.** 이 경계를 흐리면 **표본 편향이 곧
사실이 됩니다.**

> **판정에는 안 씁니다** — **100%가 "없으면 안 된다"를 증명하지 못합니다.**

**문턱은 둘이고 사전에 고정돼 있습니다**(`bundlekb/parsers/aqt.py`).

```
MIN_SAMPLES = 20   앵커를 담은 템플릿이 20개 미만이면 그 앵커를 통째로 안 담는다
MIN_HITS    =  3   같이 나온 횟수가 3회 미만이면 그 쌍을 안 담는다
```

**비율에는 문턱을 두지 않습니다.** 낮은 비율을 버리면 **분포 자체가 사라지기**
때문입니다 — 5.1의 *"100%·92% 무리와 5~7% 꼬리"*라는 판별력은 **꼬리를 남겨야만**
보입니다. 실측 분포(2026-07-29):

| | 레코드 | 100%인 쌍 | 100% 미만 | 비율 중앙값 | 표본 범위 |
|---|---:|---:|---:|---:|---|
| `aqt-cooccurrence` | 1,253 | 22 | **1,231** | 0.107 | 20 ~ 476 |
| `awscfn-cooccurrence` | 1,147 | 28 | **1,119** | 0.160 | 20 ~ 145 |

즉 이 축의 **98% 이상이 100% 미만의 관찰**이고, 그것이 이 축이 판정용이 아니라 **판별용**
인 이유입니다. (소스북 07-28 판이 *"100% 미만은 아예 안 담는다"*고 적었는데 **데이터와
어긋납니다** — 8.3에 적었습니다.)

**대신 표본 수를 반드시 함께 담습니다** — 비율만 담으면 **숫자에 없는 확신**을 줍니다.
템플릿이 17개뿐인 앵커의 35.3%는 **6건**입니다.

## 5.3 소스 ① — `tumblebug-src` (§2) · **우리 도구가 실제로 만드는 세트**

**왜 특별한가.** 다른 소스가 *"사람들이 보통 이렇게 만든다"*를 말한다면, 이것은 **"우리
실행 경로가 실제로 무엇을 같이 만드는가"**를 말합니다. **가장 확실한 리소스 군**입니다.

**두 갈래를 봅니다.**

**(a) 동적 번들 — 파싱하지 않고 사람이 읽었습니다.** `provisioning.go`의
`getNodeGroupReqFromDynamicReq`(3216~3529행)를 **눈으로 읽어** 확정한 표를 상수
(`_DYNAMIC_MEMBERS`)로 둡니다.

> **왜 그렇게 했나.** 정규식으로 긁으면 **조건 분기를 놓칩니다.** 소스에 핀이 박혀
> 있으므로 그 확인은 다음 빌드에서도 유효하고, **언제 읽었는지를 `_READ_AT_PIN`에**
> 적어 둡니다. ***"완벽한 데이터셋이 목표지 완벽한 파서가 아니다."***

```json
{"id": "tumblebug::dynamic-vm", "anchor": "core::vm",
 "caveat": "**This is what this tool creates, not what the cloud requires.**
   The four resources (vNet·subnet·sshKey·securityGroup) are shared per connection,
   so existing ones are reused.",
 "members": [{"typeId": "core::securityGroup", "tier": "always", …}, …]}
```

**(b) 큐레이션 템플릿 22개 — 기계로 읽습니다.** `init/templates/*.json`은 구조화돼 있어
짐작이 필요 없습니다. 다만 **원본이 스스로 단 경고를 값과 함께** 담습니다 —
`sg-default`는 *"전 포트를 연다, 프로덕션엔 쓰지 말라"*고 자기가 적어 두었습니다.

## 5.4 소스 ② — `azure-quickstart-templates` (§37) · 가장 큰 코퍼스

`…/Azure/azure-quickstart-templates/tar.gz/331d6f39…` · 커밋 핀 · MIT · tarball **326 MB**

**어떻게 쓰나.** **각 템플릿의 `type` 집합만** 뽑아 동시 출현을 셉니다. 앵커 타입 T가 있는
템플릿을 모수로 두고, 그 안에 함께 나온 타입의 비율을 냅니다.

**포화 기준이 명시돼 있습니다.** **`MIN_SAMPLES`(20) 아래 앵커는 아예 안 담습니다** —
타입 530종 중 **앵커 43개만 남고 487개가 빠집니다.**

**알려진 표본 편향 — 데이터가 아니라 고지로 다룹니다.** Quickstart는 **데모·튜토리얼
쪽으로 기웁니다.** VM과 스토리지 계정이 53.6%로 같이 나오는 것은 **옛 부트 진단 관행의
흔적**입니다. **값을 손보지 않고 `_coverage`에 적습니다 — 보정하면 그게 짐작이 됩니다.**

**크기 처리.** 326MB라 **빌드 때만 받고 산출물엔 파생 표만** 담습니다.

**산출:** `aqt-cooccurrence` **1,253건**.

## 5.5 소스 ③④ — `aws-cfn-templates`(§38) · `widdix-cf-templates`(§39) · **왜 둘인가**

**왜 받았나.** **Azure에서 통한 방법이 AWS에서도 되는지** 재려고. **확인됐지만 다르게
통했습니다.**

```
AWS::Lambda::Function  → AWS::IAM::Role         100.0% (38/38)   구조적 필수
AWS::EC2::Instance     → AWS::EC2::SecurityGroup  90.2%
                         AWS::EC2::Subnet         78.0%
                         AWS::EC2::KeyPair        75.6%
```

Lambda는 실행 역할이 **없으면 안 되므로** 100%가 나옵니다. EC2는 기본 VPC·기본 SG를 쓸
수 있어서 100%가 안 나옵니다 — **분포가 "구조적 필수"와 "관행"을 갈라 보여줍니다.**
**값을 손보면 그 정보가 사라지므로 그대로 담습니다.**

**둘인 이유를 밝힙니다.** AWS 공식 샘플 하나로는 템플릿 299개·앵커 22종뿐이었습니다
(Azure는 1,152개·43종). widdix를 더해 **362개·30종**이 됩니다.

**성격이 다른 둘을 섞는다는 사실을 적습니다** — AWS 샘플은 서비스별 데모이고 widdix는
**운영용 스택**이라 후자에서 CloudWatch 경보가 55.6%로 나옵니다. **편향의 방향이 다르므로
섞으면 어느 쪽 편향인지 알 수 없습니다.** 그래서 `_coverage`에 둘의 규모를 **따로**
적습니다.

**정직한 미달 기록.** **RDS(15건)·EKS(2건)는 그래도 임계에 못 미칩니다.** 채운 척하지
않고 구멍으로 남깁니다 — **`MIN_SAMPLES`를 낮춰 맞추면 그 문턱을 둔 이유가 사라집니다.**

**YAML을 통째로 파싱하지 않습니다.** CFN YAML은 `!Ref`·`!GetAtt` 같은 커스텀 태그 때문에
표준 파서로 못 읽습니다. 필요한 것은 `Type: AWS::X::Y` 한 줄뿐이라 **그 모양만
집습니다.**

## 5.6 소스 ⑤ — `aws-solutions-constructs` (§40) · **이름 자체가 조합**

**왜 이 소스인가.** 번들의 나머지 소스는 전부 Azure 쪽입니다(AVM·Quickstart). **AWS에는
그만한 선언적 번들 카탈로그가 없습니다** — CDK는 TypeScript, SAM은 파이썬 변환 규칙이라
둘 다 코드입니다. Solutions Constructs는 **패턴 이름 자체가 조합**입니다.

```
aws-alb-fargate/  aws-apigateway-dynamodb/  aws-cloudfront-s3/
aws-cognito-apigateway-lambda/  … 81개
```

**조합은 AWS가 말했고, 타입 매핑은 우리가 했습니다.** 이름은 **서비스**를 말하지 리소스
**타입**을 말하지 않습니다. `lambda`가 `AWS::Lambda::Function`인 것은 분명하지만
`fargate`가 무엇인지는 갈립니다(ECS 서비스? 태스크 정의? 클러스터?).

**모호하지 않은 것만 매핑하고 나머지는 담지 않습니다** — 83개 중 52건. 못 붙인 이름을
**개수까지** 적습니다: `fargate`(12) · `dynamodbstreams`(3) · `elasticsearch`(2) ·
`kibana`(2) · `pipes`(2) · `route53`(2) · `apigatewayv2websocket`(1) · `oai`(1).

**caveat가 두 경계를 동시에 말합니다**: *"AWS officially grouped the combination, and
**mapping the service names to resource types is ours**. Attachments the pattern actually
creates, such as IAM roles and log groups, are not included."*

## 5.7 소스 ⑥ — `avm-bicep` (§41) · **판별자를 두 번 틀리고 세 번째에 확정**

이 축에서 가장 어려웠던 소스입니다. **"무엇이 필수 동반자인가"의 판별자**를 찾는 데
세 번 걸렸습니다.

```
condition 있음                      → 선택 (파라미터에 따라)
copy.count에 coalesce/createArray   → 선택 (빈 배열 폴백이 있다)
copy.count에 폴백 없음              → **필수** (값을 반드시 줘야 한다)
둘 다 없음                          → 무조건
```

- **1차 실패** — `condition`만 봤더니 패턴 하나가 **104종을 "항상 배포"**한다고 나왔고,
  한 Cosmos 계정에 Cassandra·Gremlin·Mongo·SQL이 **동시에** 들어 있었습니다.
  `copy`(파라미터 배열 루프)를 안 센 탓입니다.
- **2차 실패** — `defaultValue` 부재를 '필수'로 읽었더니 `Insights/diagnosticSettings`가
  **173개 중 89개 모듈의 필수 동반자**로 나왔습니다. AVM은 Bicep의 nullable 파라미터를
  `defaultValue` 없이 컴파일하고 **사용처에서 `coalesce(x, createArray())`로**
  처리합니다 — **기본값이 파라미터 선언이 아니라 본문에** 있습니다.
- **중첩 배포는 재귀로 풀어야 합니다.** VM의 NIC은 `deployments → deployments →
  networkInterfaces`로 **두 단** 중첩입니다. 한 단만 보면 **VM의 유일한 진짜 필수
  동반자를 통째로 잃고** *"VM은 아무것도 필요 없다"*는 **사실과 정반대인 KB**가 됩니다.

**산출:** `avm-bundles` **207건**(res 169 · ptn 38).

## 5.8 소스 ⑦ — `kcc-crd` 샘플 (§15) · GCP · **두 번 헛짚고 찾았습니다**

**왜 이 소스인가 — 기각 기록이 둘 있습니다.**

1. `cloud-foundation-fabric` 모듈은 **86개 중 63개가 무조건 리소스 0개**였습니다
   (Terraform이 주 리소스에도 `count = var.x_create ? 1 : 0`을 걸기 때문).
2. 변수 기본값까지 추적해도 리소스 선언 **719개 중 46개(6.4%)만** 풀렸습니다.

**억지로 밀지 않고 이미 핀이 박힌 소스의 안 쓰던 디렉터리를 봤습니다** —
`config/samples/resources/`.

**어떻게 쓰나.**

- 시나리오 **443개 중 kind가 2개 이상인 296개만** 담습니다(단일 리소스 147개는 '리소스
  군'이 아닙니다).
- `Namespace`·`Secret`·`ConfigMap`과 `apiVersion`이 `k8s`로 시작하는 것은 **쿠버네티스
  살림이지 클라우드 리소스가 아니라** 뺍니다.
- kind가 우리 graphkb 노드 id와 **그대로 맞습니다**(`gcp::AlloyDBCluster`).

**caveat가 경계를 말합니다**: *"**This is the minimal working configuration Google picked
as an example.** Applying it creates all of them, but that does not mean the API enforces
this set."*

## 5.9 이 축이 지금 답할 수 있는 것 (2026-07-29 실측)

| 산출물 | 건수 | 성격 | 근거 등급 |
|---|---:|---|---|
| `kcc-bundles` | **296** | Google이 고른 최소 동작 구성 | observed(공식 샘플) |
| `avm-bundles` | **207** | 검증 모듈이 배포하는 세트 | observed(모듈 저자 설계) |
| `aws-pattern-bundles` | **52** | AWS 공식 패턴 이름의 조합 | observed(조합은 AWS·매핑은 우리) |
| `tumblebug-bundles` | **23** | **우리 도구가 실제로 만드는 것** | stated(소스 읽음) + 템플릿 |
| **번들 합계** | **578** | | |
| `aqt-cooccurrence` | **1,253** | ARM 템플릿 1,152개 동시 출현 | observed |
| `awscfn-cooccurrence` | **1,147** | CFN 템플릿 362개 동시 출현 | observed |
| **동시 출현 합계** | **2,400** | | |

**앵커 커버리지:** Azure 43개(타입 530종 중) · AWS 30개(182종 중).

## 5.10 이 축의 타당성 위협 — **가장 크게 적어야 하는 축**

- **전부 `observed`입니다.** 판정에 쓰지 않는 이유이고, 답변에 **항상 유보가 붙습니다.**
- **표본 편향이 측정돼 있습니다.**
  - Azure Quickstart는 **데모 쪽으로 기웁니다**(VM↔스토리지 계정 53.6%는 옛 부트 진단
    관행의 흔적).
  - AWS 코퍼스 둘은 **편향의 방향이 다릅니다**(공식 샘플=서비스별 데모,
    widdix=운영 스택). 섞은 사실을 `_coverage`에 적었지만 **분리해 답하지는 않습니다.**
- **코퍼스 크기가 비대칭입니다.** Azure 1,152개 vs AWS 362개. **비율만 보면 두 코퍼스가
  같은 무게로 읽히므로** 그 차이를 `_coverage`에 적습니다.
- **임계 미달 구멍을 남겨 뒀습니다** — RDS(15건)·EKS(2건). **채운 척하지 않습니다.**
- **GCP·AWS 번들의 매핑 단계가 우리 것**입니다(§40의 서비스→타입, §15의 kind 정합은 자동).
- **100%가 필수의 증명이 아닙니다.** Lambda→IAM Role 100%는 구조적 필수가 맞지만,
  **그 판단은 데이터가 아니라 사람이 한 것**입니다.
- **12개 클라우드 중 3곳뿐입니다** — azure·aws·gcp + core(도구). 나머지 아홉은 0건.

---

# 6부 — 다섯 축에 들어가지 않은 소스 (배제 기준 명문화)

47종 중 다섯 축에 직접 쓰이는 것은 **38종**입니다. 나머지 **9종**이 어디에 있고 **왜 이
다섯 축에 넣지 않았는지**를 적습니다 — *구현 범위와 근거 부재를 섞지 않기 위해서입니다.*

## 6.1 설계 지침 산문 — **자문 전용** (§42~45, 4종)

`azure-well-architected`(199편) · `gcp-architecture-framework`(57편) ·
`twelve-factor`(15편) · `aws-well-architected`(PDF 177편)

**왜 축을 분리했나.** 수치로 환원되지 않는 지식입니다. **사실 축에 넣으면 지침이 사실
행세를 합니다.** 넷 다 evidence가 `pattern-advisory`(basis **inferred**)이고,
**검수해도 클라우드 사실이 되지 않는 성격이라 `reviewed`를 붙이지 않는 것이 규약**입니다.

> **자문 원칙**: 이 축의 답에는 **항상** *"설계 지침이지 검증된 사실이 아니다"*라는
> 고지가 붙습니다. 가격·한도 같은 사실 질문은 **다른 축으로 가라고 답 자체가
> 안내합니다.**

**검색은 벡터가 아니라 단어 일치(FTS5)입니다.** 임베딩은 **두 번 검토하고 두 번
기각**했습니다 — 1차엔 검색 실패가 0건이라 **없는 문제**였고, 2차엔 재현율을 실측
(엄격 75% · 주제 적합 ~90%)해 자문 용도에 충분했습니다. **다시 검토할 조건까지 기록해
뒀습니다**(*"실사용에서 자문 오답이 실제 문제로 실측되면"*).

**법적으로 가장 예민한 소스가 여기 있습니다.** `aws-well-architected`는 **라이선스 부여가
없습니다**(법적 고지가 'All rights reserved'뿐 — CC-BY가 명시된 §42·§43과 결정적으로
다른 점). 수록 근거가 라이선스가 아니라 **교육 목적 공정이용 판단**이고, 그 판단은
**허가가 아니므로** 세 곳(NOTICE·산출물 `_note`·문서별 attribution)에 사실을 명시하고
**권리자가 요청하면 제거**합니다. *"허가받은 것처럼 굴지 않는다"*는 문장까지 테스트가
강제합니다.

**추출 방법도 규율입니다** — 산문 휴리스틱이 아니라 **PDF 책갈피 1,334개**로 자릅니다.
1,002쪽 문서가 목차 구조를 기계로 주는데 정규식으로 자르는 것은 **함정을 자초하는
일**입니다. 깊이 3까지를 문서 경계로 씁니다(깊이 4부터는 베스트 프랙티스 낱개 647개라
너무 잘게 쪼개집니다).

**산출:** `pattern-corpus` **346건** + `aws-pattern-corpus` **177건**.

## 6.2 환경·수명 (§32~34, 3종)

`gcp-carbon` · `ccf-emissions` · `endoflife-date`

**왜 다섯 축이 아닌가.** 인스턴스의 성능·비용도, 리소스의 용량·관계도 아닙니다. **리전
선택과 버전 선택**이라는 별개의 결정 축입니다. 다만 **가이드라인 제공**이라는 목표
2의 상위 문장에는 들어갑니다.

**여기에도 같은 규율이 걸려 있습니다.**

- **두 탄소 소스를 같은 축에 놓고 비교하면 안 됩니다.** `ccf-emissions`는 공개 전력
  데이터 추정이고 `gcp-carbon`은 구글 자체 발표라 **방법론이 다릅니다.** 실측이 분명히
  보여줍니다 — **서울은 gcp가 최저(356.6), 도쿄는 aws가 최저(439.8)로 순서가
  뒤집힙니다.** 그래서 **프로바이더 안에서만 비교**하고 레코드마다 `method`를 남깁니다.
- **`carbonFreeEnergy`가 `null`인 것이 중요합니다** — CFE 비율은 **구글만 발표**하므로
  AWS·Azure 쪽은 **빈칸이지 0이 아닙니다.**
- **연도를 고정합니다(2024)** — 최신 연도를 따라가면 **값이 조용히 바뀝니다.**
- `endoflife-date`는 **`false`가 올 수 있습니다** — "종료일이 아직 안 정해졌다"는 뜻이라
  날짜로 읽으면 안 되고, **"종료 예정일 미정"과 "이미 종료"는 다른 답**입니다.
  **라이선스 경계도 파서에 박혀 있습니다** — 저장소는 MIT이지만 README가 본문을
  *"adapted from Wikipedia, CC BY-SA 3.0"*이라고 밝혀서, **frontmatter의 `releases:`만
  읽고 산문은 아예 읽지 않습니다.**

**산출:** `region-carbon` 161건 · `service-lifecycle` 17건.

## 6.3 리전·이미지·엔드포인트 (§4, §11, §1의 image_infos)

`tumblebug-cloudinfo`(리전 188개) · `botocore-endpoints`(9,039쌍) · `basic-images`(6,033건)

**왜 다섯 축이 아닌가.** 다섯 축의 **좌표계**입니다. 리전 정의가 없으면 가격·성능·지연을
어디의 값인지 말할 수 없습니다.

**여기서 나온 규율 셋이 다섯 축 전부에 적용됩니다.**

1. **조인 키만 소문자로 맞추고 원본 표기는 남깁니다.** `kt`·`ncp`·`nhn`은 이 파일이
   `KR1`·`KR`로 적는데 미러는 `kr1`·`kr`로 적습니다. 그대로 조인하면 이 셋이 **0%**가
   되고, 소문자로 맞추면 100%입니다. **`code`는 원본 표기를 남깁니다** — 원본을 고쳐
   쓰면 그건 우리 값이 됩니다.
2. **한국어 별칭은 리전 코드가 아니라 영어 낱말에 붙입니다.**
   `"서울" → ("Seoul","Korea") → (원본 이름에서 찾기) → 프로바이더별 코드`.
   `"서울": "ap-northeast-2"`로 적으면 리전 코드가 우리 표에 박혀서, **프로바이더가
   이름을 바꾸면 표가 조용히 거짓이 됩니다.**
3. **방위 이름은 도시로 매핑하지 않습니다.** `서울`→`Seoul`은 같은 것을 다른 말로 적은
   것이지만 `Southeast Asia`→`싱가포르`는 **새 사실을 주장하는 것**입니다.

**`botocore-endpoints`는 있음만 담고 없음은 안 담습니다.** *"엔드포인트가 없다"*는
*"그 리전에서 못 쓴다"*가 아닙니다 — CloudFront는 엔드포인트가 1개인데 **글로벌
서비스**라서입니다. 판별자(`isRegionalized`)가 **307개 중 22개에만** 있고, 위험군 34개
중 절반인 17개는 판별자가 없습니다.

**`basic-images`는 174,759행 중 6,033건(3.3%)만** 담습니다. `is_gpu_image`는 45.6%에
켜져 있고 그중 79,478건이 AWS 하나입니다 — **45%가 켜진 플래그는 큐레이션 신호가
아니라** "GPU 인스턴스에서 돌아갈 수 있음"에 가까우므로 쓰지 않습니다.

---

# 7부 — 조사 절차 자체에 대하여

축별 내용이 아니라 **어떻게 소스를 골랐는가**입니다. 논문으로 낼 때 리뷰어가 가장 먼저
묻는 것이라 따로 씁니다.

## 7.1 소스 선정 절차 — 재현 가능한 형태

축 하나를 열 때 밟은 순서입니다. 다섯 축 전부 같은 순서를 밟았습니다.

```
① 공백 실측      축의 현재 커버리지를 프로바이더별로 센다 (1.2·2.2·3.4·4.11·5.1)
② 요건 정의      그 공백을 메우려면 소스가 무엇을 줘야 하는가
③ 후보 열거      기계 판독 · 무인증 · 핀 가능 · 라이선스 확인 가능
④ 게이트 통과    넷 중 하나라도 못 지나면 기각하고 **기각 이유를 적는다**
⑤ 실측 검증      받아서 실제로 세어 본다 — 채움률·변별력·교차 일치
⑥ 등급 배정      evidence 라벨 · basis · caveat를 정한다
⑦ 커버리지 기록  담은 것과 **버린 것**을 산출물 안에 적는다
```

**④에서 기각된 것들이 이 절차의 증거입니다.**

| 기각된 후보 | 어느 축 | 기각 이유 |
|---|---|---|
| Magic Modules YAML | 용량 | **태그 0개**·하루 3.6건 변경 — 핀 불가. 게다가 선언이 생성 중 증발 |
| `cloud-foundation-fabric` | 리소스 군 | 86개 모듈 중 63개가 무조건 리소스 0개 — 신호가 안 나옴 |
| `ec2instances.info` 종합 파일(209MB) | 성능 | 커밋돼 있지 않고 AWS 가격표 파생물 — 고정도 재배포도 불가 |
| AWS Service Quotas API | 용량 | 자격증명 필요 (대안 awslimitchecker는 AGPL + 2021년 이후 정체) |
| GCP Cloud Quotas | 용량 | 문서 저장소가 비공개(HTML만) |
| AWS WAF **HTML** 문서 | 지침 | 사이트 약관이 자동 수집 금지 → **PDF 형태로 재검토해 통과** |
| 임베딩 검색 | 지침 | 두 번 검토·두 번 기각 — 1차엔 실패 0건(없는 문제), 2차엔 FTS 재현율이 충분 |
| aws·azure·gcp 밖 8곳의 성능 소스 | 성능 | 조사 결과 **IBM 외에 실재하지 않음** — "부재 확정"으로 기록 |

**⑤에서 단정이 뒤집힌 사례 셋**도 남아 있습니다. *"NHN은 공개 프로바이더가 없다"*(있었음,
110종) · *"botocore shape엔 min/max가 없다"*(있었음, min 183·max 175) · *"Azure 가격
중복의 범인은 `isPrimaryMeterRegion`"*(아니었음, `productName`). **실측 전에 단정하지
마라**의 실제 사례로 셋 다 문서에 남겼습니다.

## 7.2 포화 기준 — "왜 이 개수에서 멈췄나"

축마다 다른 기준을 씁니다. **암묵적으로 멈춘 축이 없도록** 여기 모읍니다.

| 축 | 포화 기준 | 미달을 어떻게 다루나 |
|---|---|---|
| 성능 | 프로바이더별 **공개 소스 전수 조사** — 있으면 넣고 없으면 부재 확정 | 나머지 8곳 0건을 **명시** |
| 비용 | 정가는 미러 전수 · 할인은 **공개 소스가 있는 곳까지** | AWS 할인 부재를 **범위 결정으로 명시** |
| 용량 | 스키마 **전수** — 원본에 있는 축을 다 뽑았는가 | GCP 수치 한도 0건을 **원본 성질로 명시** |
| 의존성 | 소스별 전수 + **evidence별 건수 공개** | 7 CSP 0건을 **"아직 안 만든 축"으로 명시** |
| 리소스 군 | **`MIN_SAMPLES` = 20 · `MIN_HITS` = 3** (둘 다 사전 고정) | RDS 15·EKS 2를 **구멍으로 남김** |

**리소스 군의 문턱이 가장 명시적입니다** — 사전에 정하고, 미달을 채우려고 **문턱을 낮추지
않았습니다.** *"`MIN_SAMPLES`를 낮춰 맞추면 그 문턱을 둔 이유가 사라집니다."*

## 7.3 입도와 배제 기준

| 무엇을 하나로 세나 | 무엇을 뺐나 |
|---|---|
| 제약 = (타입, 속성, 종류, 조건집합) 하나 | 스키마 값이 이미 있으면 산문은 안 만듦(R1) |
| 엣지 = (from, to, type, via_property) 하나 | `readOnly` 속성 기준 엣지 전부 |
| 번들 = 앵커 하나에 붙은 멤버 집합 | kind 1개짜리 시나리오 147개 · 쿠버네티스 살림 리소스 |
| 동시 출현 = (앵커, 타입) 쌍 + hits/samples | 표본 20 미만 앵커 487개 |
| 성능 레코드 = (프로바이더, 리전, 스펙) 하나 | 변별력 없는 칸(값 1가지) · 벤치마크 점수 |
| 가격 레코드 = (스펙, 리전) 하나 | DevTest · Low Priority · 값이 여럿인 SKU |

## 7.4 외부 근거와 어떻게 맞물리나

우리 목록끼리만 정합적이면 **자기참조**입니다. 두 방향을 다 봅니다.

- **소스 간 교차 검증이 실제로 일어난 자리** — AWS 디스크 한도(botocore × Price List,
  10쌍 전부 일치한 20건만) · Azure 가격(API × 미러, 어긋남 0) · IBM 스펙(카탈로그 ×
  미러, 287/287) · 서비스 대응(MS 표 × diagrams, 31건 승급) · GCP 불변성(접두사 × CEL,
  모순 0건).
- **교차 검증이 **없는** 자리를 세었습니다** — 성능 보강 소스 셋(§28·§29·§30) · 일곱 CSP
  용량(§18–24) · 리전 좌표(§4) · 지연(§3). **단일 소스라는 사실이 `_coverage`에
  적힙니다.**
- **독립처럼 보이지만 아닌 자리** — `tp-nhn`과 `tp-openstack`의 84종 겹침은 **같은
  코드**입니다. **독립된 두 소스로 세면 안 됩니다.**

## 7.5 다섯 축 공통의 타당성 위협

축별 위협은 각 부 끝에 적었습니다. 여기는 **다섯 축 전부에 걸린 것**입니다.

1. **벤더 편향.** 소스 47종 중 aws·azure·gcp 대상이 압도적입니다. 국내 클라우드는
   **전 축에서 1% 미만**입니다 — 공개된 기계 판독 자료가 그만큼밖에 없기 때문입니다.
   **이 창고로 "국내 클라우드 가이드라인"을 주장하면 안 됩니다.**
2. **단일 코더.** 손 검수 지점(§7 매핑 · §36 타입 붙이기 · §35 타입 연결 ·
   `reviewed-sizing` · §29 구세대 표 · §40 서비스→타입)이 **전부 한 사람**입니다.
   §29만 문서 라벨과 상호 대조가 걸려 있습니다.
3. **신선도 관리 부재.** 핀 47종이 **언제 낡는지 지켜보는 주기가 없습니다.** 핀이
   박혀 있다는 건 **낡는다**는 뜻이기도 합니다.
4. **재현 불가 5종.** `cfn-schema`(최대 근거 46,911건) · `azure-retail-prices` ·
   `ibm-global-catalog` · `gcp-architecture-framework` · `aws-well-architected`.
   **바뀐 사실은 알 수 있지만 옛 상태로 되돌릴 수는 없습니다.**
5. **라이선스 미확인 4종** — `cfn-schema` · `cdk-oob` · `aws-price-list` ·
   `azure-rest-api-specs`. **"기록 없음"은 "자유롭다"가 아니라 "우리가 아직 확인
   안 했다"**입니다.
6. **정확성(accuracy)을 잰 적이 없습니다.** 정답지가 없어서 **출처는 밝히지만 값이 실제와
   맞는지는 측정 밖**입니다(ISO 25012 매핑에서 드러난 공백). 다섯 축 전부에 해당합니다.
7. **영어 질의 품질 미측정 → 측정됨.** 2026-07-28에 60칸 짝 측정을 마쳤습니다 —
   영어 29/30 · 한국어 30/30으로 **언어 효과 없음**. 부수 발견으로 **한국어로 물어도 답이
   영어로 오는 것이 30칸 중 28칸**이었습니다.

---

# 8부 — 이 문서를 쓰면서 실측과 어긋난 곳

두 소스 문서의 값을 전부 다시 셌습니다. **소스 문서가 데이터와 어긋난 두 곳(8.1·8.3)과
이 문서의 초고가 틀렸던 한 곳(8.2)**을 적습니다(원 문서들은 불변
기록이라 고치지 않습니다).

## 8.1 "정가 73,083건"은 스펙 행 수이지 가격 보유 수가 아닙니다

`kb-sourcebook-2026-07-28.md` §6 커버리지 표의 **"정가"** 열은 프로바이더별 **스펙 행
수**입니다. 실제로 `hourlyUSD`를 가진 행은 **68,705건(94.0%)**이고 **4,378건(6.0%)은
가격 칸이 비어 있습니다.**

가장 크게 갈리는 곳:

```
kt          220행 중 가격 있음 31건 (14.1%)
openstack     6행 중 가격 있음  0건 (0%)
ncp         393행 중 가격 있음 178건 (45.3%)
alibaba   2,494행 중 가격 있음 1,954건 (78.3%)
```

**이 문서는 2.2에 실측 표를 실었습니다.** 축의 규율("부재를 값으로 세지 않는다")을 그
표 자체에 적용한 것입니다.

## 8.2 어긋난 줄 알았으나 아니었던 곳 — 측정 대상을 잘못 잡았다

이 문서의 초고는 azure `maxNics`를 **24,553건**으로 적고 아틀라스(**25,385건**)와
어긋난다고 보고했습니다. **아틀라스가 맞고 초고가 틀렸습니다.**

원인은 값이 아니라 **무엇을 쟀느냐**입니다. `data/tumblebug-perf.json.gz`만 작업
트리에서 수정 중이었고, 초고는 그 **커밋되지 않은 중간 상태**를 셌습니다. 커밋된
상태와 나란히 놓으면 이렇습니다.

| | 커밋(HEAD) | 되돌려지는 중간 상태 |
|---|---:|---:|
| aws 필드 종류 | **23** | 26 (`fieldEvidence`·`maxNics`·`localSsdGB` 추가) |
| aws `maxNics` | **없음** | 18,564 |
| azure `maxNics` | **25,385** | 24,553 (교차 대조 불일치 832건을 값에서 뺌) |
| gcp 필드 종류 | **11** | 12 |

**832 = 25,385 − 24,553**은 `perfkb-field-axis-plan-2026-07-29.md`가 미리 기록한 불일치
건수와 정확히 맞습니다 — 즉 그 계획의 실행분이었고, **지금 되돌려지고 있습니다.**

이 문서는 성능 축 전체를 **커밋된 상태로 다시 셌습니다**(1.9). 남길 것은 값이 아니라
방법입니다.

> **더러운 작업 트리를 재면 문서가 나오는 순간부터 거짓이 됩니다.** 산출물이 커밋돼
> 있다는 것은 **무엇을 기준으로 세었는지 말할 수 있다**는 뜻이고, 그 기준을 밝히지
> 않은 수치는 재현 가능한 수치가 아닙니다. 이 축의 규율(*"어느 스냅샷의 값인지 알 수
> 없게 하지 않는다"*)을 **문서 자신에게 적용하지 않았던 것**입니다.

## 8.3 "100% 미만은 아예 안 담는다"는 데이터와 어긋납니다

`kb-sourcebook-2026-07-28.md` §H 도입부와 `kb-source-atlas-2026-07-29.md` H장 도입부가
동시 출현 축에 대해 **"대신 100% 미만은 아예 안 담습니다"**라고 적었습니다. 실측은
정반대입니다.

```
aqt-cooccurrence      1,253건 중 100%인 쌍  22건 · 100% 미만 1,231건 (98.2%)
awscfn-cooccurrence   1,147건 중 100%인 쌍  28건 · 100% 미만 1,119건 (97.6%)
```

코드가 실제로 두는 문턱은 **비율이 아니라 개수 둘**입니다
(`bundlekb/parsers/aqt.py`) — `MIN_SAMPLES = 20`(앵커의 템플릿 수)과
`MIN_HITS = 3`(쌍의 동시 출현 횟수). 데이터의 최저 비율은 aqt 0.006 · awscfn 0.021이고
표본 최솟값은 양쪽 다 정확히 20입니다.

**두 문서의 다른 서술과도 충돌합니다** — 같은 문서들이 *"100%·92% 무리와 5~7% 꼬리가
뚜렷이 갈린다"*는 판별력을 이 축의 존재 이유로 들고 있는데, **100% 미만을 버리면 그
꼬리가 남아 있을 수 없습니다.** 즉 이 문장은 규율의 서술이 아니라 **문서 쪽의
과장**이고, 코드·데이터·같은 문서의 다른 문단 셋이 모두 반대편입니다.

이 문서는 **코드와 데이터를 따랐습니다**(5.0 경계표 · 5.2 · 7.2).

## 8.4 어긋나지 않은 것

나머지는 전부 일치했습니다 — 제약 141,377 · 엣지 6,548(evidence 분포까지) ·
번들 578 · 동시 출현 2,400 · 할인 43,266 · 관리형 24,294 · 성능 67,034 ·
리전 쌍 10,890 · 산문 346+177.

**성능 축은 커밋 상태에서 아틀라스와 전부 일치합니다** — azure `maxNics` 25,385 ·
`networkBandwidthMbps` 24,108 · gcp 98% 채움. 8.2는 소스 문서의 오류가 아니라 **이
문서 초고의 오측**이었습니다.

**핀 분포**(태그 23 · 커밋 18 · 지문 5 · 동봉 1 = 47)와 **라이선스 미확인 4종**도
아틀라스와 일치합니다. 레코드 실물에 붙인 **소스 키·핀·sha256은 각 산출물의 `_source`
배열에서 직접 읽은 값**입니다.

---

# 9부 — 한 장으로 줄이면

**다섯 축의 정의를 한 줄씩** (상세는 각 부의 `X.0`).

```
① 성능      스펙 하나가 낼 수 있는 처리 능력 중 **발표됐거나 실측된** 속성들
② 비용      같은 스펙을 **어떤 조건으로 쓰느냐**에 따라 달라지는 **단가**
③ 용량      리소스 타입의 **속성 하나에 걸린 한계** (+ 계정에 걸리는 상한은 별도 모델)
④ 의존성    리소스 **타입 둘 사이의 방향 있는 관계** — 의존하는 쪽 → 의존 대상
⑤ 리소스 군  한 리소스를 중심으로 **함께 만들어지거나 함께 나타나는** 묶음
```

**①②의 대상은 스펙(상품), ③④⑤의 대상은 타입(부품)입니다** — 조인 키부터 다르고,
이 문서는 둘을 섞지 않습니다(0.2).

그리고 축별로 한 문장씩.

1. **성능** — 미러 하나로 열고 프로바이더별 공백을 셋으로 메웠으며, **넓히려 조사했더니
   IBM 외에는 공개 소스가 없다는 것이 결과**였다. 승자 선언과 회사 간 비교는
   **구조적으로 막았다.**
2. **비용** — 정가·할인·관리형이 **다른 질문**이고, **합계를 내지 않는 것**이 이 축의
   가장 중요한 판단이다. 재배포 금지는 주석이 아니라 **구조로** 막았다.
3. **용량** — 141,919건 중 최대 근거가 **재현 불가능한 소스**에서 나오고, **"모른다"에
   다섯 종류**를 두어 침묵을 구별한다. 조건이 갈리면 **3상태로** 답한다.
4. **의존성** — 관계 6,548건이 있지만 **12개 클라우드 중 3곳뿐**이고, 근거 등급이
   프로바이더마다 달라 **그 비율이 답에 실려 나간다.** 담김을 지어내지 않은 것이 이
   축의 성격이다.
5. **리소스 군** — 전부 `observed`라 **판정에 쓰지 않고**, 사전에 고정한 문턱 둘
   (표본 20 · 동시출현 3)을 **미달을 메우려고 낮추지 않았다.** 비율에는 문턱을 두지
   않는데, **꼬리를 버리면 "구조적 필수"와 "관행"을 가르는 분포 자체가 사라지기**
   때문이다. 판별자를 두 번 틀린 기록이 남아 있다.

그리고 다섯 축을 관통하는 한 문장.

> **이 조사의 자랑은 아는 게 많은 것이 아니라, 모르는 걸 모른다고 말할 수 있게 만든
> 것이다.** 축마다 "봤는데 없다"와 "안 봤다"가 다른 문장으로 나가고, 버린 것이 개수와
> 이유까지 산출물 안에 적혀 있다.
