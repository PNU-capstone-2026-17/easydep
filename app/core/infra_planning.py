"""인프라 계획의 접근점 — 다른 에이전트가 부르는 **하나의 문**.

정의와 근거는 `app/core/cloudkb/depkb`에 있다(3사 실측 주장과 그 소비 절차).
여기서는 상류·하류 에이전트가 쓸 수 있게 **한 번의 호출로 끝나는 진입점**을
연다 — 부르는 쪽이 depkb 내부 모듈 순서를 알 필요가 없어야 한다.

## 이 문을 부르는 두 에이전트

- **설계 에이전트(배포 다이어그램)**: `plan(...).design` — 노드·간선·근거·빈칸.
- **구현 에이전트(manifest + IaC)**: `plan(...).provision` — 순서·doNotCreate·
  검사 규칙·막힌 결정. **`layer: "cloud"`이고 manifest 층에 대해서는 아무 주장도
  하지 않는다**(침묵을 '제약 없음'으로 읽지 말 것).

## 규율은 아래에서 그대로 올라온다

모르면 죽는다(unknown 간선) · 대신 정하지 않는다(선택은 질문으로) · 서버가
채우는 것은 고지한다 · 우리 축 밖 신호는 사유와 함께 기록한다.

## 경계

이 모듈은 **다른 영역의 코드를 부르지 않는다.** 배포 의도(dict)를 받고 결과
(dict)를 돌려줄 뿐이라, 설계·구현 에이전트는 자기 파이프라인에서 이 함수를
호출해 산출물에 실으면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.cloudkb.depkb.check import Report, check
from app.core.cloudkb.depkb.infra_intent import InfraIntent, build
from app.core.cloudkb.depkb.translate import Translation, translate
from app.core.cloudkb.depkb.views import design_view, provision_view


@dataclass(frozen=True)
class InfraPlan:
    """한 번의 호출이 내는 것 전부."""

    intent: InfraIntent
    design: dict
    provision: dict
    #: 하류 신호를 어떻게 읽었나 — 앵커의 근거와 못 정한 것.
    translation: Translation | None = None
    #: 계획을 함께 준 경우의 검사 결과.
    report: Report | None = None
    #: 사람에게 물어야 하는 것 전부(번역의 미결 + 의도의 결정).
    questions: tuple[str, ...] = ()
    #: 측정하지 않아 말할 수 없는 것 — 침묵하지 않는다.
    unmeasured: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


def plan_from_deployment_intent(
    deployment_intent: dict, csp: str, region: str,
    concrete_plan: dict | None = None,
) -> InfraPlan:
    """배포 의도(k8s 층)에서 인프라 계획을 만든다 — 에이전트가 부르는 기본 경로.

    Args:
        deployment_intent: `easydep-deployment-intent/v1alpha1` 사전.
        csp: `aws` · `azure` · `gcp`. 주장이 CSP로 색인돼 있어 필수다.
        region: 계획에 실릴 리전(판정에는 쓰이지 않는다 — 우리 주장은 리전
            불변으로 관측됐고, 그 한계는 여정 문서 §7에 적혀 있다).
        concrete_plan: 이미 채운 계획이 있으면 규칙 위반을 함께 본다.

    Raises:
        ValueError: 앵커를 하나도 못 읽었을 때. **추측하지 않는다** — 무엇을
            고를지 모르면 계획도 없고, 이유는 `questions`가 아니라 예외로 낸다.
    """
    t = translate(deployment_intent)
    if not t.anchors:
        raise ValueError(
            "배포 의도에서 클라우드 앵커를 읽지 못했다: "
            + " / ".join(t.open_questions or ("워크로드가 없다",)))
    return _assemble(list(t.anchors), csp, region, concrete_plan, t)


def plan_for_anchors(anchors: list[str], csp: str, region: str,
                     concrete_plan: dict | None = None) -> InfraPlan:
    """앵커를 직접 아는 경우의 경로 — 배포 의도 없이도 부를 수 있다."""
    return _assemble(anchors, csp, region, concrete_plan, None)


def _assemble(anchors: list[str], csp: str, region: str,
              concrete_plan: dict | None, t: Translation | None) -> InfraPlan:
    intent = build(anchors, csp, region)
    report = check(intent, concrete_plan) if concrete_plan is not None else None
    questions = tuple(d.question for d in intent.decisions)
    if t is not None:
        questions = tuple(t.open_questions) + questions
    notes = []
    if t is not None and t.ignored:
        notes.extend(f"{signal}: {why}" for signal, why in t.ignored)
    return InfraPlan(
        intent=intent,
        design=design_view(intent),
        provision=provision_view(intent),
        translation=t,
        report=report,
        questions=questions,
        unmeasured=tuple(t.unmeasured) if t else (),
        notes=tuple(notes),
    )
