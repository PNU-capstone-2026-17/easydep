"""단계 목록이 실제 배선과 맞는지 고정한다.

**2026-07-27 이전에는 배선이 두 벌 더 있었다**: `subgraphs.py`의 그래프 엣지와
`runner.run_pipeline`의 배치 실행 순서. 사람이 읽을 코드로 남기는 편이 낫다고 보고 사본을
둔 채, 이 파일이 사본과 목록을 대조하는 방식으로 지켰다 — 배치 쪽은 `inspect.getsource`로
**소스 텍스트를 검사**했다.

둘 다 목록에서 파생하도록 바꿨으므로 대조할 사본이 없다. 그래서 이 파일이 지키는 것도
"두 벌이 같은가"에서 **"파생된 배선이 실제로 그 순서로 도는가"**로 바뀌었다. 소스 텍스트가
아니라 동작을 본다 — 텍스트 검사는 호출을 리팩터링하는 순간 거짓 실패를 내고, 반대로
호출 순서가 바뀌어도 텍스트만 맞으면 통과한다.
"""
import pytest

from app.requirements import stage_registry as stages
from app.requirements.common.state_contract import StateContract, state_contract_of
from app.requirements.orchestration import graph as orchestration_graph
from app.requirements.orchestration import runner, subgraphs


def test_pipeline_group_batch_and_key_order_is_exact() -> None:
    """graph·batch·cascade가 공유하는 단일 순서 계약을 고정한다."""

    nodes = (
        "expand_requirements",
        "intake",
        "clarify",
        "classify",
        "analyze_cloud_inputs",
        "build_resource_spec",
        "identify_actors",
        "identify_use_cases",
        "review_model",
        "check_coverage",
        "generate_specs",
        "check_specs",
        "identify_relationships",
        "check_relationships",
        "render_diagram",
    )
    assert tuple(stage.node for stage in stages.PIPELINE) == nodes
    assert stages.GROUPS == (
        "refine_requirements",
        "analyze_cloud_inputs",
        "structure_constraints",
        "model_use_cases",
        "write_specifications",
        "draw_diagram",
    )
    assert tuple(stage.node for stage in stages.batch_order()) == nodes[4:]
    assert stages.cascade_order() == (
        "actors",
        "use_cases",
        "coverage",
        "specs",
        "relationships",
        "diagram",
    )
    assert stages.node_by_key() == {
        "actors": "identify_actors",
        "use_cases": "identify_use_cases",
        "coverage": "check_coverage",
        "specs": "generate_specs",
        "relationships": "identify_relationships",
        "diagram": "render_diagram",
    }
    assert stages.editable_keys() == (
        "actors",
        "use_cases",
        "specs",
        "relationships",
    )


def test_stage_contract_public_shape_is_attached_to_every_registered_stage() -> None:
    """단계 registry가 실행 함수의 공개 입·출력 계약을 그대로 노출한다."""

    assert tuple(StateContract.__dataclass_fields__) == (
        "stage",
        "requires",
        "requires_any",
        "produces",
    )
    for stage in stages.PIPELINE:
        assert isinstance(stage.contract, StateContract)
        assert stage.contract is state_contract_of(stage.fn)


def _linear_order(compiled) -> list[str]:
    """컴파일된 서브그래프의 엣지를 따라가 노드 실행 순서를 복원한다."""
    graph = compiled.get_graph()
    nxt = {e.source: e.target for e in graph.edges}
    order, node = [], nxt.get("__start__")
    while node and node != "__end__":
        order.append(node)
        node = nxt.get(node)
    return order


@pytest.mark.parametrize("group", stages.GROUPS)
def test_subgraph_edges_follow_the_stage_registry(group):
    assert _linear_order(subgraphs.build_stage(group)) == list(stages.nodes_in(group))


def test_top_graph_groups_match_the_registry():
    assert tuple(subgraphs.build_stage_subgraphs()) == stages.GROUPS


def test_batch_runner_actually_runs_the_registry_order(monkeypatch):
    """배치 러너는 그래프를 우회해 함수를 직접 부른다 — **실제로** 부르는 순서를 본다.

    예전에는 `run_pipeline`의 소스에 `<이름>(st)`가 있는지 문자열로 확인했다. 그 검사는
    호출을 루프로 바꾸면 거짓으로 실패하고(사본이 없어졌을 뿐인데), 순서를 뒤집어도
    텍스트만 맞으면 통과한다. 스텁을 끼워 넣고 실제 호출 순서를 기록한다.
    """
    called: list[str] = []

    def recorder(name: str):
        def stage(_state):
            called.append(name)
            return {}
        return stage

    for stage in stages.PIPELINE:
        if hasattr(runner, stage.node):
            monkeypatch.setattr(runner, stage.node, recorder(stage.node))
    runner.run_pipeline([{"id": "FR1", "text": "x", "type": "FR"}])

    assert called == [s.node for s in stages.batch_order()]


def test_batch_order_skips_exactly_the_preclassified_group():
    """배치가 step1을 건너뛴다는 사실은 **목록에 적힌 것**이지 코드 모양이 아니다."""
    skipped = {s.node for s in stages.PIPELINE} - {s.node for s in stages.batch_order()}
    assert skipped == set(stages.nodes_in(stages.PRECLASSIFIED_GROUP))
    assert skipped, "건너뛰는 단계가 하나도 없다 — 상수가 실제 그룹 이름이 맞는가"


def test_every_stage_declares_what_it_reads():
    """계약 없는 단계가 생기면 조용한 빈 산출물이 다시 가능해진다."""
    for stage in stages.PIPELINE:
        contract = stage.contract
        assert contract is not None, f"{stage.node} 에 @contract 선언이 없다"
        assert contract.requires or contract.requires_any, f"{stage.node} 계약이 비었다"


def test_every_stage_declares_what_it_produces():
    """읽는 쪽만 선언하면 배선을 정적으로 검사할 수 없다.

    "이 키를 아무도 만들지 않는다"는 오류가 **그 단계를 실제로 돌려 봐야만** 드러났다.
    산출물 선언이 있어야 아래 두 검사가 성립한다.
    """
    for stage in stages.PIPELINE:
        assert stage.contract.produces, f"{stage.node} 이 무엇을 내는지 선언하지 않았다"


def _wiring_gaps(order, inputs: set[str]) -> list[str]:
    """단계 순서를 따라가며 **채워지지 않는 입력**을 모은다."""
    available = set(inputs)
    gaps = []
    for stage in order:
        contract = stage.contract
        gaps += [f"{stage.node}: {k!r} 를 아무도 만들지 않는다"
                 for k in contract.requires if k not in available]
        if contract.requires_any and not (set(contract.requires_any) & available):
            gaps.append(f"{stage.node}: {contract.requires_any} 중 하나도 안 만들어진다")
        available |= set(contract.produces)
    return gaps


def test_graph_pipeline_wiring_is_closed():
    """그래프 경로: `raw_requirements` 하나로 시작해 끝까지 이어지는가.

    단계를 옮기거나 쪼갤 때 **돌려 보지 않고** 깨진 걸 안다. 계약이 읽는 쪽만 선언하던
    동안에는 이 검사가 아예 불가능했다.
    """
    assert _wiring_gaps(stages.PIPELINE, {"raw_requirements"}) == []


def test_batch_pipeline_wiring_is_closed():
    """배치 경로: 입력이 이미 분류돼 있으므로 `classified`로 시작한다.

    그래프와 따로 본다 — 배치는 step1을 건너뛰므로, 건너뛴 단계가 만들던 키를 하류가
    요구하면 **배치에서만** 깨진다. 평가 세트가 재는 실행이 이 배치다.
    """
    assert _wiring_gaps(stages.batch_order(), {"classified"}) == []


def test_editable_stages_are_a_subset_of_the_cascade_order():
    assert set(stages.editable_keys()) <= set(stages.cascade_order())


@pytest.mark.parametrize("gated", [False, True])
def test_every_registered_group_is_actually_wired_into_the_parent_graph(gated):
    """**목록에 넣었는데 부모 그래프에 안 이은 그룹**을 잡는다.

    이 방향의 실수만 조용하다. 그룹을 지우면 `subs["..."]`가 KeyError로 크게 죽지만,
    더하면 `add_node`만 하고 `add_edge`를 빠뜨려도 컴파일이 통과하고 그 단계는 영영
    안 돈다. 그런데 배치 러너는 `batch_order()`로 파생되므로 **그 단계를 돈다** —
    서빙 경로와 평가 세트가 재는 경로가 조용히 갈린다. `stages.py`가 없애려고 만들어진
    바로 그 사고다.
    """
    compiled = orchestration_graph.build_graph(feedback_gates=gated)
    nodes = set(compiled.get_graph().nodes)
    missing = [group for group in stages.GROUPS if group not in nodes]
    assert not missing, f"부모 그래프에 안 이어진 그룹: {missing}"

    # 노드로 있기만 하면 안 된다 — 들어오는 엣지가 있어야 실제로 돈다.
    reachable = {e.target for e in compiled.get_graph().edges}
    orphans = [group for group in stages.GROUPS if group not in reachable]
    assert not orphans, f"들어오는 엣지가 없어 영영 안 도는 그룹: {orphans}"
