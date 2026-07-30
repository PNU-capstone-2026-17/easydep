# P5a 측정 — azure preflight가 의존에 대해 말한 것 (2026-07-30)

> 측정 기록. 원자료는 `results.json`(오류 원문 발췌 포함), 입력은
> `templates/`(생성기 `gen_templates.py`). 자원 생성 없음 — validate·what-if만.
> 대상: 구독 "Azure for Students" · RG `depkb-preflight`(koreacentral, 빈 껍데기).

## 1. 컨트롤 플레인이 필연을 이름으로 말한다

| 후보 간선 | 실험 | preflight 판정 | 뜻 |
|---|---|---|---|
| `nic→subnet` | 생략 | **거부 `SubnetIsRequired`** | 존재-필수 **확인**. 스키마 층은 침묵했는데(`requiredInSchema: false`) 컨트롤 플레인이 말한다 — 오라클 층화가 실제로 값을 낸다 |
| `nic` 내부 | ipConfiguration 생략 | 거부 `NetworkInterfaceMustHaveIpConfigurationOrAttachedElasticNetworkInterface` | NIC은 ip 구성 없이는 못 선다(단 attached ENI 대안 — 술어 후보) |
| `loadBalancer→?` | frontend 참조 생략 | **거부 `FrontendIPConfigurationHasNoSubnetOrPublicIPAddressOrPublicIPPrefix`** | **선언형(disjunctive) 필연** — subnet ∨ publicIP ∨ publicIPPrefix 중 하나. 우리 후보는 `lb→subnet`·`lb→publicIp` 두 간선이었는데 진실은 **3항 선택 술어**다. 주장 형식의 술어 슬롯이 필요한 이유의 실증 |
| `network→subnet` | 생략 | 통과 | VNet은 서브넷 없이 만들어진다(통과는 약한 증거 — 스키마 관측과 정합) |
| `publicIp`·`firewall`·`disk` 단독 | 대조군 | 통과 | 독립 생성 가능 — 후보(입력 참조 없음)와 정합 |

## 2. preflight의 경계 — 허상 참조는 못 본다 (T7의 실측)

| 실험 | 판정 |
|---|---|
| `dangling-nic-subnet` (없는 서브넷 id) | **통과** |
| `dangling-subnet-parent` (없는 VNet 밑 서브넷) | **통과** |
| `dangling-lb-pip` (없는 PIP id) | **통과** |

**preflight는 "슬롯이 채워졌는가"는 보지만 "가리킨 것이 존재하는가"는 안 본다.**
참조 해석은 apply(P5b)의 몫이다 — 계획이 위협 T7로 가정했던 커버리지 편향이
가정이 아니라 측정이 됐다. 따라서 preflight 층에서 "검증됨"이라 적을 수 있는 것은
**구조적 필연**까지다.

## 3. preflight 깊이는 리소스 프로바이더마다 다르다

가용 SKU(`Standard_B2ats_v2`)로 재실행한 결과, `omit-vm-nic`이 **통과**했다.
azure VM은 실제로는 NIC 없이 만들어지지 않으므로, 이것은 "vm→nic이 선택"이라는
뜻이 아니라 **Compute RP가 preflight에서 구조 필연을 검사하지 않는다**는 뜻이다
(통과는 증거가 아니라는 규율이 여기서 실제로 일한다). 대조적으로 Network RP는
셋을 이름까지 붙여 거부했다. 즉:

- **preflight의 커버리지는 균일하지 않고 RP별로 갈린다** — Network는 깊고
  Compute는 얕다. `vm→nic` 필수성은 preflight 층에서 **미판정**이고 apply(P5b)
  대상이다.
- 1차 실행의 교란도 기록해 둔다: `Standard_B1s`가 이 구독·리전에서
  `SkuNotAvailable`이라 VM 2건이 의존 검사에 도달하지 못했었다. preflight는
  SKU 가용성(환경 제약)을 구조 검사보다 먼저 본다 — 실험 템플릿은 의존 축 외의
  모든 축이 유효해야 한다(테스트 격리).

## 4. 층별 판정 요약 (이 측정이 주장할 수 있는 것)

| 간선/사실 | preflight 층의 판정 |
|---|---|
| `nic→subnet` 필수 | **확인** (`SubnetIsRequired`) |
| `lb→(subnet ∨ publicIp ∨ pipPrefix)` | **확인** — 3항 선언 술어로 |
| `nic` ipConfiguration 필수(∨ attached ENI) | **확인** |
| `network→subnet` 비필수 · pip/nsg/disk 독립 | 통과 — 스키마 관측과 정합(약한 증거) |
| `vm→nic` 필수 | **미판정** (Compute RP가 preflight에서 안 봄) → P5b |
| 허상 참조 4종 | **미판정** (preflight는 참조 해석 안 함) → P5b |

## 5. 다음

- P5b(apply): `vm→nic` 생략 · 허상 참조 4종 — **비용 게이트 결정 필요**(분 단위
  생성-삭제, 소액 예상).
- `nic→firewall`(NSG) 생략의 직접 측정(현재는 간접 신호뿐).
- RG `depkb-preflight`는 빈 껍데기로 남겨 둔다(무료) — P5b가 쓰면 된다.
