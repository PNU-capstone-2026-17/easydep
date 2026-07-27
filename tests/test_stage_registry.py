"""단계 목록(app/requirements/agent/stages.py)이 실제 배선과 맞는지 고정한다.

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
    # 되돌아가기가 끼어들면 순서에 재실행이 섞인다 — 정방향만 본다.
    monkeypatch.setattr(runner.settings, "max_redo_rounds", 0)

    runner.run_pipeline([{"id": "FR1", "text": "x", "type": "FR"}])

    assert called == [s.node for s in stages.batch_order()]


def test_batch_order_skips_exactly_the_preclassified_group():
    """배치가 step1을 건너뛴다는 사실은 **목록에 적힌 것**이지 코드 모양이 아니다."""
    skipped = {s.node for s in stages.PIPELINE} - {s.node for s in stages.batch_order()}
    assert skipped == set(stages.nodes_in(stages.PRECLASSIFIED_GROUP))
    assert skipped, "건너뛰는 단계가 하나도 없다 — 상수가 실제 그룹 이름이 맞는가"


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
