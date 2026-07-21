# 산출물 퀄리티 3-way 비교 — baseline vs 자동화(피드백 X) vs 자동화(피드백 O)

**입력**: `inputs/toystore.json` (FR 15 + NFR 8 = 23개 요구사항, 사전분류본)
**모델**: NIM `openai/gpt-oss-120b` · 채점: 결정론 검증기(`app/agent/compare.py`) + RTM(`app/agent/rtm.py`)

| 모드 | 방법 | artifact run |
|---|---|---|
| **baseline** | 순진한 2콜(분류·검증·반성·커버리지 강제 전부 없음) `intake→생성(1콜)→관계(1콜)` | `run_20260711T072336Z_bce98f7ef4` |
| **자동화(피드백 X)** | 4단계 분해 + 단계별 정적/의미 검증·반성·커버리지 강제·참조 가드 | `run_20260711T072437Z_b8dd8871fe` |
| **자동화(피드백 O)** | 위 산출물에 리뷰어(=본인) 자연어 피드백 1회 → 명세 전반 재생성 + 하위 cascade | `run_20260711T072810Z_b8dd8871fe` |

---

## 1. 정량 지표 대비

| 지표 | baseline | 자동화(피드백X) | 자동화(피드백O) | 방향 |
|---|---:|---:|---:|---|
| 액터 수 | 5 | 4 | 4 | — |
| 유스케이스 수 | 12 | 12 | 12 | — |
| **FR 커버리지** | 16/23 (orphan 7) | **15/15** | **15/15** | ↑ |
| **NFR 부착** | 0/0 | **8/8** | **8/8** | ↑ |
| **NFR 검증(ack)** | 0/0 | **8/8** | **8/8** | ↑ |
| 명세당 평균 확장(예외·대안흐름) | 1.4 | 3.2 | 2.8 | ↑ |
| 명세당 평균 success_guarantee | 1.0 | 3.7 | 3.5 | ↑ |
| 명세당 평균 minimal_guarantee | 1.0 | 3.2 | 3.2 | ↑ |
| **잔여 의미 이슈(자체 검출)** | 0* | 6 | **1** | ↓ |
| **설계 누출(내부 컴포넌트 노출)** | 3 | 2 | **0** | ↓ |
| **NFR 관심사가 FR 확장에 혼입** | 0** | 3 | **0** | ↓ |
| 관계 includes / extends / general. | 10 / 4 / 2 | 3 / 0 / 1 | 2 / 0 / 1 | 주의 |

> `*` baseline의 잔여 이슈 0은 **깨끗해서가 아니라 의미 자체검사를 아예 안 돌려서**다(설계 누출 3개는 여전히 존재).
> `**` baseline은 FR/NFR 분류를 안 하므로 "NFR 확장 혼입"이 정의상 0 — 대신 NFR 8개를 FR로 취급해 그중 7개를 orphan으로 흘렸다.

---

## 2. baseline → 자동화 : 무엇이 좋아지나

### (a) FR/NFR 분류 유무 — 커버리지의 의미가 다르다
- **baseline**: 23개를 전부 FR로 취급. 매핑 안 된 품질제약 **7개가 orphan**으로 사라짐 — 접근성(NFR-01), 브라우저 지원(NFR-02), 로드시간(NFR-03), 비밀번호 해싱(NFR-05), 데이터보호(NFR-06), 반응형(NFR-07), 동시접속 100(NFR-08). 즉 **비기능 요구가 설계에서 증발**한다.
- **자동화**: FR 15개는 100% UC로 커버, NFR 8개는 관련 UC에 **부착(attached)되고 명세에서 acknowledge(ack)** 된다. NFR이 유실되지 않는다.

### (b) 명세 깊이(Cockburn) — 예외·대안 흐름과 보장
- baseline 명세는 대부분 **확장 1개, 보장 각 1개**로 얇다.
- 자동화는 명세당 **확장 3.2개, success 3.7 / minimal 3.2개 보장**으로, 결제 실패·재고 부족·검증 실패 등 예외 흐름과 사후조건을 구체화한다.

### (c) 투명성 — 자동화는 자기 결함을 안다
- baseline은 검증을 안 하므로 결함(설계 누출 3건 등)을 **모른 채 통과**시킨다.
- 자동화는 의미 검증기가 **잔여 이슈 6건을 스스로 기록**(`spec.issues`)한다. 자가수리(repair) 예산 안에서 못 고친 것은 남겨 **다음 단계(사람 피드백)의 입력**이 된다.

---

## 3. 자동화(피드백 X) → 자동화(피드백 O) : 리뷰어 피드백 1회의 효과

**투입한 자연어 피드백(리뷰어=본인):**
> "명세 전반을 Cockburn 블랙박스 원칙에 맞게 정리해줘. 내부 구현 컴포넌트 이름(예: Pricing service, Recommendation service)을 시나리오나 확장에 노출하지 말고 관찰 가능한 시스템 행위로 바꾸고, 시스템 용량 초과 같은 성능·NFR 관심사는 기능 유스케이스의 확장 흐름에서 제거하고, 감사/로깅 동작은 메인 시나리오 스텝이 아니라 success_guarantee로 옮겨줘."

**시스템 처리(자동 분류→재생성→cascade):**
```json
intent   = { stage: "specs", scope: "broad", instruction: "...black-box..." }
regenerated = "specs"        // 명세 전반 재생성
cascaded    = ["relationships","diagram"]   // 하위 단계 fresh 재실행
consistency = { coverage_ratio: 1.0, orphan_fr: 0, dropped_refs: 0, spec_issues_total: 1 }
```

**효과:**
- 잔여 의미 이슈 **6 → 1**, 설계 누출 **2 → 0**, NFR의 FR확장 혼입 **3 → 0** (전부 개선 방향).
- 커버리지 1.0·orphan 0 **유지**(피드백이 커버리지를 깨지 않음).

### before / after 실제 산출물
**UC12 View recommended toys — 내부 컴포넌트 누출 제거**
```
[before] System selects a set of toys based on popularity and any available visitor preferences
         → (issue: "Recommendation service" 내부 컴포넌트 노출)
[after]  Visitor requests recommended toys
         System displays recommended toys        ← 관찰가능한 블랙박스 행위
```

**UC8 Place an order — NFR 확장 제거 + 결제 실패 흐름 강화**
```
[before] 확장: 2a 주소오류 · 2b 결제정보오류 · 3a 결제실패 · 4a 주문기록실패
              · *a "System load exceeds capacity for concurrent users"   ← NFR을 FR 확장에 혼입
[after]  확장: 2a 주소/결제정보 오류 · 3a 결제게이트웨이 거절 · 3b 결제게이트웨이 불가
              · 4a 재고 할당 실패     ← 용량(*a) 제거, 결제 실패를 더 현실적으로 세분화
```
NFR-08(동시접속 100)은 삭제된 게 아니라 **precondition/guarantee로 이동**한다(예: UC1 "operating within its supported concurrent user capacity (≥100)"). NFR을 올바른 자리로 옮긴 것.

> avg_ext가 3.2→2.8로 소폭 준 것은 품질 저하가 아니라 **가짜 NFR 확장을 걷어낸 결과**다.

---

## 4. 결론

| | baseline | 자동화(피드백X) | 자동화(피드백O) |
|---|---|---|---|
| NFR 보존 | ✗ (7개 유실) | ✓ (8/8 부착·검증) | ✓ |
| 명세 깊이 | 얕음 | 깊음 | 깊음 |
| 결함 인지 | 못 함(0은 미검사) | 스스로 기록(6) | 대부분 해소(1) |
| 설계 누출 | 3 | 2 | **0** |
| 사람 개입 비용 | — | 없음 | 자연어 1문장 |

- **baseline**은 빠르지만 분류·검증·커버리지 강제가 없어 **NFR을 통째로 흘리고 얕은 명세에 결함을 인지조차 못 한다**.
- **자동화(피드백 X)** 는 커버리지·NFR·명세 깊이에서 baseline을 압도하고, **못 고친 잔여 결함을 투명하게 노출**한다.
- **자동화(피드백 O)** 는 그 잔여 결함을, 리뷰어의 **자연어 한 문장**을 정확한 stage/scope로 라우팅해 재생성·cascade하여 **커버리지를 깨지 않고 해소**한다.

### 재현
```bash
python -m scripts.run_baseline_only toystore                     # baseline
python -m app.run_pipeline toystore                              # 자동화(피드백 X)
python -m app.apply_feedback <run_dir> "<위 피드백 문장>"          # 자동화(피드백 O)
```
