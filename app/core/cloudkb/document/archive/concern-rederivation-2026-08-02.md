# 관심사 축 실측 재도출 (2026-08-02)

> 계기: 계약 계보 감사가 관심사 축 자체에 같은 질문을 불렀다 — *"cn.*는 무슨
> 근거로 도출된 것들이지? 다 필요한 건 맞아?"* 구 판(29건)의 근거는 전량 벤더
> 회색문헌이었고, 우리 실측 커버리지와 괴리가 실재했다(29건 중 PURE 실물 0이
> 7건 · 탄소는 열쇠말 13건 전부 오탐). **사용자 결정: 싹 치우고 환경측 실측만을
> 근거로 제로베이스 재도출. 수요측은 필수 후보만 뽑아 둔다.**

## 1. 도출식

메타 특성은 구 판 그대로다 — *"요구사항 단계에서 확정할 수 있고, 확정하지 않으면
클라우드 실행 환경이 그 답을 대신 정해 버리는 결정."* 바뀐 것은 **입장 관문**이다:

    구  판   문헌 좌표(doc_id·probe)가 코퍼스에 실재하면 후보
    신  판   컨트롤 플레인 실측이 "침묵의 답"을 보였을 때만 후보

"침묵의 답" = 그 결정을 안 정했을 때 환경이 실제로 무엇을 하는지의 **관측**.
claims.json의 부류가 그 관측이다: server-default(기본값 대체) · server-implicit
(암묵 합성) · 동반 정리 · 잔존 · 무방비. **거부(required) 계열은 제외** — 거부는
"환경이 대신 정함"이 아니라 계획이 처리할 제약이다(infra_planning이 소비).

T-출발 집합 순환(계보 감사)을 피한다: 출발 집합이 "현행 관심사 목록"이나 "현행
배선"이 아니라 **실측 산출물**(claims.json 119주장 + perfkb)이다.

## 2. 결과 — 7관심사, 53 claims 좌표 + perfkb 1

| 관심사 | 좌표 | 침묵의 답 (관측) |
|---|---|---|
| `cn.network-isolation` | 7 | 기본 VPC·default 네트워크·auto 서브넷이 대체한다 (azure는 대체 없음 — 3사 반전) |
| `cn.reachability` | 4 | 기본 SG 부착·LB 내부/외부 스킴이 접근성을 정한다 |
| `cn.platform-owned-resources` | 13 | LB·디스크·노드풀·vnet·NIC를 플랫폼이 암묵 합성한다 |
| `cn.data-fate-on-removal` | 10 | 경로마다 반대 — OS/부트 디스크는 잔존, PVC 디스크·LB는 동반 소멸 |
| `cn.runtime-binding-protection` | 12 | 결속 변이를 컨트롤 플레인이 안 막는다 (상실·회복 실측) |
| `cn.cloud-api-access` | 7 | 3사 3색 — gcp 무권한·azure 합성·aws 무프로필 |
| `cn.load-shape` | perfkb | 하한만 주면 최저가=버스트가 답이 된다 (100 중 37 함정) |

군집(53좌표 → 7결정)은 **우리 구성**이고 그렇게 표시한다. 좌표 유일성(한 주장
키는 한 관심사에만)은 구 판 doc_id 유일성의 계승이며 `verify_concerns`가 CI에서
대조한다 — 대조 대상이 패턴 코퍼스에서 **claims.json으로** 바뀌었다.

신호(열쇠말)는 승계만 한다: `load-shape` ← `traffic-shape` ·
`reachability` ← `network-exposure` (질문이 같은 결정, 코퍼스 채굴·측정 이력
유효). 나머지 5건은 신호 없음 = LLM 층만 판정(`unjudged`로 정직하게 드러난다).

## 3. 대조표 — 구 29건의 운명

**실측 대응을 얻어 계승 (2)**: traffic-shape→load-shape · network-exposure→reachability.

**계약이 직접 묻는 것으로 존속 — 관심사로는 소멸 (4)**: expected-scale(→`scale`) ·
redundancy-target(→`multiZone`) · cost-ceiling(→`monthlyBudgetUSD`) ·
data-residency(→`dataResidency`, **칸 자체는 계보 감사 결정 대기**).

**실측이 재구성한 인접 질문 (3)**: disposability(인스턴스 상실→자원 제거의
`data-fate-on-removal`로) · backing-services(위임 일반론→합성 관측의
`platform-owned-resources`로) · identity-access(사용자 인증→앱의 클라우드 권한
`cloud-api-access`로). 질문이 같지 않으므로 승계가 아니라 재구성이다 — 신호를
물려받지 않았다.

**실측 관문을 통과하지 못해 목록 밖 (20)**: stateless-process ·
config-externalised · scale-out · transient-fault · event-record ·
operational-signal · service-limits · managed-vs-self · performance-target ·
critical-flow · recovery-objective · degradation-policy · release-continuity ·
data-classification · encryption-obligation · tenancy-model · usage-quota ·
carbon-constraint(계보 감사에서 이미 계약 제거) · regulatory-obligation ·
cross-service-consistency.

**괴리의 실물이 곧 결과다**: 벤더 문헌 29 중 실측이 뒷받침한 것 2 + 인접 3,
그리고 **실측이 드러냈는데 문헌 축에 없던 결정 4**(잔존/동반 정리 · 암묵 합성 ·
무방비 · 권한 3사 3색 — 어느 벤더 프레임워크 문서도 "우리 컨트롤 플레인은 이
변이를 안 막는다"라고 적지 않는다).

## 4. 수요측 필수 후보 (사용자 지시 — 후보만, 실증 대기)

판정식: 사용자 진술 실물이 있고 **없으면 앱이 돌지 않는** 것.

**고유 후보는 식별되지 않았다.** "없으면 안 도는" 결정들 — 노출(azure
secure-by-default에서 접근 불가 실측) · 영속(디스크 운명) · 권한(자격증명 상실)
— 은 전부 환경측 실측이 이미 입장시켰다. 남는 수요측 실물(다중화 24건 · 규모
4건 · 부하 모양)은 "없어도 도는" 것들이고 계약이 직접 묻는다. 이 결론은 부재
주장이므로 코퍼스 확장 시 재검이 필요하다.

## 5. 치른 값 (위협)

- **커버리지 주장이 좁아졌다.** 새 축은 어휘 23종·3사·IaaS 경계를 물려받는다.
  Nickerson의 comprehensive 주장은 버렸다 — "넓게 훑는 체크리스트"에서 "관측된
  것만 말하는 체크리스트"로. 목록 밖 20건이 무의미하다는 뜻이 아니다 — **우리가
  잰 범위에서 침묵의 답이 관측되지 않았다**는 뜻이고, 실측이 도착하면 돌아온다.
- **군집·질문문·승격은 우리 구성** — basis는 여전히 inferred, 고지 유지.
- **커버리지 측정(396표)·라벨 계기·분화 측정은 구 축의 기록** — 유효한 이력이되
  새 축의 측정이 아니다. 새 축 재측정은 미실행(신호가 2건뿐이라 결정론 층
  커버리지가 좁다는 것도 그 측정이 보여줄 사실이다).
- **iso25010 공백이 커졌다**(5/9 미매핑) — "환경이 대신 정하지 않는 특성"과
  "우리가 아직 안 잰 특성"을 이 축은 못 가른다.
