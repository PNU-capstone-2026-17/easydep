"""하류에서 나온 사실을 **상류로 되돌린다** — 목표 ①의 고리.

## 왜 있나

검증은 여럿 있었다(RTM · 관심사 커버리지 · 계획↔실측 대조 · 인용 대조). 그런데
**결과가 사람에게만 갔다.** 대조기가 위반을 찾아도 계획을 만든 쪽이 그것을 모르고,
다음 실행도 똑같이 만든다.

이 모듈이 그 결과를 두 방향으로 되돌린다:

    계획으로   위반은 계획의 **미결**로 올라간다. 계획을 읽는 모든 소비자가
               따로 대조를 돌리지 않아도 본다.
    요구사항으로  **사용자 답으로 닫히는 것**만 되묻기로 올린다. 나머지는
               우리 코드의 결함이지 사용자에게 물을 일이 아니다.

## 이 저장소는 여기서 한 번 물렸다

되돌아가기(C2, `requirements/agent/supervisor.py`)를 만들었는데 **값을 못 냈다** —
처리 범위가 기저를 감쌌고 비용만 1.9배였다. 그래서 기본값이 0으로 되돌아갔다.

같은 실수를 피하려고 순서를 뒤집었다: **고리를 붙이기 전에 되먹임이 결과를
바꿀 수 있는지부터 쟀다**(2026-08-01). 첫 측정에서 `multiZone`을 줘도 계획이
한 글자도 안 바뀌는 것이 나왔고, 원인이 **계획 생성기가 실측 폐포를 안 본다**는
것이었다(k8s 번들에 네트워크·서브넷이 없다). 즉 그때의 되먹임은 **답해도 소용없는
질문**을 냈을 것이다.

그래서 이 모듈은 되묻기를 만들기 전에 **닫을 수 있는가**를 먼저 본다.

## 무엇을 사용자에게 묻고 무엇을 안 묻나

가르는 기준은 하나다 — **그 값을 받으면 위반이 닫히는가.**

    닫힌다     계약의 되묻기로 올린다(`input_registry`의 그 칸)
    안 닫힌다  우리 코드의 결함이다. 미결로만 올리고 사용자를 괴롭히지 않는다

이 구별이 없으면 우리 버그를 사용자에게 질문으로 떠넘기게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core import plan_crosscheck
from app.core.cloudkb.appkb.plan import DeploymentPlan

#: 대조 결과의 종류 → **사용자 답으로 닫히는가.**
#:
#: 값이 있으면 그 계약 칸을 물으면 닫힌다는 뜻이고, `None`이면 사용자가 답해도
#: 안 닫힌다(우리 코드나 하류 스키마의 몫). **표를 작게 유지한다** — 늘리려면
#: "그 값을 받으면 정말 닫히는가"를 재고 나서다.
_CLOSED_BY: dict[str, str | None] = {
    plan_crosscheck.VIOLATED_RULE: None,      # 계획이 규칙을 어긴 것 — 생성기의 몫
    plan_crosscheck.MISSING_REQUIRED: None,   # 필수 자원 누락 — 생성기의 몫
    plan_crosscheck.DOUBLE_CREATE: None,      # 이중 생성 — 생성기의 몫
    plan_crosscheck.REDUNDANT_NODE: None,     # 정상일 수 있다 — 묻지 않는다
    plan_crosscheck.UNCHECKED_RULE: None,     # 계획 형식의 몫(AZ 칸 등)
    plan_crosscheck.ABSENT_ORDER: None,       # 배선으로 닫힌다
    plan_crosscheck.ABSENT_WARNING: None,
    plan_crosscheck.ABSENT_WAIT: None,
    plan_crosscheck.WEAK_READING: None,       # 계획 자료 모델의 몫
    plan_crosscheck.OUT_OF_VOCABULARY: None,  # 실측이 없다 — 사용자가 못 닫는다
    plan_crosscheck.OUT_OF_SCOPE: None,       # 선언된 경계 — 결함이 아니다
}

#: 미결로 올리지 않는 종류. **경계와 이미 배선된 것**이다 — 올리면 미결 목록이
#: 늘 차 있어서 진짜 미결이 묻힌다.
_QUIET = (plan_crosscheck.OUT_OF_SCOPE,)


@dataclass(frozen=True)
class Feedback:
    """되돌린 것 한 건."""

    kind: str
    subject: str
    #: 계획의 미결로 올릴 문장.
    unresolved: str
    #: 사용자에게 물어 닫을 수 있으면 그 계약 칸. 아니면 빈 문자열.
    ask_field: str = ""


def collect(plan: DeploymentPlan, csp: str, region: str = "-") -> tuple[Feedback, ...]:
    """계획을 대조해 되돌릴 것을 모은다. **계획을 고치지 않는다.**

    고치는 것은 생성기의 일이고, 여기서는 무엇이 어긋났는지를 계획이 스스로
    들고 다니게 할 뿐이다.
    """
    result = plan_crosscheck.crosscheck(plan, csp, region)
    out: list[Feedback] = []
    for finding in result.findings:
        if finding.kind in _QUIET:
            continue
        field = _CLOSED_BY.get(finding.kind) or ""
        out.append(Feedback(
            kind=finding.kind, subject=finding.subject,
            unresolved=(f"[{finding.kind}] {finding.subject}: "
                        f"{finding.observed} / 실측: {finding.measured}"),
            ask_field=field))
    return tuple(out)


def apply_to_plan(plan: DeploymentPlan, csp: str, region: str = "-") -> int:
    """되돌린 것을 계획의 **미결**로 올린다. 올린 개수를 돌려준다.

    `unresolved`에 두는 이유: 계획을 읽는 모든 소비자가 이미 그것을 읽는다
    (`intake_report` · 답변 렌더러 · 표본 점검). 새 칸을 만들면 읽는 쪽을 다
    고쳐야 하고, 안 고친 쪽에서는 위반이 **조용히 사라진다.**
    """
    found = collect(plan, csp, region)
    for item in found:
        plan.unresolved.append(item.unresolved)
    return len(found)


def questions(plan: DeploymentPlan, csp: str, region: str = "-") -> tuple[dict, ...]:
    """사용자에게 되물을 것 — **답하면 닫히는 것만.**

    지금은 비어 있는 것이 정상이다. 대조 결과 전부가 우리 코드나 하류 스키마의
    몫이고, 그 사실 자체가 측정 결과다 — *"검증 결과를 사용자 질문으로
    떠넘기지 않는다"*.
    """
    from app.core import cloud_contract

    out = []
    for item in collect(plan, csp, region):
        if not item.ask_field:
            continue
        out.append({
            "field": item.ask_field,
            "question": cloud_contract.question(item.ask_field),
            "why": cloud_contract.why(item.ask_field),
            # **근거는 하류에서 온 사실이다** — "이걸 안 줘서 이 위반이 났다".
            "evidence": item.unresolved,
        })
    return tuple(out)
