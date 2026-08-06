"""파이프라인 단계 목록 — 이 에이전트의 모양을 적어 두는 한 곳.

**왜 있나.** 파이프라인 순서가 세 군데에 따로 적혀 있었다:
  - `subgraphs.py`  — 그래프 엣지
  - `runner.py`     — 배치 실행 순서
  - `feedback.py`   — cascade 순서(게다가 논리 이름 체계가 달랐다)

구조를 바꾸려면 세 곳을 다 고쳐야 하고, 하나를 빠뜨려도 아무 표시가 없다. 그래서
"무슨 단계가 어떤 순서로 있는가"는 여기서만 말한다.

**2026-07-27부터 사본이 없다.** 한동안 두 곳(`subgraphs.py`의 그래프 엣지,
`runner.run_pipeline`의 배치 실행 순서)은 사람이 읽을 코드로 남기고 테스트가 이 목록과
대조하는 방식으로 지켰는데, 대조는 사본을 없애 주지 않는다. 특히 배치 쪽은 테스트가
`inspect.getsource`로 **소스 텍스트**를 검사하고 있어서, 호출을 리팩터링하면 거짓 실패를
내고 순서를 바꿔도 텍스트만 맞으면 통과했다. 지금은 둘 다 여기서 파생된다:

  - `nodes_in(group)`  → `subgraphs.build_stage`가 노드와 엣지를 만든다
  - `batch_order()`    → `runner._run_stages`가 정방향 패스를 돈다
  - `cascade_order()` · `node_by_key()` · `editable_keys()` → `feedback.py`

파생된 배선이 **실제로 그 순서로 도는지**는 `tests/test_stage_registry.py`가 확인한다.

**입력 계약은 여기 없다.** 그건 단계 함수의 `@contract(...)` 선언에 있고, 이 목록은
`contract_of()`로 읽기만 한다 — 같은 사실을 두 번 적지 않기 위해서다.

**교체를 염두에 둔다.** 언젠가 LangGraph가 아닌 것으로 옮기거나 단계를 다시 나눌 수
있다. 그때 갈아엎는 대상은 이 파일이고, 단계 함수들(`steps/`)은 `state -> dict` 라는
모양 그대로 따라간다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.requirements.agent.steps.step1_requirements import clarify, classify, intake
from app.requirements.agent.steps.step2_usecases import (
    check_coverage,
    identify_actors,
    identify_use_cases,
    review_model,
)
from app.requirements.agent.steps.step3_specifications import check_specs, generate_specs
from app.requirements.agent.steps.step4_diagram import (
    check_relationships,
    identify_relationships,
    render_diagram,
)
from app.requirements.agent.steps.step_cloud import derive_deployment_needs
from app.requirements.agent.steps.step_resource import build_resource_spec
from app.requirements.common.state_contract import StateContract, state_contract_of


@dataclass(frozen=True)
class Stage:
    """한 단계."""

    #: 그래프 노드 이름 = 함수 이름. 둘을 같게 두면 트레이스와 코드가 같은 말을 한다.
    node: str
    fn: Callable[..., dict]
    #: 어느 스테이지 서브그래프에 속하는가(상위 그래프의 노드 이름).
    group: str
    #: 피드백·cascade가 쓰는 논리 이름. None이면 cascade 대상이 아니다
    #: (check_* 같은 집계 노드는 상위가 다시 돌면 자연히 다시 돈다).
    key: str | None = None
    #: 사용자가 피드백으로 직접 재생성할 수 있는가.
    #: coverage·diagram은 상위에서 파생되는 결정론 산출물이라 대상이 아니다.
    editable: bool = False

    @property
    def contract(self) -> StateContract | None:
        """이 단계가 상태에서 무엇을 읽는지(함수의 `@contract` 선언에서 읽는다)."""
        return state_contract_of(self.fn)


#: 실행 순서 그대로. 이 순서가 그래프 엣지·배치 실행·cascade의 근거다.
PIPELINE: tuple[Stage, ...] = (
    Stage("intake", intake, group="refine_requirements"),
    Stage("clarify", clarify, group="refine_requirements"),
    Stage("classify", classify, group="refine_requirements"),
    # 분류된 요구사항에서 제네릭 배포 필요사항을 도출한다. 구체 리소스는 선택하지 않으며,
    # 피드백 대상이 아니라 요구사항이 바뀌면 다시 파생되는 산출물이라 key를 두지 않는다.
    Stage("derive_deployment_needs", derive_deployment_needs, group="derive_deployment_needs"),
    # 사용자가 별도로 입력한 클라우드 제약을 RESOURCE_SPEC 계약으로 옮긴다.
    # 이것도 입력에서 파생되므로 key를 두지 않는다.
    Stage("build_resource_spec", build_resource_spec, group="structure_constraints"),
    Stage("identify_actors", identify_actors, group="model_use_cases",
          key="actors", editable=True),
    Stage("identify_use_cases", identify_use_cases, group="model_use_cases",
          key="use_cases", editable=True),
    # 의미 검증은 결정론 집계(check_coverage) 앞에 둔다 — check_* 노드가 그룹의 마지막
    # 집계라는 성격을 지키고, 검증 결과는 자기 상태 키(model_review)로만 나간다.
    Stage("review_model", review_model, group="model_use_cases"),
    Stage("check_coverage", check_coverage, group="model_use_cases", key="coverage"),
    Stage("generate_specs", generate_specs, group="write_specifications",
          key="specs", editable=True),
    Stage("check_specs", check_specs, group="write_specifications"),
    Stage("identify_relationships", identify_relationships, group="draw_diagram",
          key="relationships", editable=True),
    Stage("check_relationships", check_relationships, group="draw_diagram"),
    Stage("render_diagram", render_diagram, group="draw_diagram", key="diagram"),
)

#: 상위 그래프의 노드 순서(= 스테이지 서브그래프 순서).
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(s.group for s in PIPELINE))

#: 배치 입력(`inputs/*.json`)이 **이미 만족시키는** 그룹.
#:
#: 배치는 분류가 끝난 요구사항을 받으므로 step1이 할 일이 없다. 그 사실이 지금까지
#: `runner.run_pipeline`이 step2부터 손으로 호출한다는 형태로만 있었다 — 즉 코드 모양에서
#: 읽어내야 했고, 테스트는 그 모양을 소스 텍스트로 검사했다.
PRECLASSIFIED_GROUP = "refine_requirements"


def nodes_in(group: str) -> tuple[str, ...]:
    """한 서브그래프 안의 노드 이름들(실행 순서)."""
    return tuple(s.node for s in PIPELINE if s.group == group)


def batch_order() -> tuple[Stage, ...]:
    """배치 러너가 도는 단계들 — 입력이 이미 분류돼 있어 step1을 건너뛴다.

    `runner.run_pipeline`의 정방향 패스가 여기서 파생된다. 예전에는 그 아홉 줄이 손으로
    적혀 있었고, **되돌리기(`_rerun_from`)만** 이 목록에서 파생됐다. 한쪽만 파생된 것이
    문제였다: 단계를 하나 넣으면 되돌리기는 저절로 따라오는데 정방향은 안 따라오고,
    그러면 **정방향에 없는 단계를 되돌리기가 실행하는** 상태가 조용히 만들어진다.
    """
    return tuple(s for s in PIPELINE if s.group != PRECLASSIFIED_GROUP)


def cascade_order() -> tuple[str, ...]:
    """논리 이름의 선형 순서 — 피드백 cascade가 따르는 의존 사슬."""
    return tuple(s.key for s in PIPELINE if s.key)


def node_by_key() -> dict[str, str]:
    """논리 이름 → 재실행할 함수 이름."""
    return {s.key: s.node for s in PIPELINE if s.key}


def editable_keys() -> tuple[str, ...]:
    """사용자 피드백으로 재생성할 수 있는 단계의 논리 이름."""
    return tuple(s.key for s in PIPELINE if s.editable and s.key)
