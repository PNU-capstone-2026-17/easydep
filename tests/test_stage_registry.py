"""단계 목록이 실제 배선과 맞는지 고정한다.

단계 목록과 실제 LangGraph 배선이 같은 순서를 사용하는지 공개 동작으로 확인한다.
"""
import pytest

from app.requirements import stage_registry as stages
from app.requirements.common.state_contract import StateContract, state_contract_of
from app.requirements.orchestration import graph as orchestration_graph
from app.requirements.orchestration import subgraphs


def test_pipeline_group_and_key_order_is_exact() -> None:
    """graph와 feedback cascade가 공유하는 단일 순서 계약을 고정한다."""

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


def test_editable_stages_are_a_subset_of_the_cascade_order():
    assert set(stages.editable_keys()) <= set(stages.cascade_order())


@pytest.mark.parametrize("gated", [False, True])
def test_every_registered_group_is_actually_wired_into_the_parent_graph(gated):
    """**목록에 넣었는데 부모 그래프에 안 이은 그룹**을 잡는다.

    이 방향의 실수만 조용하다. 그룹을 지우면 `subs["..."]`가 KeyError로 크게 죽지만,
    더하면 `add_node`만 하고 `add_edge`를 빠뜨려도 컴파일이 통과하고 그 단계는 영영
    안 돈다.
    """
    compiled = orchestration_graph.build_graph(feedback_gates=gated)
    nodes = set(compiled.get_graph().nodes)
    missing = [group for group in stages.GROUPS if group not in nodes]
    assert not missing, f"부모 그래프에 안 이어진 그룹: {missing}"

    # 노드로 있기만 하면 안 된다 — 들어오는 엣지가 있어야 실제로 돈다.
    reachable = {e.target for e in compiled.get_graph().edges}
    orphans = [group for group in stages.GROUPS if group not in reachable]
    assert not orphans, f"들어오는 엣지가 없어 영영 안 도는 그룹: {orphans}"
