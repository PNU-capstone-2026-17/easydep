"""설계 파이프라인 그래프 — 게이트가 실제로 흐름을 지배하는가 (네트워크 불필요).

여기서 확인하는 것은 토폴로지가 아니라 **동작**이다. 노드 이름만 세는 테스트는 배선이
틀려도 통과한다. 그래서 진짜 그래프를 돌려 스테이지마다 멈추는지, 빈 피드백이 다음
스테이지로 넘기는지, 피드백이 같은 스테이지를 다시 만들어 같은 게이트로 되돌아오는지,
그리고 멈춰 있는 동안 산출물이 저장소에 들어갔는지를 본다.

LLM을 부르는 스테이지 함수는 전부 대체한다 — 그래프의 흐름을 보는 자리이지 생성 품질을
보는 자리가 아니다.
"""
from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import app.design.graphs.subgraphs as sg
from app.db.models import ORIGIN_FEEDBACK_REVISED, ORIGIN_GENERATED
from app.design.graphs import design_graph as dg
from app.design.graphs.subgraphs import DESIGN_STAGES, DESIGN_SUBGRAPHS
from app.design.knowledge import rules


#: 스테이지별 (추출 결과 모델, 수정 결과 모델). 다섯 산출물이 모두 구조화 모델을
#: 내놓으므로 스텁도 모델만 돌려주면 되고, **렌더러는 진짜가 돈다** — 그래서 이 테스트는
#: 흐름뿐 아니라 "모델이 실제로 산출물이 되는가"까지 함께 본다.
_BCE = {
    "Classes": [
        {"className": "Order", "stereotype": "Entity", "fields": ["total: int"], "methods": []}
    ],
    "Relationships": [],
}
_BCE_REVISED = {
    "Classes": [
        {"className": "OrderRevised", "stereotype": "Entity", "fields": [], "methods": []}
    ],
    "Relationships": [],
}
_SEQUENCE = {
    "Participants": [
        {"name": "Customer", "kind": "actor"},
        {"name": "OrderController", "kind": "control"},
    ],
    "Messages": [{"source": "Customer", "target": "OrderController", "label": "placeOrder()"}],
}
_API = {
    "title": "Order API",
    "version": "1.0.0",
    "Endpoints": [
        {
            "path": "/orders",
            "method": "post",
            "operation_id": "createOrder",
            "request_schema": "Order",
            "responses": [{"status": 201, "schema_name": "Order"}],
        }
    ],
    "Schemas": [{"name": "Order", "fields": [{"name": "total", "type": "integer"}]}],
}
_DEPLOYMENT = {
    "Nodes": [
        {"name": "Browser", "kind": "device"},
        {"name": "AppServer", "kind": "executionEnvironment"},
    ],
    "Artifacts": [{"name": "order-service.jar", "deployed_on": "AppServer"}],
    "Connections": [{"source": "Browser", "target": "AppServer", "protocol": "HTTPS"}],
}


@pytest.fixture
def stub_llm(monkeypatch):
    """다섯 스테이지의 LLM 추출/수정을 결정론적 모델로 대체한다.

    스펙의 람다는 호출 시점에 subgraphs 모듈에서 이름을 찾으므로 모듈 쪽만 막으면 된다.
    호출 기록을 남겨 "피드백이 정말 그 스테이지를 다시 만들었는가"를 볼 수 있게 한다.
    """
    calls: list[str] = []

    def stub(stage: str, mode: str, model):
        def call(*_args, **_kwargs):
            calls.append(f"{mode}:{stage}")
            return model

        return call

    for name, stage, mode, model in (
        ("extract_bce_classes_from_scenario", "class_diagram", "gen", _BCE),
        ("revise_bce_classes", "class_diagram", "fb", _BCE_REVISED),
        ("extract_sequence_model", "sequence_diagram", "gen", _SEQUENCE),
        ("revise_sequence_model", "sequence_diagram", "fb", _SEQUENCE),
        ("extract_api_spec_model", "api_spec", "gen", _API),
        ("revise_api_spec_model", "api_spec", "fb", _API),
        ("revise_erd_classes", "erd", "fb", _BCE),
        ("extract_deployment_model", "deployment_diagram", "gen", _DEPLOYMENT),
        ("revise_deployment_model", "deployment_diagram", "fb", _DEPLOYMENT),
    ):
        monkeypatch.setattr(sg, name, stub(stage, mode, model))

    # PlantUML 문법 검사는 java 서브프로세스를 띄운다. 여기서 볼 것은 흐름이지 java의
    # 설치 여부가 아니다.
    import app.design.services.common.validation as validation

    monkeypatch.setattr(validation, "check_plantuml_syntax", lambda _t: [])

    return calls


@pytest.fixture
def graph(monkeypatch, stub_llm):
    """체크포인터를 메모리로 바꿔 컴파일한 파이프라인. DB 없이 돈다.

    저장은 app_id가 없으면 건너뛰므로(nodes/persist.py), 여기서는 저장소도 필요 없다.
    모듈 전역 graph도 같은 것으로 바꾼다 — rewind_design·has_active_session 같은 서빙
    헬퍼가 전역을 보므로, 안 바꾸면 그 함수들이 MySQL을 찾는다.
    """
    compiled = dg.build_design_graph(MemorySaver())
    monkeypatch.setattr(dg, "graph", compiled)
    return compiled


THREAD = {"configurable": {"thread_id": "test-app"}}
SEED = {"usecase_spec": {"use_cases": [{"id": "UC1", "name": "주문"}]}}


def _stage_at_gate(result: dict) -> str | None:
    """지금 멈춰 있는 게이트의 스테이지(끝났으면 None)."""
    interrupts = result.get("__interrupt__")
    return interrupts[0].value["stage"] if interrupts else None


def test_pipeline_stops_at_every_stage_in_order(graph):
    """빈 피드백만 주면 5개 스테이지를 순서대로 하나씩 거쳐 끝난다.

    한 번의 invoke로 다 돌아버리면 안 된다 — 사용자가 각 산출물을 확인할 자리가 사라진다.
    """
    result = graph.invoke(SEED, THREAD)
    assert _stage_at_gate(result) == DESIGN_STAGES[0]

    seen = [DESIGN_STAGES[0]]
    for _ in range(len(DESIGN_STAGES) - 1):
        result = graph.invoke(Command(resume=""), THREAD)
        seen.append(_stage_at_gate(result))

    assert seen == list(DESIGN_STAGES)

    # 마지막 게이트를 통과하면 파이프라인이 끝난다.
    result = graph.invoke(Command(resume=""), THREAD)
    assert _stage_at_gate(result) is None


def test_gate_shows_the_artifact_it_is_asking_about(graph):
    """게이트 페이로드에 산출물이 실려야 화면이 저장소를 따로 조회하지 않는다."""
    result = graph.invoke(SEED, THREAD)
    payload = result["__interrupt__"][0].value

    assert payload["status"] == "need_feedback"
    assert payload["stage"] == "class_diagram"
    assert "class Order" in payload["artifact"]
    assert payload["valid"] is True


def test_feedback_regenerates_the_same_stage_and_asks_again(graph, stub_llm):
    """피드백은 다음으로 넘어가지 않는다 — 그 스테이지를 다시 만들고 같은 게이트로 돌아온다."""
    graph.invoke(SEED, THREAD)
    stub_llm.clear()

    result = graph.invoke(Command(resume="Order에 상태 필드를 추가해줘"), THREAD)

    assert _stage_at_gate(result) == "class_diagram"   # 여전히 같은 자리
    assert "fb:class_diagram" in stub_llm              # 피드백 경로가 실제로 돌았다
    assert "gen:sequence_diagram" not in stub_llm      # 다음 스테이지는 시작조차 안 했다


def test_advancing_after_feedback_moves_on(graph):
    """피드백 루프에 갇히지 않는다 — 만족하면 빈 피드백으로 다음 스테이지로 간다."""
    graph.invoke(SEED, THREAD)
    graph.invoke(Command(resume="한 번 고쳐줘"), THREAD)
    result = graph.invoke(Command(resume=""), THREAD)

    assert _stage_at_gate(result) == "sequence_diagram"


def test_each_stage_is_persisted_when_it_completes(monkeypatch, graph):
    """게이트에서 멈춰 있는 동안에도 산출물이 저장소에 있어야 한다.

    마지막에 몰아서 저장하면 중간에 멈춰 있는 내내 GET /api/apps/{id}가 빈 값을 준다.
    피드백 반복도 버전으로 남아야 이력이 의미를 갖는다.
    """
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.repositories.artifact_repository.save_stage",
        lambda app_id, stage, state, origin=None: saved.append((stage, origin)),
    )

    graph.invoke({**SEED, "app_id": "test-app"}, THREAD)
    assert saved == [("class_diagram", ORIGIN_GENERATED)]

    graph.invoke(Command(resume="고쳐줘"), THREAD)
    # 피드백으로 만든 판도 새 버전으로 남고, 생성이 아니라 수정으로 표시된다.
    assert saved[-1] == ("class_diagram", ORIGIN_FEEDBACK_REVISED)

    graph.invoke(Command(resume=""), THREAD)
    assert saved[-1] == ("sequence_diagram", ORIGIN_GENERATED)


def test_every_stage_has_the_same_skeleton():
    """다섯 산출물이 하나의 골격을 공유한다 — **문법** 수리 루프가 붙은 예외가 없다.

    예전에는 시퀀스·API·배포만 validate→repair 루프를 달고 있었다. 그 루프는 LLM이
    산출물 텍스트를 직접 쓸 때만 필요한 것이고, 지금은 다섯 모두 구조화 모델에서
    결정론적으로 렌더되므로 있어서는 안 된다.

    **2026-08-04에 이 테스트가 지키는 것을 좁혔다.** 예전에는 "repair"라는 이름의 노드가
    없다는 것만 봤는데, 그건 "어떤 반복도 없다"로 읽히기 쉬웠다. 지키려던 것은 그것이
    아니라 **문법 오류를 되먹여 텍스트를 다시 쓰게 하는 루프가 없다**는 것이다. 반복
    엣지가 없다는 것은 `test_no_stage_subgraph_can_loop`이 따로 본다.

    같은 날 `convert`와 `validate`도 `render` 하나로 합쳤다. 문법 검증이 변환의 출력만
    보고 원리상 실패할 수 없어서 나눠 둘 값이 없었다. 옛 이름 둘이 되살아나지 않는지도
    여기서 함께 고정한다.

    의미 검사 노드(`check_{stage}`)는 이것들과 다른 것이다 — 문법이 아니라 모델의 내용을
    보고, 반복은 노드가 아니라 함수 안에 있어 토폴로지가 정적으로 남는다
    (`nodes/artifact.py`의 `check_node`).
    """
    for stage in DESIGN_STAGES:
        generate = set(DESIGN_SUBGRAPHS[stage]["generate"].get_graph().nodes)
        feedback = set(DESIGN_SUBGRAPHS[stage]["feedback"].get_graph().nodes)

        assert {f"extract_{stage}", f"render_{stage}"} <= generate
        assert {f"revise_{stage}", f"render_{stage}"} <= feedback
        # 합쳐진 뒤 남으면 안 되는 이름 — 예전 두 노드가 되살아나는 것을 막는다.
        assert not {f"convert_{stage}", f"validate_{stage}"} & (generate | feedback)
        assert not any("repair" in node for node in generate | feedback)


def test_no_stage_subgraph_can_loop():
    """스테이지 서브그래프의 토폴로지에 반복 엣지가 없다.

    문법 수리 루프를 없앤 이유 중 하나가 **종료 조건이 없다**는 것이었다. 의미 검사를
    더하면서 그 성질을 되살리지 않았다는 것을 여기서 고정한다 — 반복은 `check_node`
    함수 안에 있고 예산으로 유계이며, 그래프는 여전히 한 방향으로만 흐른다.

    이게 곧 이 저장소의 원칙이기도 하다: 그래프 그림이 곧 실제 흐름이다.
    """
    for stage in DESIGN_STAGES:
        for kind in ("generate", "feedback"):
            graph = DESIGN_SUBGRAPHS[stage][kind].get_graph()
            outgoing: dict[str, list[str]] = {}
            for edge in graph.edges:
                outgoing.setdefault(edge.source, []).append(edge.target)

            seen: set[str] = set()
            stack = ["__start__"]
            while stack:
                node = stack.pop()
                assert node not in seen, f"{stage}/{kind}: {node}를 다시 지난다(반복 엣지)"
                seen.add(node)
                stack.extend(outgoing.get(node, []))


def test_only_stages_with_rules_are_semantically_checked():
    """의미 검사 노드는 규칙이 있는 스테이지에만 생긴다.

    빈 검사 노드를 다섯 곳에 달면 그래프 그림이 "다 검사한다"고 거짓말을 한다. 지금
    규칙 지식베이스가 있는 것은 클래스 다이어그램과 ERD 둘이고, 그 사실이 토폴로지에
    그대로 보여야 한다 — 나머지 셋에 규칙을 채우면 이 테스트를 함께 고친다.

    **규칙 목록에서 기대값을 뽑는다.** 스테이지 이름을 손으로 적어 두면, 규칙을 추가하고
    배선을 잊었을 때 테스트가 그 사실을 못 잡는다(둘 다 손으로 고쳐야 하므로).
    """
    checked = {
        stage
        for stage in DESIGN_STAGES
        if f"check_{stage}" in DESIGN_SUBGRAPHS[stage]["generate"].get_graph().nodes
    }
    with_rules = {r.stage for r in rules.RULES if r.severity == rules.DEFECT}

    assert checked == with_rules == {"class_diagram", "erd", "sequence_diagram", "api_spec"}

    # 생성과 피드백 **양쪽**에 있어야 한다. 피드백에 없으면 사용자 피드백으로 만든 판은
    # 아무도 검사하지 않은 채 저장된다.
    assert "check_class_diagram" in DESIGN_SUBGRAPHS["class_diagram"]["feedback"].get_graph().nodes
    assert "check_sequence_diagram" in DESIGN_SUBGRAPHS["sequence_diagram"]["feedback"].get_graph().nodes
    assert "check_api_spec" in DESIGN_SUBGRAPHS["api_spec"]["feedback"].get_graph().nodes
    for stage in checked:
        feedback = DESIGN_SUBGRAPHS[stage]["feedback"].get_graph().nodes
        assert f"check_{stage}" in feedback, stage


def test_rendering_is_deterministic_and_valid_by_construction(graph):
    """모델 → 산출물 변환이 결정론적이고, 렌더 결과가 검증을 통과한다.

    이 골격의 값어치가 여기 있다: 같은 모델은 같은 산출물을 내고, 그 산출물은 구성에
    의해 유효하므로 수리할 일이 없다.
    """
    first = graph.invoke(SEED, THREAD)
    for _ in range(len(DESIGN_STAGES)):
        first = graph.invoke(Command(resume=""), THREAD)

    # 같은 스텁 모델로 다시 돌리면 산출물이 글자 그대로 같아야 한다.
    second_thread = {"configurable": {"thread_id": "test-app-2"}}
    second = graph.invoke(SEED, second_thread)
    for _ in range(len(DESIGN_STAGES)):
        second = graph.invoke(Command(resume=""), second_thread)

    for stage in DESIGN_STAGES:
        spec = sg.DESIGN_SPECS[stage]
        assert first[spec.content_key] == second[spec.content_key], stage
        assert first[spec.valid_key] is True, stage
        assert first[spec.errors_key] == [], stage

    # 산출물이 실제로 모델에서 나왔는지 — 스텁 모델의 내용이 렌더 결과에 보여야 한다.
    assert "class Order" in first["class_diagram_puml"]
    assert "placeOrder()" in first["sequence_diagram_puml"]
    assert first["api_spec"]["openapi"].startswith("3.1")
    assert "/orders" in first["api_spec"]["paths"]
    assert "Order" in first["erd_puml"]
    assert "order-service.jar" in first["deployment_diagram_puml"]


def test_no_session_is_distinguishable_from_a_paused_one(graph):
    """세션이 없는 것과 게이트에서 멈춰 있는 것을 구별할 수 있어야 한다.

    이 구별이 없으면 서빙 레이어가 resume을 막을 수 없고, LangGraph는 모르는 스레드에
    대한 resume을 예외 없이 **빈 입력으로 처음부터** 돌려버린다. 유스케이스 명세도 없이
    도니 빈 산출물이 만들어지고 그게 저장까지 된다.
    """
    assert dg.session_status("한번도-시작한적-없는-앱") == {
        "exists": False, "active": False, "stage": None
    }

    graph.invoke(SEED, THREAD)
    assert dg.session_status("test-app") == {
        "exists": True, "active": True, "stage": "class_diagram"
    }

    for _ in range(len(DESIGN_STAGES)):
        graph.invoke(Command(resume=""), THREAD)

    # 끝까지 갔으면 재개할 곳은 없지만 되감을 실행은 있다. 이 구별이 중요하다 —
    # 되감기가 가장 필요한 시점이 "다 만들고 나서"다.
    assert dg.has_active_session("test-app") is False
    assert dg.has_design_run("test-app") is True


def test_rewind_remakes_that_stage_and_everything_after(graph, stub_llm):
    """되감기는 그 스테이지만 다시 만들지 않는다 — 뒤쪽도 새 재료로 다시 만들어진다.

    그게 요점이다. API 명세를 바꿨는데 그것을 재료로 만든 배포 다이어그램이 옛 API
    기준으로 남으면 두 산출물이 어긋난다. 예전 per-stage 경로가 딱 그걸 만들 수 있었다.
    """
    graph.invoke(SEED, THREAD)
    for _ in range(len(DESIGN_STAGES)):
        graph.invoke(Command(resume=""), THREAD)
    stub_llm.clear()

    result = dg.rewind_design("test-app", "api_spec")

    assert result["stage"] == "api_spec"          # 그 스테이지 게이트에서 멈췄다
    assert "gen:api_spec" in stub_llm             # 다시 만들었다
    assert "gen:deployment_diagram" not in stub_llm   # 아직 뒤로 안 갔다

    # 이어서 진행하면 뒤쪽이 새 재료로 다시 만들어진다.
    stub_llm.clear()
    dg.resume_design("test-app", "")              # erd 로
    dg.resume_design("test-app", "")              # deployment 로
    assert "gen:deployment_diagram" in stub_llm


def test_rewinding_to_the_first_stage_is_refused(graph):
    """첫 스테이지로 되감기는 start_design 이 하는 일이다 — 두 갈래를 만들지 않는다."""
    graph.invoke(SEED, THREAD)
    with pytest.raises(ValueError, match="first stage"):
        dg.rewind_design("test-app", DESIGN_STAGES[0])


def test_rewinding_to_a_stage_not_reached_yet_is_refused(graph):
    """아직 안 만든 단계로 "되감으면" 실제로는 한 걸음 전진한다 — 조용히 그러면 안 된다.

    부르는 쪽은 되감는다고 믿고 있다. 믿음과 다른 일을 하느니 거절한다.
    """
    graph.invoke(SEED, THREAD)          # 클래스 게이트에서 멈춤
    with pytest.raises(dg.StageNotReached, match="has not been produced yet"):
        dg.rewind_design("test-app", "deployment_diagram")

    # 이미 만든 단계로는 물론 된다.
    for _ in range(len(DESIGN_STAGES)):
        graph.invoke(Command(resume=""), THREAD)
    assert dg.rewind_design("test-app", "deployment_diagram")["stage"] == "deployment_diagram"


def test_serving_import_chain_pulls_in_the_checkpoint_tables():
    """api.py를 import하면 design_* 테이블이 메타데이터에 올라간다.

    올라가지 않으면 서버는 뜨는데 init_db()가 테이블을 안 만들고, 첫 /design/start 가
    돌 때까지 아무도 모른다.
    """
    import app.design.api  # noqa: F401 - 서빙 경로의 진입점

    from app.db.models import Base

    assert {
        "design_checkpoints",
        "design_checkpoint_blobs",
        "design_checkpoint_writes",
    } <= set(Base.metadata.tables)
