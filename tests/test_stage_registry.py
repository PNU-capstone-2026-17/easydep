"""단계 목록(app/requirements/agent/stages.py)이 실제 배선과 맞는지 고정한다.

파이프라인 모양은 그 파일에서만 말하기로 했는데, 아직 파생시키지 않은 곳이 둘 있다:
`subgraphs.py`의 그래프 엣지와 `runner.py`의 배치 실행 순서. 둘은 사람이 읽을 코드로
남기는 편이 낫다고 봐서 그대로 뒀고, 대신 어긋나면 여기서 깨진다.

이 테스트가 깨지면 둘 중 하나다 — 목록을 고치고 배선을 안 고쳤거나, 그 반대다.
"""
import inspect

import pytest

from app.requirements import feedback as fb
from app.requirements import runner
from app.requirements.agent import stages, subgraphs


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
    builder = getattr(subgraphs, f"build_{group}")
    assert _linear_order(builder()) == list(stages.nodes_in(group))


def test_top_graph_groups_match_the_registry():
    assert tuple(subgraphs.build_stage_subgraphs()) == stages.GROUPS


def test_batch_runner_calls_the_same_stages_in_the_same_order():
    """배치 러너는 그래프를 우회해 함수를 직접 부른다 — 순서가 같아야 같은 것을 잰다."""
    source = inspect.getsource(runner.run_pipeline)
    called = [s.node for s in stages.PIPELINE if f"{s.node}(st)" in source]
    # step1(intake/clarify/classify)은 배치 입력이 이미 분류돼 있어 건너뛴다.
    expected = [s.node for s in stages.PIPELINE if s.group != "refine_requirements"]
    assert called == expected


def test_feedback_cascade_is_derived_not_restated():
    assert fb._ORDER == list(stages.cascade_order())
    assert fb._STAGE_FN_NAME == stages.node_by_key()
    assert fb._EDITABLE == stages.editable_keys()


def test_cascade_targets_exist_as_functions_in_feedback():
    """cascade는 globals()로 함수를 찾는다 — 이름이 실제로 거기 있어야 한다."""
    for key, name in stages.node_by_key().items():
        assert name in vars(fb), f"{key} → {name} 이 feedback 모듈에 없다"


def test_every_stage_declares_what_it_reads():
    """계약 없는 단계가 생기면 조용한 빈 산출물이 다시 가능해진다."""
    for stage in stages.PIPELINE:
        contract = stage.contract
        assert contract is not None, f"{stage.node} 에 @contract 선언이 없다"
        assert contract.requires or contract.requires_any, f"{stage.node} 계약이 비었다"


def test_editable_stages_are_a_subset_of_the_cascade_order():
    assert set(stages.editable_keys()) <= set(stages.cascade_order())
