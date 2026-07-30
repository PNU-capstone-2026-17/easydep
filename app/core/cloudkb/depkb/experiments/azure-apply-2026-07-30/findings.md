# P5b 측정 — apply 층이 닫은 것 (2026-07-30)

> 측정 기록. 원자료 `results.json`, 사슬 템플릿 `chain.json`, 실행기 `run.py`.
> A국면 5건은 전부 거부로 끝나 자원이 생기지 않았고, B~D는 무료 자원만
> (vnet·subnet·NSG·NIC×2) 썼다. **VM은 만들어지지 않았고 잔여물은 0이다**
> (`residual: []`). 비용 ≈ 0.

## 1. preflight가 못 보던 다섯을 apply가 전부 잡았다

| 실험 | apply 판정 | 닫힌 주장 |
|---|---|---|
| VM에 networkProfile 생략 | 거부 `InvalidParameter` | **`vm→nic` 존재-필수 확인** — preflight 미판정이던 것 |
| NIC→없는 subnet | 거부 `InvalidResourceReference` | 참조는 해석되어야 한다 |
| 없는 VNet 밑 subnet | 거부 `ResourceNotFound` | 소속 부모는 실재해야 한다 |
| VM→없는 NIC | 거부 `NotFound` | 〃 |
| LB→없는 PIP | 거부 `InvalidResourceReference`/`NotFound` | 〃 |

P5a에서 측정한 preflight의 두 경계(허상 불감·RP별 깊이 차)가 apply에서 전부
닫혔다 — **오라클 층화가 설계대로 작동한다.**

## 2. 생명주기 질문의 첫 동적 증거 (C국면)

| 변이 | 판정 |
|---|---|
| NIC가 쓰는 subnet 삭제 | 거부 **`InUseSubnetCannotBeDeleted`** — 막는 NIC를 경로로 지목 |
| 그 vnet 삭제 | 거부 (사용 중 서브넷 때문에 같은 코드) |
| NIC에 붙은 NSG 삭제 | 거부 **`InUseNetworkSecurityGroupCannotBeDeleted`** |

삭제 보호가 참조의 역방향으로 걸린다는 것(lifecycle 질문)이 azure 실물에서
확인됐다. D국면(역순 삭제 nic→nsg→subnet→vnet)은 전부 성공 — 양성 대조이자 청소.

## 3. 덤으로 닫힌 것

- **`nic→firewall`(NSG)은 선택** — B국면의 nic1이 NSG 없이 생성에 성공했다
  (preflight 간접 신호였던 것이 apply 직접 증거가 됨).

## 4. 첫 완전 검증 간선

**`nic→subnet`이 네 층 전부에서 확인된 첫 간선이다:**
스키마(입력 참조 4건) → preflight(`SubnetIsRequired`) → apply 허상 거부
(`InvalidResourceReference`) → 생명주기(`InUseSubnetCannotBeDeleted`).
주장 형식의 "도달한 최고 오라클 층"이 이 간선에서 처음으로 꼭대기까지 찼다.

## 5. 남은 것

- `vm→disk`·`network→subnet` 등 나머지 후보의 같은 사다리 완주(기계적 반복).
- 삭제 경로의 CB 드라이버 판정(중립화 지도 미결)과 이 lifecycle 실측의 대조.
- aws·gcp는 계정이 없어 전 층 미실행 — azure 결과를 일반화하지 않는다(T9).
