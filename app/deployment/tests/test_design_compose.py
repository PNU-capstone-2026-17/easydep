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
from nim_agent.design_tools import _render_plan_text, compose, deployment_answer

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


def test_serverless_hint_does_not_get_a_vm_price(design) -> None:
    """**실측으로 잡은 결함.** 처음엔 deployHint를 근거 라벨로만 기록하고 계획은
    그대로 뒀는데, 그러면 serverlessFunction을 지정해도 시간당 VM 단가가 붙었다 —
    서버리스는 호출당 과금이라 그 값은 그냥 틀린 값이다.
    """
    design["components"][0]["deployHint"] = {"compute": "serverlessFunction"}
    node = compose(design).node("order-api")
    assert node.role == "managed"
    assert node.type_id.endswith("Lambda::Function")
    assert not any("/h" in n.text for n in node.notes)
    assert any("호출당 과금" in n.text for n in node.notes)


def test_kubernetes_prices_the_node_group_not_the_component(design) -> None:
    """**파드가 도는 곳은 노드 그룹이다.** 컴포넌트마다 VM 단가를 붙이면 같은
    노드에 여러 파드가 올라가는 구조가 지워지고, 합치면 중복이 된다."""
    design["components"][0]["deployHint"] = {"compute": "kubernetes"}
    plan = compose(design)
    assert not any("/h" in n.text for n in plan.node("order-api").notes)
    node_group = plan.node("k8snodegroup")
    assert node_group is not None and node_group.type_id.endswith("EKS::Nodegroup")
    assert any("/h" in n.text for n in node_group.notes)


def test_kubernetes_carries_the_tumblebug_minimums_with_their_owner(design) -> None:
    """최소 사양은 cb-tumblebug이 요구하는 값이지 쿠버네티스가 정한 값이 아니다."""
    design["components"][0]["deployHint"] = {"compute": "kubernetes"}
    notes = compose(design).node("k8snodegroup").notes
    assert any("cb-tumblebug이 요구하는" in n.text for n in notes)


def test_template_counts_are_not_claimed_as_this_apps_need(design) -> None:
    """`k8scluster-across`는 멀티클라우드 데모라 클러스터가 8개다 — 그 숫자가
    이 앱에 필요한 수처럼 읽히면 안 된다."""
    design["components"][0]["deployHint"] = {"compute": "kubernetes"}
    notes = compose(design).node("k8snodegroup").notes
    assert any("이 앱에 필요한 수가 아닙니다" in n.text for n in notes)


# --- 공유 인프라 (bundlekb) ----------------------------------------------------

def test_network_boundary_exists_at_all(design) -> None:
    """**붙이기 전까지 배포 다이어그램에 네트워크가 통째로 없었다.** bundlekb는
    정확히 '무엇이 딸려 오나'에 답하려고 만든 축인데 구성기가 안 불렀다."""
    plan = compose(design)
    shared = {n.id for n in plan.nodes if n.role == "shared"}
    assert {"vnet", "subnet", "securitygroup", "sshkey"} <= shared


def test_shared_infra_is_one_set_not_per_component(design) -> None:
    """연결당 공유다 — 컴포넌트마다 세우면 컴포넌트 2개짜리 앱에 VPC가 2개 그려진다."""
    plan = compose(design)
    assert len([n for n in plan.nodes if n.id == "vnet"]) == 1


def test_shared_infra_maps_to_vendor_types_and_says_it_is_a_guess(design) -> None:
    node = compose(design).node("vnet")
    assert node.type_id == "aws::AWS::EC2::VPC"
    assert any("짐작" in n.text for n in node.notes)


def test_tumblebug_caveat_travels_with_the_shared_infra(design) -> None:
    """'클라우드가 요구하는 게 아니라 이 도구가 만드는 것'이 빠지면 오독된다."""
    notes = compose(design).node("vnet").notes
    assert any("이 도구가 만드는 것" in n.text for n in notes)


def test_multi_zone_requirement_is_finally_read(design) -> None:
    """계약이 받아 놓고 **안 읽던 칸**이다."""
    assert any("multiZone" in n.text or "가용영역" in n.text
               for n in compose(design).node("subnet").notes)


def test_subnet_carries_count_and_capacity(design) -> None:
    notes = [n.text for n in compose(design).node("subnet").notes]
    assert any("서브넷이 2개 필요" in t for t in notes)
    assert any("251" in t for t in notes)


def test_image_is_a_value_not_a_node(design) -> None:
    """이미지는 리소스가 아니라 값이다 — 벤더 타입이 없는 게 정상이라 매핑
    미결로 올리면 거짓 미결이 된다. 대신 실제 이미지 id를 붙인다."""
    plan = compose(design)
    assert plan.node("image") is None
    assert not any("core::image" in item for item in plan.unresolved)
    assert any("ami-" in n.text for n in plan.node("order-api").notes)


def test_all_serverless_means_no_vm_network(design) -> None:
    """전부 서버리스면 VM 네트워크가 필요 없다 — 없는 것을 그리지 않는다."""
    for component in design["components"]:
        component["deployHint"] = {"compute": "serverlessFunction"}
    plan = compose(design)
    assert not [n for n in plan.nodes if n.role == "shared"]


def test_deploy_hint_is_recorded_as_designer_claim_not_ours(design) -> None:
    """설계자가 지정해도 **우리 판단이 되지 않는다** — origin이 designer이고 여전히 hedge된다."""
    design["components"][0]["deployHint"] = {"compute": "kubernetes", "reason": "기존 클러스터 재사용"}
    node = compose(design).node("order-api")
    assert node.origin == ORIGIN_DESIGNER
    assert any("설계자가" in n.text for n in node.notes)


# --- 객체 스토리지 (파일 업로드 신호, 보강 3) -----------------------------------

def _add_upload_path(design: dict) -> None:
    design["artifacts"][0]["openapi"].setdefault("paths", {})["/files"] = {
        "post": {
            "requestBody": {
                "content": {"multipart/form-data": {"schema": {"type": "object"}}}
            }
        }
    }


def test_upload_body_creates_object_storage(design) -> None:
    """securitySchemes→비밀 저장소와 같은 계열 — OpenAPI가 말한 사실(업로드
    본문)에서 출발하되 결론은 inferred다."""
    _add_upload_path(design)
    plan = compose(design)
    node = plan.node("order-api-files")
    assert node is not None and node.archetype == "app::objectStorage"
    assert node.origin == ORIGIN_INFERRED
    assert ("order-api", "order-api-files") in {(e.from_id, e.to_id) for e in plan.edges}


def test_no_upload_no_object_storage(design) -> None:
    """신호가 없으면 만들지 않는다 — 있으면 좋을 것 같아서 넣지 않는다."""
    assert compose(design).node("order-api-files") is None


def test_gcp_object_storage_carries_billing_axes(design, monkeypatch) -> None:
    """보강 3의 소비자 실측 — gcp 계획의 객체 스토리지에 저장(용량-비례)·검색
    (사용량) 과금 축이 붙고, 합계 없음 고지가 함께 간다.

    conftest가 costkb를 빈 디렉터리(번들 모드)로 고정하므로, 커밋된 관리형
    산출물을 보려면 이 테스트만 기본 위치로 되돌린다(⑥-B 테스트의 선례).
    """
    from costkb import dataset as cost_dataset
    from kbcommon.artifact import DEFAULT_OUTPUT

    monkeypatch.setattr(cost_dataset, "DEFAULT_OUTPUT_DIR", DEFAULT_OUTPUT)
    cost_dataset.clear_caches()

    _add_upload_path(design)
    design["requirements"]["provider"] = "gcp"
    design["requirements"]["region"] = "asia-northeast3"
    plan = compose(design)
    node = plan.node("order-api-files")
    assert node.type_id == "gcp::StorageBucket"
    axes_notes = [n.text for n in node.notes if "과금 축" in n.text]
    assert axes_notes and "gcp asia-northeast3" in axes_notes[0]
    assert "용량-비례" in axes_notes[0] and "사용량" in axes_notes[0]
    assert "합계는 없습니다" in axes_notes[0]


# --- 진입점 (로드밸런서) --------------------------------------------------------

def test_exposed_vm_gets_an_lb_in_front(design) -> None:
    """공개 노출된 VM 앞에 진입점이 선다 — 다이어그램에 LB가 통째로 없던 구멍.
    LB 삽입은 설계가 아니라 **우리 권고**라 노드·선 전부 inferred다."""
    plan = compose(design)
    lb = plan.node("order-api-lb")
    assert lb is not None and lb.role == "ingress" and lb.origin == ORIGIN_INFERRED
    edges = {(e.from_id, e.to_id) for e in plan.edges}
    assert ("end-user", "order-api-lb") in edges
    assert ("order-api-lb", "order-api") in edges
    assert ("end-user", "order-api") not in edges  # 직결이 남으면 LB가 장식이 된다


def test_lb_maps_to_vendor_type_and_says_it_is_a_guess(design) -> None:
    lb = compose(design).node("order-api-lb")
    assert lb.type_id == "aws::AWS::ElasticLoadBalancingV2::LoadBalancer"
    assert any("짐작" in n.text for n in lb.notes)


def test_lb_is_unpriced_and_counted_in_the_budget_verdict(design) -> None:
    """LB 가격은 데이터셋에 없다 — 0으로 치지 않고 미가격 구성원으로 센다."""
    lb = compose(design).node("order-api-lb")
    assert lb.hourly_usd is None
    assert any("값이 붙지 않습니다" in n.text for n in lb.notes)
    design["requirements"]["monthlyBudgetUSD"] = 10_000
    text = deployment_answer(design, diagram=False)
    assert "부합 단정 불가" in text and "order-api-lb" in text


def test_unexposed_component_gets_no_lb(design) -> None:
    """워커는 actor가 부르지 않는다 — 노출 안 된 컴포넌트에 LB를 세우면
    '있으면 좋을 것 같아서 넣는' 실패다."""
    assert compose(design).node("order-worker-lb") is None


def test_k8s_exposure_notes_the_ingress_layer_instead_of_an_lb(design) -> None:
    """k8s의 노출은 클러스터 안(Service/Ingress)의 일이고 그 층의 대응 축이
    없다 — NLB를 억지로 세우지 않고 사실을 적는다."""
    design["components"][0]["deployHint"] = {"compute": "kubernetes"}
    plan = compose(design)
    assert plan.node("order-api-lb") is None
    assert ("end-user", "order-api") in {(e.from_id, e.to_id) for e in plan.edges}
    assert any("Service/Ingress" in n.text for n in plan.node("order-api").notes)


def test_exposed_vm_says_it_scales_out_but_count_is_not_ours(design) -> None:
    """스케일아웃 표현(보강 4) — 구조(서브그룹)는 스펙 명시라 적되, **대수는
    정하지 못한다**가 문장의 절반이어야 한다. 대수를 정하면 근거 없는 사이징이다."""
    notes = [n for n in compose(design).node("order-api").notes
             if "수평 확장" in n.text]
    assert notes and notes[0].origin == ORIGIN_KB
    assert "정하지 못합니다" in notes[0].text
    assert "1대 기준" in notes[0].text
    # 노출 안 된 워커에는 안 붙는다 — LB 뒤 확장 단위가 아니다.
    assert not [n for n in compose(design).node("order-worker").notes
                if "수평 확장" in n.text]


def test_budget_floor_declares_the_single_instance_basis(design) -> None:
    """하한이 "스케일아웃해도 이 값"으로 읽히면 안 된다 — 1대 기준 명시가
    판정문에 있어야 한다."""
    design["requirements"]["monthlyBudgetUSD"] = 10_000
    text = deployment_answer(design, diagram=False)
    assert "컴퓨트 각 1대 기준" in text
    assert "수평 확장 대수도 정해지지 않았습니다" in text


def test_lb_diagram_roundtrips_clean(design) -> None:
    """새 역할(ingress·hexagon)이 되파싱 검증을 통과해야 한다 — 그림 검사가
    실패하면 답 끝에 '자체 검증에서 걸린 것'이 붙는다."""
    text = deployment_answer(design, diagram=True)
    assert "자체 검증에서 걸린 것" not in text
    assert 'hexagon "' in text


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


def test_elasticsearch_hint_routes_to_search_index(design) -> None:
    """보강 2 — 검색 엔진을 관계형으로 몰던 자리가 searchIndex로 풀린다."""
    design["artifacts"][1]["engineHint"] = "elasticsearch"
    plan = compose(design)
    node = plan.node("order-api-db")
    assert node.archetype == "app::searchIndex"
    assert node.type_id == "aws::AWS::OpenSearchService::Domain"
    assert not plan.unresolved


def test_kafka_hint_stays_deliberately_unmapped(design) -> None:
    """ER 저장소로서의 kafka는 큐·스트림·저장 어느 축인지 설계 의도에 달렸다 —
    한 개념으로 몰면 조용히 틀린 서비스가 나온다. eventStream 개념이 생겼어도
    이 결정은 유지된다(미결 + 패턴 자문)."""
    from nim_agent.design_tools import _ENGINE_CONCEPT

    assert "kafka" not in _ENGINE_CONCEPT
    design["artifacts"][1]["engineHint"] = "kafka"
    assert any("kafka" in item for item in compose(design).unresolved)


def test_contract_violation_short_circuits(design) -> None:
    """계약을 어긴 입력으로 계획을 만들면 짐작이 섞인다 — 만들지 않는다."""
    design["artifacts"][0]["componentId"] = "ghost"
    plan = compose(design)
    assert plan.nodes == []
    assert any("입력 계약 위반" in item for item in plan.unresolved)


# --- 요구사항 대조 (부합 측정이 답에 실리는가) ----------------------------------

def test_priced_node_carries_a_machine_readable_hourly(design) -> None:
    """예산 판정은 노트 문장("$0.0468/h")을 되파싱하지 않는다 — 문구 하나에
    판정이 흔들리면 안 되므로 기계 값이 따로 실린다."""
    node = compose(design).node("order-api")
    assert node.hourly_usd and node.hourly_usd > 0


def test_tiny_budget_is_confirmed_over(design) -> None:
    """하한(값 붙은 부분의 월합)만으로 초과가 확정되는 경우 — 비대칭의 확정 쪽."""
    design["requirements"]["monthlyBudgetUSD"] = 1
    text = deployment_answer(design, diagram=False)
    assert "[요구사항 대조]" in text and "초과 확정" in text


def test_loose_budget_is_not_called_compliant(design) -> None:
    """하한이 예산 아래여도 부합이라 말하지 않는다 — 미가격 구성원을 0으로 치는
    그 실패를 판정에서도 막는다."""
    design["requirements"]["monthlyBudgetUSD"] = 10_000
    text = deployment_answer(design, diagram=False)
    assert "부합 단정 불가" in text


def test_no_budget_still_gets_a_verdict_line(design) -> None:
    """"기준 없음"도 판정이다 — 침묵이면 부분 답이 완전한 답처럼 읽힌다."""
    text = deployment_answer(design, diagram=False)
    assert "기준 없음" in text


def test_multizone_requirement_is_judged_in_the_answer(design) -> None:
    text = deployment_answer(design, diagram=False)
    assert "multiZone: 반영됨" in text


def test_steady_traffic_turns_the_burst_warning_into_a_verdict(design) -> None:
    """**⑥-A가 여는 판정.** 데모의 최저가 스펙(t3a.medium)은 버스트라 경고가
    붙는데, 지금까지는 이 앱에 문제인지 판단할 입력이 없었다 — trafficPattern이
    그 입력이고, steady면 상충으로 판정된다. 실제 costkb×perfkb 조인을 탄다."""
    design["requirements"]["trafficPattern"] = "steady"
    text = deployment_answer(design, diagram=False)
    assert "trafficPattern(steady): **상충**" in text


def test_stateful_serverless_hint_is_flagged_in_the_answer(design) -> None:
    design["requirements"]["stateless"] = False
    design["components"][0]["deployHint"] = {"compute": "serverlessFunction"}
    text = deployment_answer(design, diagram=False)
    assert "상충 가능성" in text and "우리 추론" in text


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
    # 컴포넌트는 하나뿐이고 **배포 형태는 여전히 미결**이다.
    assert [n.id for n in plan.nodes if n.role == "compute"] == ["svc"]
    assert any("배포 형태를 정하지 못했" in item for item in plan.unresolved)
    # 값은 하나도 안 붙는다 — provider가 없다.
    assert not any("/h" in n.text for node in plan.nodes for n in node.notes)
    # 관리형 서비스도 없다 — ER도 시퀀스도 없으니 만들 근거가 없다.
    assert not [n for n in plan.nodes if n.role == "managed"]
    # 공유 인프라는 나온다. **우리가 VM으로 가정한 결과**이고, 그 가정은
    # 컴포넌트 노트에 적혀 있다 — 가정을 숨긴 채 그리는 것과는 다르다.
    assert any("VM으로 가정" in n.text for n in plan.node("svc").notes)
