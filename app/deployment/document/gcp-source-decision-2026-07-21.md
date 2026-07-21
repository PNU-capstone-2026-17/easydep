# GCP 소스 결정 — KCC냐 Magic Modules냐 (2026-07-21)

**질문**: 둘 중 신뢰도가 높은 쪽을 base로 삼자. 현재 설계를 바꿔야 하는가?

**답 먼저**: 질문의 전제가 틀렸습니다. **KCC는 하나의 소스가 아니라 세 개**이고,
그중 하나(전체의 46%)는 **Magic Modules를 2년 8개월 묵혀 담은 것**입니다.
그래서 "둘 중 하나"가 아니라 **KCC를 백엔드별로 갈라서** 다르게 다뤄야 합니다.

설계는 **구조를 바꿀 필요가 없습니다.** `kind` 값 몇 개와 **출처 등급 한 칸**만 늘리면 됩니다.

---

## 1. KCC는 세 개의 소스다

CRD의 라벨로 갈립니다(실측, 조사 결과와 정확히 일치):

| 백엔드 | CRD | 스키마 출처 | 우리 제약 | 신선도 |
|---|---:|---|---:|---|
| `direct` | 210 | 손으로 쓴 Go 타입, **proto 주석으로 출처 표시** | 870 (20%) | 최신 |
| `tf2crd` | 235 | **벤더링된 terraform-provider-google-beta 4.84.0** | 2,453 (**55%**) | **2023-09-26** |
| `dcl2crd` | 65 | 벤더링된 DCL OpenAPI | 1,098 (25%) | 중간 |

`direct`는 필드마다 `+kcc:proto:field=google.cloud.compute.v1...` 주석이 붙어 있습니다.
**어느 GCP proto 필드에서 왔는지 기계가 확인할 수 있는 유일한 신호**이고, Magic Modules에는
이에 해당하는 게 없습니다.

### 제가 잰 "낡음"이 여기서 설명됩니다

허용값을 양쪽 다 가진 189건 중 **MM이 더 많은 19건, KCC가 더 많은 0건, 모순 0건**이었습니다.
그 낡은 리소스들의 백엔드를 확인하니 **5/5 전부 `tf2crd`**였습니다:

```
BigQueryRoutine · ComputeBackendService · ComputeImage
ComputeInterconnectAttachment · ComputeSubnetwork   → 전부 tf2crd
```

`ComputeSubnetwork.purpose`가 옛 이름 `PRIVATE_RFC_1918`을 쓰고 신규 값을 모르는 것도
같은 이유입니다. **개별 사고가 아니라 계통적 시차입니다.**

---

## 2. 그런데 Magic Modules도 base가 될 수 없다

| | Magic Modules | KCC |
|---|---|---|
| 핀 고정 | **태그 0개, 릴리스 0개** — 커밋 SHA만 | 태그 238개 |
| 변경 속도 | **주 64커밋, 절반이 `mmv1/products/`** (하루 3.6건) | 2~6주 간격 릴리스 |
| 라이선스 | **분할 라이선스**(GitHub이 `NOASSERTION`). `mmv1/products/`는 Apache-2.0 | 깔끔한 Apache-2.0 |
| 커버리지 | KCC kind 527 중 **154종(29%)이 MM에 없음** | — |

소스 핀은 우리 프로젝트의 전제입니다(`kbcommon/sources.py`).

> **정정(2026-07-21)**: "핀을 못 박는다"는 뺄 근거가 **아니었습니다.** 우리는 이미
> `bicep-types-az`와 `azure-limits-doc`을 **커밋 SHA로 핀**하고 있고, AWS CFN zip은
> 아예 `digest`(재현 불가)로 받아들입니다. 커밋 SHA는 digest보다 엄격히 더 재현
> 가능하므로, 제 근거가 우리 자신의 핀 분류와 어긋났습니다.
> 프로바이더를 고른 **다른** 이유(증발 12.7%, `ForceNewIfChange`가 YAML엔 없음)는
> 그대로 유효합니다.

### MM 데이터에는 함정이 하나 더 있다

MM이 선언한 교차 필드 조건 중 **약 12.7%가 생성된 프로바이더에서 빈 목록으로 나옵니다**
(`ExactlyOneOf` 125건, `ConflictsWith` 52건, `RequiredWith` 12건이 통째로 증발).
중첩 객체 안에서 형제 이름을 그냥 쓰면 경로 해석이 루트에서 시작해 실패하는데,
**조용히 버려집니다.**

> 그래서 MM YAML을 곧이곧대로 담으면 **현실보다 엄격한 KB**가 됩니다.
> 우리 원칙(잘못 막는 게 침묵보다 나쁘다)과 정면으로 충돌합니다.

---

## 3. 가장 중요한 정정 — 둘 다 "API가 거부한다"를 말하지 않는다

`Immutable.`도 MM의 `immutable:`도 실은 **Terraform의 `ForceNew`**입니다.
MM 문서가 직접 이렇게 적어 놨습니다:

> *"복잡한 경우에는 사용자가 설정을 적용할 수 있도록 **차라리 `ForceNew`로 표시하는 것이
> 낫다**."*

즉 **의도적으로 과다 표시**합니다. 반대로 조건부 불변(어떤 값일 때만 재생성)은
`CustomizeDiff` Go 코드 안에 있어 **YAML에는 안 보입니다** — 과소 표시도 합니다.

그리고 강제력이 백엔드마다 다릅니다(실측):

| 백엔드 | 불변 표시 | CEL로 강제 |
|---|---:|---:|
| direct | 184 | 61 (33%) |
| tf2crd | 1,182 | 6 (**1%**) |
| dcl2crd | 637 | 1 (**0%**) |

`tf2crd`·`dcl2crd`는 CEL이 없어도 **admission webhook이 `ForceNew`로 막습니다**(같은
플래그에서 나오므로 일치가 보장됨). 문제는 `direct`입니다 — webhook이 direct 리소스를
**그냥 통과시키는** 코드 경로가 있어서, `Immutable.`이라 적혔지만 **어디서도 강제되지 않는
필드가 약 180개** 있습니다.

> **우리 표시 문구는 우연히 맞았습니다.** `"생성 후 변경 불가 (바꾸면 리소스 재생성)"`은
> 정확히 `ForceNew` 의미입니다. `"API가 거부한다"`라고 적었으면 틀렸을 것입니다.
> 이건 운이 좋았던 것이므로 문구를 고정해 둡니다.

---

## 4. 결정

### 뼈대는 KCC — 단, 백엔드별로 등급을 나눈다

| 백엔드 | 취급 |
|---|---|
| `direct` (210) | **base로 신뢰.** proto 주석이 있어 출처 확인 가능 |
| `dcl2crd` (65) | 사용. TF 경로보다 API에 가깝지만 얼어붙은 의존성 |
| `tf2crd` (235) | **그대로 신뢰하지 않는다.** 2년 8개월 낡음. 값은 재확보 대상 |

이유는 단순합니다 — **핀을 박을 수 있고, 라이선스가 깨끗하고, 154종은 MM에 아예 없고,
KCC 자신이 백엔드를 라벨로 선언**해서 등급을 기계적으로 매길 수 있습니다.

### 값은 MM에서 보강 — 단, 프로바이더 릴리스를 핀으로

`tf2crd` 리소스의 낡은 값을 메우는 데 MM을 쓰되, **핀은 MM 커밋이 아니라
`terraform-provider-google` 릴리스 태그**(417개, 주간 릴리스)로 잡습니다. 같은
파이프라인의 **버전이 매겨진 산출물**이고, KCC가 벤더링한 4.84.0보다 3개 메이저 최신입니다.

보강할 항목(같은 373종 기준 실측):

| | MM | 현재 |
|---|---:|---:|
| `enum` | 665 | 13 |
| `default` | 326 | 4 |
| `exactly_one_of` / `at_least_one_of` / `conflicts` / `required_with` | 779 | 0 |
| `update_url`(부분 갱신) | 85 | 0 |

### 세 번째 소스를 하나 더 둔다 — Discovery 문서

조사가 잡아낸 사례: `SubnetworkLogConfig.metadata`의 기본값을
**MM은 `INCLUDE_ALL_METADATA`, GCP Discovery 문서는 `EXCLUDE_ALL_METADATA`**라고 합니다.
MM 쪽은 버그가 아니라 **Terraform의 클라이언트 측 기본값**입니다 — API의 기본값이 아닙니다.

`googleapis.com/discovery/v1/apis/*/rest`는 버전이 있고 받을 수 있으며 **API 자신의 말**입니다.
기본값·필드 의미는 여기를 기준으로 삼고, 어긋나면 검수 대기열에 올립니다.

> **정정(2026-07-21)**: Discovery에 **수치 한도는 없습니다.** compute v1을 직접 파싱해
> `schemas` 안 `minimum`이 **0건**임을 확인했습니다(201건은 전부 페이지네이션 파라미터).
> 기본값·`readOnly`(1,466)·`enum`(707)에는 여전히 쓸모 있지만 **범위 추출기를 만들면
> 안 됩니다.** 또 `googleapis` proto는 저장소 전체로는 `IMMUTABLE` 2,941건인데
> `compute/v1/compute.proto`는 **0건**입니다(그게 Discovery에서 생성되므로).
> — [추가 소스 조사](source-survey-2026-07-21.md)

---

## 5. 설계는 바꿔야 하는가 — 구조는 그대로, 두 가지만 는다

`Constraint(type_id, property, kind, value, evidence, basis, ...)` 모델이 새 데이터를
**그대로 받습니다.** 구조 변경 없음.

### (1) `kind` 값 추가

```
exactly_one_of / conflicts_with / at_least_one_of / required_with   value = 형제 속성 목록
mutability: update_restricted                                       note에 메서드명
```

`update_restricted`가 새 축입니다. `update_url`의 끝단어가
`expandIpCidrRange` · `resize` · `setMachineType`인데, 이건 **"늘릴 수만 있다"** 류라
불변/가변 이분법으로는 안 담깁니다. 메모(`schema-vs-prose`)에 적어 둔
*"디스크는 늘릴 수는 있어도 줄일 수는 없다"*가 바로 이것입니다.

### (2) 출처 등급 한 칸 — `tier`

**이번 조사의 실질적 산출물입니다.** 같은 `evidence` 라벨이라도 백엔드에 따라
신선도가 다르므로, 라벨을 쪼개는 대신 등급을 답니다:

```json
{
  "evidence": "kcc-immutable-prefix",
  "basis": "stated",
  "tier": "tf2crd",        // direct | dcl2crd | tf2crd
  "note": "벤더링된 terraform-provider-google-beta 4.84.0(2023-09-26) 기준"
}
```

이걸로 `cap_*` 도구가 "이 값은 낡았을 수 있다"를 **말할 수 있게** 됩니다. 지금은
4,421건이 전부 같은 얼굴이라 55%가 2년 8개월 묵었다는 걸 사용자가 알 방법이 없습니다.

### (3) `condition`에 대한 제 판단 정정

설계 문서에서 `condition`을 "조건부 제약의 일반해"처럼 적었는데, **GCP 데이터는
`condition`을 전혀 안 씁니다.** 둘은 다른 클래스입니다:

- **값 의존 한도**(AWS EBS): "gp2면 최대 16,384" → `condition`. 여전히 4건.
- **필드 존재 규칙**(GCP MM): "셋 중 하나만" → 새 `kind`. 779건.

`condition`을 더 일반적으로 설계했더라도 `exactly_one_of`는 **여전히 못 담았을 것**입니다.
좁게 둔 것이 결과적으로 맞았습니다.

---

## 6. D1에 대한 조치

이미 커밋한 D1(4,421건)의 **55%가 `tf2crd`에서 왔습니다.** 데이터를 버릴 필요는
없습니다 — `ForceNew` 의미로는 여전히 유효하고, 표시 문구도 맞습니다. 다만:

1. **`tier`를 붙인다**(D1-a). CRD 라벨을 읽기만 하면 되므로 작습니다.
2. **`tf2crd` 값의 낡음을 사용자에게 고지한다.** 지금은 침묵합니다.
3. 그 다음 **D6(MM/프로바이더로 값 보강)**.

---

## 부록: 확인 방법

- 백엔드 라벨: `cnrm.cloud.google.com/tf2crd` · `dcl2crd`, 없으면 direct.
  재현 `tools/kb_audit/gcp_backend.py`
- 낡음 대조: `tools/kb_audit/gcp_enum_drift.py` (189건 중 MM 우세 19 / KCC 우세 0)
- MM 불변 도출 규칙: `(리소스 immutable) AND (update_url 없음) OR (속성 immutable)`,
  `name ≡ resourceID`. 8종 표본에서 Jaccard 88%, 4종은 100%.

**추론(출처 아님)**: `tf2crd` 값이 앞으로도 계속 낡으리라는 것(현 스냅샷 1회 관측),
Discovery 문서가 우리 용도에 충분하다는 것(사례 1건으로 확인).
