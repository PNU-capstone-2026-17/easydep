# 참고 프로젝트(usecase) 분석 — 스텝별 검증·프롬프팅에서 적용할 것 / 안 할 것

> 대상: `C:\Users\projw\Desktop\dev\capstone\usecase` (Cockburn 기반 4단계 멀티에이전트).
> 목적: 우리 파이프라인 아티팩트 품질 문제(orphan 액터, system-as-actor 등)를 참고 프로젝트의
> 검증·프롬프팅 기법으로 개선하되, **AI slop(맹목 복사·임의 사전·과잉설계)을 피해** 선별한다.
>
> 구조만 참고하고 코드/네이밍은 우리 방식으로. 우리는 이미 "의미는 LLM, 구조 검증은 결정론"
> 패턴(check_coverage / _validate_spec / render_diagram / 주액터 보강)을 깔아둠 → 그 위에 얹는다.

---

## 0. 참고 프로젝트를 관통하는 메타 원칙 (= slop 방지의 핵심)

> **"정적으로 결정 가능한 규칙은 LLM이 아니라 코드가 판정한다. 의미 판단만 LLM에 위임하되,
> 임의 키워드 사전을 만들지 않고 권위는 Cockburn의 실제 근거에만 둔다."**

- 예: black-box lint는 Cockburn Reminder 7이 **직접 예시한 UI 용어**(screen/page/button/click/
  field/menu…)만 빠른 필터로 쓰고, "내부 컴포넌트 누출" 같은 판단은 규칙 인용 LLM Validator에
  위임. **임의 실패-키워드 가드·동사 사전은 제거함**(오버피팅 = slop).
- 우리가 새 검증을 추가할 때 이 규율을 그대로 따른다: 결정론엔 표준기법(정규식·집합연산),
  의미판단엔 근거 프롬프트. 임의 목록 금지.

---

## 1. 우리가 관찰한 품질 문제 ↔ 참고 프로젝트의 대응

| 우리 문제 | 참고 프로젝트가 막는 방식 |
|---|---|
| **system-as-actor** (`E-commerce System` kind=system이 orphan) | 액터는 **외부 인간/외부 시스템만**. "시스템 자신은 절대 액터가 아니다"(경계 자체). system 종류를 아예 만들지 않음 |
| **orphan 액터**(어떤 UC에도 안 걸림) | 액터를 **actor-goal로 함께** 도출(모든 goal에 액터 有) → 액터는 goal이 있어서 존재 = 항상 연결 |
| 요구 누락 | 커버리지를 **코드로** 강제, orphan FR을 재프롬프트로 보충(최대 3회) |
| 확장 남발(Handle X Failure UC) | **실패 승격 금지** 프롬프트 + 코드 가드. 실패=확장으로 유지, extend는 진짜 elective만 |
| 시나리오에 if/else·마크다운 | 정적 체크(branch words, control token, black-box lint) + 스타일 프롬프트 |

---

## 2. 적용 대상 — Tier 1 (싼 값 · 관찰된 문제 직결 · 즉시 적용)

### T1-1. system-as-actor 제거 + orphan-actor 결정론 체크
- **무엇**: step2 `identify_actors`에서 `kind: system`을 없앤다. 액터는 primary(외부 인간)·
  supporting(외부 시스템)만. 프롬프트에 "설계 대상 시스템 자신은 액터가 아니라 경계다" 명시.
- **+ 결정론 체크**: `check_coverage`처럼, 도출된 액터 중 어떤 유스케이스에도 primary/supporting
  으로 안 걸린 액터를 **orphan_actors로 플래그**. (참고 프로젝트는 애초에 안 생기게 하지만,
  우리는 생성 후 결정론 점검이 우리 패턴과 일관.)
- **근거**: 사용자가 직접 지적한 결함. Cockburn: 시스템은 SuD(경계)이지 액터 아님.

### T1-2. 경계 리트머스 테스트를 프롬프트에 (actors + relationships)
- **무엇**: "이 컴포넌트가 **이 애플리케이션의 일부로 빌드·배포된다면 내부**다(액터/외부시스템
  아님)"는 한 문장 리트머스를 액터/관계 프롬프트에 추가.
- **근거**: 참고 프로젝트의 가장 재사용성 높은 anti-hallucination 지시. 값싸고 효과 큼.

### T1-3. step3 정적 체크 확장 (`_validate_spec`에 결정론 추가)
현재 `_validate_spec`은 분기/복귀 참조만 본다. 아래를 **순수함수**로 추가 → `issues`:
- **NO_BRANCHING**: main_scenario/handling 문장에 `\b(if|else)\b` → 위반. (Cockburn: MSS는 무분기)
- **NO_CONTROL_TOKEN**: `Success!`/`Fail!` 프로즈 삽입 금지(그건 outcome 필드 몫).
- **BLACK_BOX_LINT**: **Cockburn Reminder 7이 예시한 UI 용어만**(screen/page/button/click/field/
  menu/window/form/dialog/tab/checkbox) 단어경계 매칭. 그 이상의 임의 사전 금지.
- **CONTRACT**: preconditions·success_guarantee 비었는지.
- **근거**: 전부 정적 결정 가능 → 코드가 판정(참고 checklist.py). 스텝 수(3~9)는 **게이트하지
  말 것**(참고도 neutral hint로만) — 의미 판단이라 슬롭 유발.

### T1-4. step4 실패-승격 금지 + 참조 검증
- **프롬프트**: relationship 프롬프트에 "실패/에러/타임아웃/취소/빈결과 처리는 extend/파생 UC로
  올리지 마라. extend는 액터가 **선택적으로 opt-in**하는 진짜 optional에만(예: MFA)."
- **코드 가드**: 관계가 참조하는 UC/액터 이름이 실제 목록에 없으면 **제거**(현재는 렌더에서
  alias fallback으로 조용히 통과 → 대신 결정론적으로 드롭+플래그). "Handle X Failure" 류
  파생 UC 필터.
- **근거**: 참고 ARCHITECTURE §4 실패처리 정책. 확장 남발(=slop) 차단.

---

## 3. 적용 대상 — Tier 2 (중간 노력 · 품질 상승 큼 · 다음 단계 권장)

### T2-1. 통합 Validator + reflection 루프 (generate→validate→repair)
- **무엇**: 스텝 산출물마다 **정적 체크(코드) + 의미 체크(LLM) → 코드에서 병합**해 verdict.
  실패 시 **수술적 directive**를 붙여 재생성, 통과/예산소진까지(bounded, 마지막 정상본 유지).
- **왜 우리에게 맞나**: 우리 "의미=LLM, 구조=결정론" 철학의 정점. 지금은 issues를 **경고로만**
  남기는데, 이걸 **되먹여 고치는** 루프가 품질을 실제로 올림.
- **핵심 설계(참고 validator_agent.py)**: 정적 결과를 LLM이 "말로 무마" 못 하게 코드에서 merge;
  directive는 실패당 1개·중복 제거·길이 상한(퇴화 방지).
- **비용**: 스텝당 LLM 호출 +1~N. 우리 ThreadPool 병렬과 결합 가능.

### T2-2. step2 커버리지 강제-수리 루프
- **무엇**: `check_coverage`가 orphan FR을 찾으면, **그 FR만** 담아 재프롬프트로 유스케이스를
  보충(최대 3회). 현재는 탐지만 함.
- **근거**: 참고의 커버리지 100% 보장 메커니즘. 우리 check_coverage에 자연 확장.

### T2-3. 경량 rules-KB (공유 그라운딩)
- **무엇**: Cockburn 핵심 규칙 몇 개를 **페이지 인용과 함께 상수**로 두고 생성·검증 프롬프트에
  **동일 주입**(생성 기준 == 검증 기준). RAG 없이 하드코딩.
- **근거**: 참고의 규칙 KB(결정론 floor). "생성이 통과하려는 기준"과 "검증이 요구하는 기준"
  일치가 핵심.

---

## 4. **적용하지 않을 것** (면밀 검토 결과 — 우리 맥락엔 과잉/슬롭)

| 항목 | 왜 안 하나 |
|---|---|
| **self-consistency(5) + SetVote/Medoid 집계** | 스텝당 LLM 5배 비용. 목적이 "서빙 비결정성 대응 재현성 지표". 재현성이 **채점 항목이 아니면** 과잉. 우리 병렬화(속도)와는 목적이 다름 |
| **Cockburn 도서 전체 RAG**(임베딩·npz 인덱스) | 인프라 무겁고 외부 의존↑. 필요하면 T2-3 경량 rules-KB(하드코딩 인용)가 80/20 |
| **임의 키워드 사전**(실패-동사 가드 등) | 참고 프로젝트도 **명시적으로 제거**함. 오버피팅=slop. 의미판단은 근거 LLM에 |
| **actor_registry**(UC 워커 간 액터명 정규화) | 우리는 유스케이스를 **한 번의 LLM 호출**로 다 뽑아 이름 드리프트가 적음. step2↔3↔4 이름 참조가 실제로 어긋나면 그때 도입 |
| **MSS 스텝 수 게이팅** | Cockburn도 3~9는 heuristic. 게이트하면 억지 패딩/삭제 유발(slop). neutral hint까지만 |

---

## 5. 권장 적용 순서

1. **Tier 1 전부** (싸고 관찰된 결함 직결): T1-1 system-actor 제거+orphan 체크 → T1-2 경계
   리트머스 → T1-3 step3 정적체크 → T1-4 step4 실패승격 금지+참조검증.
2. 효과 확인 후 **T2-1 validator+reflection 루프**(가장 큰 품질 상승) → T2-2 커버리지 수리 →
   필요 시 T2-3 rules-KB.
3. Tier 4의 항목들은 **채점 요건(재현성 등)이 생기기 전까지 보류**.

각 적용은 반드시 **정적=코드 / 의미=근거LLM / 임의사전 금지** 규율을 지킨다.
