"""다섯 설계 산출물이 공유하는 스테이지 골격 — 스펙 하나로 노드를 찍어낸다.

**하나의 골격.** 예전에는 두 계열이 있었다. 클래스·ERD는 구조화된 모델을 받아 결정론적
으로 렌더했고(수리 루프 없음), 시퀀스·API·배포는 LLM이 PlantUML/JSON을 직접 써서
validate → repair 루프가 필요했다. 지금은 다섯 모두 이 골격을 따른다:

    생성:   extract_{stage} → [check_{stage}] → render_{stage} → END
    피드백: revise_{stage}  → [check_{stage}] → render_{stage} → END

  extract — LLM이 **구조화 모델**을 내놓는다(자유 텍스트 아님). ERD만 예외로 LLM 대신
            클래스 다이어그램의 BCE에서 시드한다.
  revise  — 사용자 피드백을 **모델에** 적용한다. 렌더된 텍스트는 절대 편집하지 않는다.
  check   — **모델이 규칙을 지켰는지** 결정론으로 판정하고, 어겼으면 유계로 재생성한다.
            `check_key`를 가진 스펙에만 생긴다(지금은 클래스 다이어그램뿐).
  render  — 모델 → 산출물. 결정론적이고, 입력을 중화하므로 구성에 의해 유효하다.
            그리고 **그 산출물이 성한지 스스로 확인해** 상태에 기록한다.

## check 와 render 는 다른 것이다

한때 `convert`와 `validate`가 따로 있었다. 나눠 둔 값이 없어서 합쳤다 — 문법 검증은
변환의 출력만 보고, 변환이 sanitize로 구성에 의해 유효한 산출물을 내므로 **원리상 실패할
수 없다.** 그래서 "절대 울리지 않는 노드"가 그래프에 다섯 개 떠 있었다.

그 사실은 동시에, 오랫동안 **"LLM이 낸 내용이 옳은가"를 아무도 묻지 않았다**는 뜻이기도
했다 — 문법은 항상 통과하고 의미는 검사한 적이 없었다. `check`가 그 자리를 채운다.

둘을 합치지 않는 이유는 **보는 것도 실패의 뜻도 다르기** 때문이다:

  - `check`  — **렌더 전, 모델**의 내용(`knowledge/rules.py`). 실패 = LLM이 틀렸다.
  - `render` — **렌더 후, 산출물**의 문법. 실패 = 우리 변환기가 깨졌다.

대응이 정반대다. 앞은 재생성이 옳고, 뒤는 재생성이 정확히 틀렸다(LLM은 우리 렌더러를
고칠 수 없다). 한 목록으로 뭉개면 예전 문법 수리 루프의 버그가 그대로 돌아온다.

## 예전에 없앤 수리 루프와 무엇이 다른가

없앤 것은 **문법 수리 루프**였다. LLM이 PlantUML/JSON 텍스트를 직접 쓰던 시절, 문법
오류를 되먹여 텍스트를 다시 쓰게 했다. 문제가 둘이었다 — 피드백이 렌더된 텍스트를
편집하므로 모델과 그림이 어긋날 수 있었고, LLM이 오류를 못 고치면 **종료 조건이
없었다.** 구조화 출력 + 결정론 렌더로 그 오류 자체가 불가능해지면서 루프도 사라졌다.

여기 있는 것은 그것이 아니다:

  - 고치는 대상이 **텍스트가 아니라 모델**이다(`spec.revise`). 그림은 언제나 모델의
    순수한 투영이므로 어긋날 자리가 없다.
  - 판정이 **결정론**이라 위반 수를 셀 수 있고, **줄지 않으면 멈춘다.** 종료가 보장된다.
  - 예산이 유계다(`DESIGN_MAX_REPAIR_ITERS`, 기본 2).

## 자동으로 고쳐 주지 않는다

코드가 위반을 직접 손보지 않는다. 대부분의 위반은 **정답이 유일하지 않기** 때문이다 —
Boundary-Entity 직결을 고치려면 Control을 끼워야 하는데 그게 어떤 Control인지는 판단이다.
그리고 코드가 조용히 고치면 LLM이 무엇을 틀렸는지가 산출물에서 사라진다. `sanitize`가
이미 그 함정에 빠져 있다(중화는 검증이 아니다). 그래서 여기서는 **고칠 기회를 주고,
남은 것은 드러낸다.**
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from app.design.knowledge.detectors import Finding
from app.design.schemas.architecture_state import ArchitectureState

#: 왜 재생성을 멈췄는가. **"위반 0건"과 "예산이 끝났다"를 같은 값으로 두지 않기 위해 있다.**
CLEAN = "clean"                    # 위반이 없다
BUDGET = "budget"                  # 예산을 다 썼는데 위반이 남았다
NO_IMPROVEMENT = "no_improvement"  # 재생성이 위반을 줄이지 못했다 → 직전본을 지켰다
ERROR = "error"                    # 재생성 호출이 실패했다 → 직전본을 지켰다
#: 검사는 했고 재생성은 **시도하지 않았다.** 지목 수정(`cascade.py`)의 값이다 — 그 경로는
#: 사용자가 지목한 항목만 고치는 것이 보장인데, 재생성은 전체 수정으로 부르므로 그 보장을
#: 스스로 깬다. 그래서 드러내기만 하고 고칠지는 사용자가 정한다.
#:
#: `BUDGET`을 재사용하지 않는 이유: 예산을 쓰지도 않았는데 다 썼다고 적는 것이 된다.
CHECKED_ONLY = "checked_only"
#: 위반이 남아 있는 상태들. 원인은 다르지만 결과는 같다 — 남아 있는 findings가 결함의
#: 전부라고 말할 수 없다.
UNRESOLVED = (BUDGET, NO_IMPROVEMENT, ERROR, CHECKED_ONLY)


def repair_budget() -> int:
    """재생성을 몇 번까지 시도하는가. 0이면 검사만 하고 고치지 않는다."""
    from app.core.config import settings
    return max(0, settings.design_max_repair_iters)


def repair_directive(findings: list[Finding]) -> str:
    """위반 목록을 재생성 지시문으로. 규칙 꼬리표를 그대로 실어 보낸다.

    꼬리표가 붙는 이유는 근거가 함께 가야 해서다 — 짐작인 규칙은 "우리 판단"까지 달고
    간다(`knowledge/rules.py`의 `Rule.tag`). 모델에게 "왜 그것이 결함인지"를 숨기고
    고치라고 하면, 규칙을 지키는 대신 지적 문구를 회피하는 쪽으로 고친다.
    """
    listed = "\n".join(f"{i}. {f.as_issue()}" for i, f in enumerate(findings, 1))
    return (
        "[YOUR PREVIOUS OUTPUT FAILED THESE CHECKS]\n"
        f"{listed}\n\n"
        "Fix every one of them. Keep everything that was already correct — the same "
        "classes, the same fields and methods, the same order — and do not introduce "
        "new violations."
    )


@dataclass(frozen=True)
class DesignArtifactSpec:
    """한 산출물의 스테이지를 만드는 데 필요한 전부.

    산출물마다 다른 것은 (상태 키 넷, 함수 넷)뿐이고 그래프 골격은 같다. 그래서 새
    산출물을 추가하는 일은 스펙 하나를 적는 일이 된다.
    """

    stage: str
    #: 진실의 원천 — LLM이 편집하고 저장소가 저장하는 구조화 모델.
    model_key: str
    #: 모델에서 렌더된 산출물(PlantUML 문자열 또는 OpenAPI dict).
    content_key: str
    valid_key: str
    errors_key: str
    #: 사용자 피드백을 이 스테이지로 실어 나르는 전이(transient) 상태 키.
    feedback_key: str
    #: 렌더 결과의 "아직 없음" 값 — PlantUML은 "", JSON은 {}.
    empty: Any
    #: 상태 → 모델. 앞선 산출물을 재료로 쓰므로 개별 인자가 아니라 상태를 통째로 받는다.
    extract: Callable[[ArchitectureState], Any]
    #: (현재 모델, 피드백, 상태, 대상) → 수정된 모델.
    #: 대상이 비면 전체 수정, 있으면 그 항목만 고치라고 프롬프트에 적는다. 다만 지시는
    #: 지시일 뿐이라 **믿지 않는다** — 실제 보장은 merge_model 이 한다.
    revise: Callable[[Any, str, ArchitectureState, set[str]], Any]
    #: 모델 → 산출물. 결정론적이어야 한다.
    render: Callable[[Any], Any]
    #: 산출물 → {syntax_valid, syntax_errors}.
    validate: Callable[[Any], dict[str, Any]]
    #: 모델 안에서 개별 항목을 가리키는 법: {목록 필드 이름: 그 항목의 이름을 뽑는 함수}.
    #: 여기서 나오는 이름은 **추적표의 element 와 같아야 한다** — 그래야 "api_spec:Order 를
    #: 고쳐줘"가 모델의 어느 항목인지 통한다. 비어 있으면 지목 수정을 지원하지 않는다.
    elements: dict[str, Callable[[dict], str]] = field(default_factory=dict)
    #: (모델, 상태) → 규칙 위반 목록. 렌더 **전에**, 모델에 대해 돈다.
    #: 상태를 받는 이유는 그라운딩 검사(지어낸 유스케이스 id 등)가 상류 산출물을 봐야
    #: 해서다 — 모델만으로는 "지어낸 참조"와 "정당한 참조"를 구별할 수 없다.
    check: Callable[[dict, ArchitectureState], list[Finding]] = lambda model, state: []
    #: 검사 결과를 담는 상태 키. **비어 있으면 검사 노드 자체가 안 생긴다** —
    #: 그 산출물에는 아직 규칙이 없다는 뜻이고, 없는 것을 있는 척하지 않는다.
    #: 값은 dict: {findings: list[str], repair_iters: int, stopped: str, error?: str}.
    check_key: str = ""
    #: 모델이 만들어진 뒤, 검사 전에 **다른 산출물과 대사**하는 후크. 그래프에서
    #: extract/revise 노드와 check/render 사이에 선택적으로 끼워진다. 시퀀스 다이어그램이
    #: 이것으로 클래스 다이어그램에 빠진 메서드를 보강한다.
    #: None이면 대사 노드가 생기지 않는다 — 그 산출물은 다른 것을 고칠 일이 없다는 뜻이고,
    #: 그래프에 빈 노드가 뜨지 않는다.
    reconcile: Callable[[ArchitectureState], dict] | None = None
    #: 검사·수리가 모델을 바꾼 뒤 렌더 직전에 다시 강제할 산출물 불변식.
    finalize: Callable[[ArchitectureState], dict] | None = None


def merge_targeted(
    original: list[dict],
    revised: list[dict],
    key_of: Callable[[dict], str],
    targets: set[str],
) -> list[dict]:
    """대상 항목만 revised 를 쓰고, 나머지는 original 을 **그대로** 둔다.

    바뀔 수 있는 것은 딱 하나다: **대상 항목의 내용.** 목록의 길이도, 순서도, 다른
    항목의 글자 하나도 안 바뀐다. 비대상에 대해서는 LLM 출력을 아예 읽지 않으므로
    "지시를 어기고 다른 데를 고쳤다"가 **구성에 의해 불가능**하다 — 검사해서 막는 게
    아니라 손댈 수 없게 만든다. sanitize(services/*/plantuml.py)와 같은 철학이다.

    **추가를 안 받는 이유.** 이름 변경은 (삭제 + 추가)처럼 보인다. 삭제 쪽은 보호되니
    추가만 통과하면 결국 **원본과 개명본이 둘 다 남는다.** "Order 에 배송지를 넣어줘"가
    Address 스키마를 부르는 정당한 경우도 있지만, 그건 그 스키마를 지목해 따로 요청하면
    된다 — 조용히 늘어나는 것보다 낫다.

    **빠뜨린 것을 삭제로 안 보는 이유.** 대상이 revised 에 없을 때 그것이 "지우라는 뜻"
    인지 "LLM 이 빠뜨린 것"인지 구별할 수 없다. 빠뜨림이 훨씬 흔하므로 원본을 지킨다.
    삭제가 필요하면 그건 다른 조작이어야 한다.
    """
    revised_by_key = {key_of(item): item for item in revised}
    return [
        revised_by_key[key_of(item)]
        if key_of(item) in targets and key_of(item) in revised_by_key
        else item
        for item in original
    ]


def merge_model(
    spec: DesignArtifactSpec,
    original: dict,
    revised: dict,
    targets: set[str],
) -> dict:
    """스펙이 아는 목록마다 merge_targeted 를 적용한다.

    스펙이 `elements` 를 안 갖고 있거나 대상이 비면 revised 를 그대로 쓴다 — 전체 수정
    (기존 피드백 게이트 경로)이라 좁힐 것이 없다는 뜻이다.

    **바탕은 original 이다.** revised 에서 가져오는 것은 스펙이 아는 목록의, 대상 항목뿐.
    api_spec 의 title/version 처럼 스펙이 모르는 필드는 원본을 지킨다 — 지목 대상이
    아닌데 LLM 이 바꿔놓는 자리이기 때문이다(실제로 그랬다).
    """
    if not spec.elements or not targets:
        return revised

    merged = dict(original)
    for list_field, key_of in spec.elements.items():
        merged[list_field] = merge_targeted(
            original.get(list_field) or [],
            revised.get(list_field) or [],
            key_of,
            targets,
        )
    return merged


def model_elements(spec: DesignArtifactSpec, model: dict) -> list[str]:
    """이 모델 안에서 지목할 수 있는 항목 이름들."""
    return [
        key_of(item)
        for list_field, key_of in spec.elements.items()
        for item in (model.get(list_field) or [])
        if key_of(item)
    ]


def extract_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """앞선 산출물에서 이 산출물의 구조화 모델을 도출한다."""

    def node(state: ArchitectureState) -> dict:
        return {spec.model_key: spec.extract(state)}

    return node


def revise_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """사용자 피드백을 모델(진실의 원천)에 적용한다.

    산출물은 convert 노드가 같은 변환으로 재렌더하므로, 피드백이 렌더된 텍스트를 직접
    건드리는 일이 없고 모델과 산출물이 어긋나지 않는다.
    """

    def node(state: ArchitectureState) -> dict:
        # 게이트 피드백은 산출물 전체를 대상으로 한다 — 사용자가 그 산출물을 보면서
        # 말하는 자리라 항목을 좁힐 근거가 없다. 항목을 지목하는 수정은 cascade 가 한다.
        revised = spec.revise(
            state.get(spec.model_key) or {},
            state.get(spec.feedback_key, ""),
            state,
            set(),
        )
        return {spec.model_key: revised}

    return node


def _is_degenerate(spec: DesignArtifactSpec, original: dict, candidate: dict) -> bool:
    """재생성본이 **산출물을 비워서** 위반을 없앤 것인가.

    "위반이 줄었으면 채택"은 그것만으로 불완전하다. **빈 모델은 거의 모든 검사를
    통과하기 때문이다** — 검사할 것이 없으니 위반도 없다. 그래서 재생성이 클래스를 전부
    날려 버리면 위반 수는 0으로 떨어지고, 루프는 그것을 성공으로 채택하며, 상태에는
    `stopped="clean"`이 적힌다. **산출물을 통째로 잃고도 깨끗하다고 보고하게 된다.**

    검출기 중 커버리지 검사가 이것을 우연히 막아 주기는 한다 — 상류 유스케이스를 아무도
    안 가리키게 되므로 위반이 도로 생긴다. 그러나 **입력에 유스케이스 id가 없으면 그
    검사가 아예 안 돌고**(대조할 상류가 없다), 그때 이 함정이 그대로 열린다.

    그래서 위반 수와 별개로 본다: 원래 항목이 있었는데 재생성본에 하나도 없으면 그것은
    수리가 아니다. 항목을 세는 법은 `model_elements`가 이미 안다(스펙의 `elements`).
    """
    if not spec.elements:
        return False
    return bool(model_elements(spec, original)) and not model_elements(spec, candidate or {})


def check_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """모델이 규칙을 지켰는지 판정하고, 어겼으면 **유계로** 재생성한다.

    루프가 그래프 노드가 아니라 함수 안에 있는 이유: 이 저장소는 "라우팅은 전부 정적,
    그래프 그림이 곧 실제 흐름"을 원칙으로 한다(`nodes/gates.py`). 반복 엣지를 넣으면
    그림과 흐름이 갈라진다. 반복은 스테이지 **내부의 세부**이므로 여기 있는 것이 맞다.

    **수용 조건은 "위반 수가 줄어야 한다"이다.** 안 줄면 재생성본을 버리고 직전본을
    지킨다. 이유가 둘이다. (1) 재생성이 다른 데를 망가뜨리면서 지적 하나를 고치는 일이
    실제로 있다. (2) 이 조건이 **종료를 보장한다** — 위반 수는 자연수이고 매 회 반드시
    줄어야 하므로 무한 루프가 원리상 불가능하다. 예전 문법 수리 루프에는 없던 성질이다.

    **남은 위반을 숨기지 않는다.** 예산을 다 써도 고쳐지지 않은 것은 그대로 상태에 실려
    게이트에서 사람에게 간다. `stopped`가 왜 멈췄는지를 들고 가므로, 화면은 "위반 0건"과
    "예산이 끝났다"를 구별할 수 있다.
    """

    def node(state: ArchitectureState) -> dict:
        model = state.get(spec.model_key) or {}
        findings = spec.check(model, state)
        iterations = 0
        error: str | None = None
        # 루프를 한 번도 안 돌 수 있다(위반이 없거나 예산이 0). 그때의 답을 먼저 적어 둔다.
        stopped = CLEAN if not findings else BUDGET

        for _ in range(repair_budget()):
            if not findings:
                break
            iterations += 1
            try:
                # 전체 수정(targets=set())이다. 위반이 여러 클래스에 걸칠 수 있고,
                # merge_model 은 targets 가 비면 revised 를 그대로 쓴다.
                candidate = spec.revise(model, repair_directive(findings), state, set())
            except Exception as exc:  # noqa: BLE001 - 검증 실패가 스테이지를 죽이면 안 된다
                error = f"{type(exc).__name__}: {exc}"
                stopped = ERROR
                break
            candidate_findings = spec.check(candidate or {}, state)
            if _is_degenerate(spec, model, candidate):
                stopped = NO_IMPROVEMENT
                break
            if len(candidate_findings) >= len(findings):
                # 재생성본을 **버린다.** 지적 하나를 고치면서 다른 데를 망가뜨린 것을
                # 채택하면 다음 회차의 기준선이 더 나빠진다.
                stopped = NO_IMPROVEMENT
                break
            model, findings = candidate, candidate_findings
            stopped = CLEAN if not findings else BUDGET

        report: dict[str, Any] = {
            "findings": [f.as_issue() for f in findings],
            "repair_iters": iterations,
            "stopped": stopped,
        }
        if error:
            report["error"] = error
        return {spec.model_key: model, spec.check_key: report}

    return node


def render_and_validate(spec: DesignArtifactSpec, model: dict) -> dict[str, Any]:
    """모델 → `{content_key, valid_key, errors_key}`. **렌더와 그 자기검사는 한 벌이다.**

    따로 부를 일이 없어서 한 함수다. 문법 검증은 렌더러가 제 일을 했는지 확인하려고만
    존재하므로, 렌더 없이 검증하거나 검증 없이 렌더하는 것은 둘 다 뜻이 없다.

    순수 함수인 이유는 부르는 곳이 둘이라서다 — 그래프의 `render_node`와 지목 수정
    (`cascade.py`). 예전에는 이 네 줄이 세 곳에 흩어져 있었다.
    """
    content = spec.render(model or {})
    validation = spec.validate(content)
    return {
        spec.content_key: content,
        spec.valid_key: validation["syntax_valid"],
        spec.errors_key: validation["syntax_errors"],
    }


def render_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """모델을 산출물로 렌더하고, **그 산출물이 성한지 스스로 확인한다.**

    예전에는 노드가 둘이었다(`convert` → `validate`). 나눠 둔 값이 없었다 — 검증 노드는
    변환 노드의 출력만 보고, 변환이 sanitize로 구성에 의해 유효한 산출물을 내므로
    **원리상 실패할 수 없었다.** 그래서 그래프에 "절대 울리지 않는 노드"가 다섯 개
    떠 있었고, 그림이 실제로 일어나는 일보다 커 보였다.

    합친 지금은 노드가 자기 출력을 자기가 검사한다. 한 가지 책임이다.

    **여기서 실패하면 그것은 LLM이 아니라 우리 변환기의 결함이다.** 그래서 재생성하지
    않는다 — LLM은 우리 렌더러를 고칠 수 없고, 고치라고 시키면 예전 문법 수리 루프가
    그대로 돌아온다(고칠 수 없는 지적에 예산을 태우고 종료 조건이 없다). 값은 상태에
    기록되어 사람에게 간다.

    ## 규칙 검사(`check_node`)와 왜 합치지 않는가

    보는 것도 실패의 뜻도 다르다:

      - `check`  — **렌더 전, 모델**을 본다. 실패 = LLM이 틀렸다 → 재생성이 옳은 대응.
      - `render` — **렌더 후, 산출물**을 본다. 실패 = 우리 코드가 깨졌다 → 재생성은
        정확히 틀린 대응.

    한 목록·한 루프로 합치면 그 둘이 뭉개진다. 시점도 어긋난다 — `check`는 convert
    앞이어야 하고(sanitize가 이름을 뭉개기 전에 봐야 한다), `render`는 그 뒤다.
    """

    def node(state: ArchitectureState) -> dict:
        return render_and_validate(spec, state.get(spec.model_key) or {})

    return node
