"""설계 산출물 → 배포 계획 (조인이 일어나는 곳).

여기 테스트가 지키는 것은 **추론이 사실로 승격되지 않는가**와 **부분 입력에 부분
답을 내는가**다. 아키타입 분류는 영원히 `inferred`이므로, 우리가 할 수 있는 일은
hedge를 답에 싣는 것까지다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from appkb.plan import ORIGIN_DESIGN, ORIGIN_DESIGNER, ORIGIN_INFERRED, ORIGIN_KB
from nim_agent.design_tools import _render_plan_text, compose

_EXAMPLE = Path(__file__).resolve().parent.parent / "appkb" / "examples" / "order-demo.json"


@pytest.fixture()
def design() -> dict:
    return json.loads(_EXAMPLE.read_text(encoding="utf-8"))


# --- 추론 규칙 -----------------------------------------------------------------

def test_openapi_component_becomes_a_service_and_says_so(design) -> None:
    plan = compose(design)
    node = plan.node("order-api")
    assert node.role == "compute" and node.origin == ORIGIN_INFERRED
    assert any("OpenAPI" in n.text for n in node.notes)


def test_async_receiver_becomes_a_worker(design) -> None:
    node = compose(design).node("order-worker")
    assert any("비동기" in n.text for n in node.notes)


def test_entity_owner_gets_a_store_via_svcmap(design) -> None:
    """**앱 계층과 인프라 계층이 만나는 지점.** ER 소유 → app::relationalDatabase
    → svcmap → 벤더 타입."""
    node = compose(design).node("order-api-db")
    assert node.archetype == "app::relationalDatabase"
    assert node.type_id.startswith("aws::")
    assert any(n.origin == ORIGIN_KB and "svcmap" in n.source for n in node.notes)


def test_async_message_creates_a_queue(design) -> None:
    node = compose(design).node("message-queue")
    assert node.archetype == "app::messageQueue" and node.type_id


def test_security_schemes_create_a_secret_store(design) -> None:
    assert compose(design).node("secret-store").archetype == "app::secretStore"


def test_no_security_schemes_no_secret_store(design) -> None:
    """신호가 없으면 만들지 않는다 — 있으면 좋을 것 같아서 넣지 않는다."""
    del design["artifacts"][0]["openapi"]["components"]
    assert compose(design).node("secret-store") is None


def test_actor_call_marks_public_exposure(design) -> None:
    notes = compose(design).node("order-api").notes
    assert any("공개 노출" in n.text and n.origin == ORIGIN_DESIGN for n in notes)


def test_deploy_hint_is_recorded_as_designer_claim_not_ours(design) -> None:
    """설계자가 지정해도 **우리 판단이 되지 않는다** — origin이 designer이고 여전히 hedge된다."""
    design["components"][0]["deployHint"] = {"compute": "kubernetes", "reason": "기존 클러스터 재사용"}
    node = compose(design).node("order-api")
    assert node.origin == ORIGIN_DESIGNER
    assert any("설계자가" in n.text for n in node.notes)


# --- 모르는 것을 채우지 않는다 --------------------------------------------------

def test_component_without_signals_is_reported_not_guessed(design) -> None:
    design["components"].append({"id": "mystery", "name": "정체불명"})
    plan = compose(design)
    assert any("mystery" in item for item in plan.unresolved)


def test_no_provider_means_no_price_join(design) -> None:
    """프로바이더가 없으면 대표 리전을 임의로 고르지 않는다 — 실측으로 확인된
    라우팅 변수가 '프로바이더를 밝혔는가'였다."""
    del design["requirements"]["provider"]
    plan = compose(design)
    assert not any(n.source == "costkb" for node in plan.nodes for n in node.notes)
    assert any("임의로 고르지 않습니다" in n.text for n in plan.notes)


def test_unknown_engine_hint_is_reported(design) -> None:
    """모르는 엔진을 관계형으로 몰면 조용히 틀린 서비스가 나온다."""
    design["artifacts"][1]["engineHint"] = "neo4j"
    assert any("neo4j" in item for item in compose(design).unresolved)


def test_redis_hint_routes_to_cache_not_relational(design) -> None:
    design["artifacts"][1]["engineHint"] = "redis"
    assert compose(design).node("order-api-db").archetype == "app::keyValueCache"


def test_contract_violation_short_circuits(design) -> None:
    """계약을 어긴 입력으로 계획을 만들면 짐작이 섞인다 — 만들지 않는다."""
    design["artifacts"][0]["componentId"] = "ghost"
    plan = compose(design)
    assert plan.nodes == []
    assert any("입력 계약 위반" in item for item in plan.unresolved)


# --- 답의 계약 -----------------------------------------------------------------

def test_answer_never_totals(design) -> None:
    text = _render_plan_text(compose(design))
    assert "합계를 내지 않습니다" in text


def test_answer_marks_inferences(design) -> None:
    text = _render_plan_text(compose(design))
    assert "우리 추론" in text and "검증된 사실이 아닙니다" in text


def test_answer_lists_what_it_could_not_answer(design) -> None:
    design["components"].append({"id": "mystery", "name": "정체불명"})
    assert "답하지 못한 것" in _render_plan_text(compose(design))


def test_class_only_design_answers_almost_nothing(design) -> None:
    """**"클래스 다이어그램만으론 배포를 정할 수 없다"**를 실제로 지키는가."""
    minimal = {
        "schemaVersion": "1", "name": "클래스만",
        "components": [{"id": "svc", "name": "Svc"}],
        "artifacts": [{
            "id": "c1", "kind": "class",
            "classes": [{"name": "A", "componentId": "svc", "stereotypes": ["Service"]}],
        }],
    }
    plan = compose(minimal)
    assert [n.id for n in plan.nodes] == ["svc"]
    assert plan.unresolved
