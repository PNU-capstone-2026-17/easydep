# 상·하류 배선 계획 — 기존 에이전트에 덧대기 (2026-08-01, 실행 전 · 2판)

목적: *"클라우드 네이티브 요소를 현 시스템에 추가한다."* 118주장이 쌓였는데
**아무도 `app/core/infra_planning`을 부르지 않는다.** 분석이 시스템 가치가
되는 지점이 이 배선이다.

> **1판을 버렸다.** 1판은 요구사항 뒤에 정적 단계를 두려다 앵커를 못 정해
> 막혔고, 그 다음엔 별도 에이전트를 신설하려 했다. 둘 다 과했다 —
> **이미 같은 패턴이 있다**(`step_resource`, 2026-07-29). 여기에 덧댄다.

## 0. 사슬의 실물 (확인함)

| 단계 | 산출물 | 만드는 곳 |
|---|---|---|
| 요구사항 | `classified·actors·use_cases·use_case_specs·relationships·usecase_diagram` + `cloud_concerns`·`resource_spec` | `app/requirements/`(내 코드) |
| 설계 | `class_diagram·sequence_diagram·erd·api_spec` | `app/design/`(**팀원 코드 — 안 건드린다**) |
| 배포 의도 | `easydep-deployment-intent/v1alpha1` (전부 k8s 층) | 시스템 구현 에이전트(외부) |
| 구현 | Dockerfile·k8s YAML — 결정적 renderer | 하류 |

## 1. 병목은 앵커였고, 답은 "묻는다"

`closure(anchor, csp)`를 부르려면 앵커가 필요한데 얻을 길이 셋 다 막혔다:
요구사항엔 정보가 없고 · 설계 산출물엔 **배포 단위 신호가 실물에서 안 나오고**
([[easydep-upstream-sample]] 가려진 것 1) · 배포 의도는 하류라 이미 k8s로
결정된 뒤다.

**넷째 길이 안 막혀 있다: 사용자에게 묻는다.** "무엇을 배포하는가"는 산출물에서
짜낼 것이 아니라 사용자가 아는 것이다. 그리고 이 저장소에는 그러라고 만든
기제가 이미 있다 — `resource_questions` → `ResourceAnswer` → `resume_analysis`.

## 2. 형태: `step_resource`와 같은 "도구 쓰는 에이전트"

새 파일 `app/requirements/agent/steps/step_infra.py`. 제어 흐름을 코드에 박지
않는다(그러면 워크플로다). 목표만 준다: **인프라 의도를 낼 수 있으면 내고,
못 내면 정확한 질문을 남겨라.**

    지각   resource_spec(있으면) · cloud_concerns · 앞선 되묻기의 답
    행동   앵커 후보 조회 · 폐포 계산 · CSP별 결정 조회 · 계획 검사 ·
           **되묻기**(ask_user) · 기록(record) · 마치기(finish)
    관찰   폐포가 성립했는가 · 아직 사람이 정할 것이 남았는가
    정지   finish · 도구 호출 없는 답변 · 턴 상한

**도구 목록**(전부 depkb의 사영 — 새 지식이 아니다):

| 도구 | 하는 일 | 근거 |
|---|---|---|
| `list_anchors(csp)` | 이 CSP에서 앵커가 될 수 있는 자원 목록 | claims의 subject 집합 |
| `plan_closure(anchors, csp)` | 폐포·생성 순서·doNotCreate·운영 경고 | `app/core/dependency` |
| `open_decisions(csp)` | 사람이 정해야 하는 것(선언 술어·조건부) | claims의 predicate |
| `check_plan(plan, csp)` | 구체 계획의 규칙 위반 | `depkb.check` |
| `ask_user` · `record` · `finish` | `step_resource`와 같은 것을 재사용 | — |

**지어냄 방지는 `step_resource`와 같은 장치**: 값마다 자기가 본 자리를 대게
하고, 미측정 CSP·자원이면 도구가 죽는다(`closure`가 이미 그렇다).

## 3. 자리: `stages.py`에 `plan_infrastructure` 그룹

**자기 그룹으로 둔다.** `refine_requirements`에 넣으면 배치가 건너뛰어 평가
세트가 영원히 못 잰다(C2에서 물린 자리 · `cover_cloud_concerns`·
`structure_constraints`가 같은 이유로 자기 그룹이다).

순서: `structure_constraints`(provider·region이 정해짐) **뒤**. provider 없이는
주장이 CSP로 색인돼 있어 아무것도 못 한다.

산출물: `infra_intent.json`(요구사항 산출물 디렉터리) — `design`/`provision`
두 뷰와 `questions`·`unmeasured`·`operationalWarnings`.

## 4. 하류 배선(C)

1. **`infra-intent` 병렬 산출물** — 배포 의도를 **대체하지 않는다**.
   `layer: "cloud"`·`notForLayer: ["kubernetes"]`가 이미 경계를 말한다.
   먹지 않아도 기존 사슬이 그대로 도는 **비파괴 추가**다.
2. **`downstream.py` 표 갱신** — iamRole 실측으로 낡은 칸이 있다:
   `capabilities.serviceAccount` = MISSING("권한 축은 우리 계획에 없다")
   → **partial**: aws EKS는 역할 필수(실측) · VM은 3사 선택 · **떼면 기능이
   깨진다**(vm→iamRole function holds). 다만 "이 앱이 어떤 권한을 원하는가"는
   여전히 우리 축이 아니다.
3. 하류 intent에 **클라우드 자원 층 칸이 없다**는 것은 우리가 못 고치는 스키마
   공백이다 — `infra-intent`를 병렬로 내는 것이 그 우회다.

## 5. 구현 순서

1. `depkb`에 `decisions_for(csp)`·`anchors_for(csp)` — 앵커 없는 CSP 단위 조회
   (claims의 사영, 새 지식 아님).
2. `steps/infra_tools.py` — 위 도구들(`@tool`).
3. `steps/step_infra.py` + `prompts.INFRA_AGENT_SYSTEM`.
4. `stages.py`에 그룹 추가 + `@contract` 선언.
5. `downstream.py` 표 갱신.
6. 테스트: 도구가 미측정에 죽는가 · 앵커 없으면 질문이 나오는가 · 그룹이
   배치에서 도는가.

## 6. 위협

- **T-이른 질문**: 요구사항 단계에서 "무엇을 배포하나"를 묻는 것이 이를 수
  있다. 완화: 못 정해도 **질문만 남기고 통과**한다(계약 미충족이 정상 경로 —
  `step_resource`가 이미 그렇다).
- **T-과대 질문**: CSP 전체 결정을 나열하면 무관한 것까지 묻는다. 완화:
  앵커가 정해진 뒤의 결정만 묻고, 그 전에는 앵커만 묻는다.
- **T-스키마 공백**: 하류가 `infra-intent`를 먹는다는 보장이 없다. 완화:
  비파괴 병렬 산출물.
- **T-평가 공백**: 새 그룹의 효과를 잴 세트가 없다. 기존 평가 세트는 요구사항
  품질을 잰다 — 이 그룹은 다른 것을 낸다(별도 측정이 필요하다는 것만 적어 둔다).
