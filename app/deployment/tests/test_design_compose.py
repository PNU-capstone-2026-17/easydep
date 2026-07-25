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

from app.deployment.appkb.plan import ORIGIN_DESIGN, ORIGIN_DESIGNER, ORIGIN_INFERRED, ORIGIN_KB
from app.deployment.nim_agent.design_tools import (
    _VM_ASSUMED,
    _render_plan_text,
    compose,
    deployment_answer,
)

_EXAMPLE = Path(__file__).resolve().parent.parent / "appkb" / "examples" / "order-demo.json"


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


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
    assert any("receives async messages" in flat(n.text) for n in node.notes)


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
    assert any("publicly exposed" in flat(n.text) and n.origin == ORIGIN_DESIGN
               for n in notes)


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
    assert any("billed per invocation" in flat(n.text) for n in node.notes)


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
    assert any("a value cb-tumblebug requires, not one Kubernetes sets" in flat(n.text)
               for n in notes)


def test_template_counts_are_not_claimed_as_this_apps_need(design) -> None:
    """`k8scluster-across`는 멀티클라우드 데모라 클러스터가 8개다 — 그 숫자가
    이 앱에 필요한 수처럼 읽히면 안 된다."""
    design["components"][0]["deployHint"] = {"compute": "kubernetes"}
    notes = compose(design).node("k8snodegroup").notes
    assert any("not the number this app needs" in flat(n.text) for n in notes)


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
    assert any("the mapping is a guess (reviewed)" in flat(n.text) for n in node.notes)


def test_tumblebug_caveat_travels_with_the_shared_infra(design) -> None:
    """'클라우드가 요구하는 게 아니라 이 도구가 만드는 것'이 빠지면 오독된다."""
    notes = compose(design).node("vnet").notes
    assert any("what this tool creates, not what the cloud requires" in flat(n.text)
               for n in notes)


def test_multi_zone_requirement_is_finally_read(design) -> None:
    """계약이 받아 놓고 **안 읽던 칸**이다."""
    assert any("multiZone" in flat(n.text) or "availability zone" in flat(n.text)
               for n in compose(design).node("subnet").notes)


def test_subnet_carries_count_and_capacity(design) -> None:
    notes = [flat(n.text) for n in compose(design).node("subnet").notes]
    assert any("needs 2 subnets" in t for t in notes)
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
    assert any("The designer specified kubernetes" in flat(n.text) for n in node.notes)


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


def test_gcp_object_storage_carries_billing_axes(design, costkb_committed) -> None:
    """보강 3의 소비자 실측 — gcp 계획의 객체 스토리지에 저장(용량-비례)·검색
    (사용량) 과금 축이 붙고, 합계 없음 고지가 함께 간다."""
    _add_upload_path(design)
    design["requirements"]["provider"] = "gcp"
    design["requirements"]["region"] = "asia-northeast3"
    plan = compose(design)
    node = plan.node("order-api-files")
    assert node.type_id == "gcp::StorageBucket"
    axes_notes = [flat(n.text) for n in node.notes if "Billing axes" in flat(n.text)]
    assert axes_notes and "gcp asia-northeast3" in axes_notes[0]
    assert "capacity-rate" in axes_notes[0] and "usage" in axes_notes[0]
    assert "no total is produced" in axes_notes[0]


# --- 레지던시 (2층-A) -----------------------------------------------------------

def test_residency_gets_reference_material_not_a_verdict(design) -> None:
    """국가 판정 소스가 없다(리전 표시 이름은 프로바이더 자유 서식 — 실측).
    산문에서 국가를 추출해 판정하면 확신에 찬 오답이라, **대조 자료 + 판정 불가
    명시**까지가 소비다."""
    design["requirements"]["dataResidency"] = "한국"
    text = flat(deployment_answer(design, diagram=False))
    assert "dataResidency(한국): **no verdict**" in text
    assert "South Korea (Seoul)" in text          # envkb 원본 표시 이름
    assert "We do not judge the country" in text


def test_no_residency_no_reference_note(design) -> None:
    assert not [n for n in compose(design).notes
                if "Residency requirement" in flat(n.text)]


# --- 설계자 아키타입 지정 (2층-B) ------------------------------------------------

def test_archetype_hint_resolves_kafka_as_designer_claim(design) -> None:
    """kafka는 우리가 몰면 안 되는 모호 엔진이다(큐·스트림·저장이 설계 의도에
    달림). archetypeHint는 그 해소 경로 — 설계자의 주장이라 origin=designer로
    hedge되고, 미결·자문은 사라진다(해소됐으므로)."""
    design["artifacts"][1]["engineHint"] = "kafka"
    design["artifacts"][1]["archetypeHint"] = "eventStream"
    plan = compose(design)
    node = plan.node("order-api-db")
    assert node.archetype == "app::eventStream"
    assert node.origin == ORIGIN_DESIGNER
    assert node.type_id == "aws::AWS::Kinesis::Stream"
    assert any("The designer specified eventStream (archetypeHint)" in flat(n.text)
               for n in node.notes)
    assert not any("kafka" in item for item in plan.unresolved)
    assert not any(n.source == "pattern-advisory" for n in node.notes)


def test_archetype_hint_must_be_a_known_concept(design) -> None:
    """enum 밖의 지정은 계약 위반이다 — 임의 문자열이 아키타입이 되면 svcmap
    조인이 조용히 빈 답이 된다."""
    design["artifacts"][1]["archetypeHint"] = "blockchainLedger"
    plan = compose(design)
    assert plan.nodes == []
    assert any("input contract violation" in flat(item) for item in plan.unresolved)


# --- 컴퓨트 방식 비교 판정 (아키텍처 권고 — 결정 대행 아님) ----------------------

def _comparison_notes(plan):
    return [n for n in plan.notes
            if "Compute method comparison" in flat(n.text)
            or n.source == "method-comparison"]


def test_recommendation_only_when_exactly_one_method_survives(design) -> None:
    """steady(버스트 상충으로 VM·k8s 탈락) + stateless=true(서버리스 무상충)일
    때만 권고가 난다 — 그리고 권고는 hedge된 우리 권고다."""
    design["requirements"].update({"trafficPattern": "steady", "stateless": True})
    notes = _comparison_notes(compose(design))
    verdict = notes[-1]
    assert "Recommendation: serverless" in flat(verdict.text)
    assert verdict.origin == ORIGIN_INFERRED
    assert "not a verified fact" in flat(verdict.text)
    # 판정에 없는 결정 입력을 명시
    assert "outside the measured axes" in flat(verdict.text)


def test_no_recommendation_when_all_methods_conflict(design) -> None:
    """전멸이면 임의로 하나를 고르지 않는다."""
    design["requirements"].update({"trafficPattern": "steady", "stateless": False})
    notes = _comparison_notes(compose(design))
    assert "No recommendation" in flat(notes[-1].text)
    assert "methods with no conflict: none" in flat(notes[-1].text)


def test_sparse_requirements_keep_all_options_open(design) -> None:
    """판별 사실이 없으면 선택지를 유지하고, stateless 미확인은 보류로 표시된다."""
    notes = _comparison_notes(compose(design))
    body = flat(notes[0].text)
    assert "stateless unconfirmed" in body
    assert ("No recommendation" in flat(notes[-1].text)
            and "VM, k8s, serverless" in flat(notes[-1].text))


def test_all_hinted_components_get_no_comparison(design) -> None:
    """설계자가 전부 지정했으면 비교할 결정이 없다 — 노이즈를 만들지 않는다."""
    for component in design["components"]:
        component["deployHint"] = {"compute": "vm"}
    assert not _comparison_notes(compose(design))


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
    assert any("the mapping is a guess (reviewed)" in flat(n.text) for n in lb.notes)


def test_lb_is_unpriced_and_counted_in_the_budget_verdict(design) -> None:
    """LB 가격은 데이터셋에 없다 — 0으로 치지 않고 미가격 구성원으로 센다."""
    lb = compose(design).node("order-api-lb")
    assert lb.hourly_usd is None
    assert any("Load balancer prices are not in this dataset" in flat(n.text)
               for n in lb.notes)
    design["requirements"]["monthlyBudgetUSD"] = 10_000
    text = flat(deployment_answer(design, diagram=False))
    assert "**cannot be asserted to fit**" in text and "order-api-lb" in text


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


def test_azure_lb_carries_global_billing_axes(design, costkb_committed) -> None:
    """1층 ③ — LB 노드에 과금 축이 붙되, 원본이 리전 무관(Global)으로 공표한
    값이라는 사실이 함께 실린다."""
    design["requirements"]["provider"] = "azure"
    design["requirements"]["region"] = "koreasouth"
    lb = compose(design).node("order-api-lb")
    texts = [flat(n.text) for n in lb.notes]
    assert any("Billing axes (azure Global" in t for t in texts)
    assert any("publishes as region-independent (Global)" in t for t in texts)
    assert not any("Load balancer prices are not in this dataset" in t for t in texts)


def test_gcp_exposed_plan_notes_egress(design, costkb_committed) -> None:
    """1층 ② — 노출이 있는 gcp 계획에 이그레스 축이 알려지되, **곱하지 않는다**."""
    design["requirements"]["provider"] = "gcp"
    design["requirements"]["region"] = "asia-northeast3"
    plan = compose(design)
    egress = [flat(n.text) for n in plan.notes if "Internet egress" in flat(n.text)]
    assert egress and "we do not multiply it out" in egress[0]
    # 구간 이름은 costkb 데이터셋(data/*.gz) 값이다. `design_tools`가 이 문자열을
    # 리터럴로 걸러 대표 단가를 고르므로(`"0-1TB" in meter`), 여기서 함께 고정한다 —
    # 어긋나면 대표 단가가 **조용히** 빠진다.
    assert "0-1TB/month band" in egress[0]


def test_azure_exposed_plan_notes_egress_too(design, costkb_committed) -> None:
    """검증 라운드 ② — gcp만 있던 이그레스 비대칭이 azure(Bandwidth
    serviceName, Data Transfer Out만 큐레이션)로 닫혔다."""
    design["requirements"]["provider"] = "azure"
    design["requirements"]["region"] = "koreasouth"
    plan = compose(design)
    egress = [flat(n.text) for n in plan.notes if "Internet egress" in flat(n.text)]
    assert egress and "we do not multiply it out" in egress[0]


def test_aws_plan_has_no_egress_claim(design) -> None:
    """이그레스 수록은 gcp뿐이다 — aws 계획에서 이그레스 단가가 나오면 거짓이다."""
    assert not [n for n in compose(design).notes
                if "Internet egress" in flat(n.text)]


def test_exposed_vm_says_it_scales_out_but_count_is_not_ours(design) -> None:
    """스케일아웃 표현(보강 4) — 구조(서브그룹)는 스펙 명시라 적되, **대수는
    정하지 못한다**가 문장의 절반이어야 한다. 대수를 정하면 근거 없는 사이징이다."""
    notes = [n for n in compose(design).node("order-api").notes
             if "horizontal scaling unit" in flat(n.text)]
    assert notes and notes[0].origin == ORIGIN_KB
    assert "not something this knowledge base can decide" in flat(notes[0].text)
    assert "the unit price and the monthly floor are for one instance" in flat(notes[0].text)
    # 노출 안 된 워커에는 안 붙는다 — LB 뒤 확장 단위가 아니다.
    assert not [n for n in compose(design).node("order-worker").notes
                if "horizontal scaling unit" in flat(n.text)]


def test_budget_floor_declares_the_single_instance_basis(design) -> None:
    """하한이 "스케일아웃해도 이 값"으로 읽히면 안 된다 — 1대 기준 명시가
    판정문에 있어야 한다."""
    design["requirements"]["monthlyBudgetUSD"] = 10_000
    text = flat(deployment_answer(design, diagram=False))
    assert "one instance per compute" in text
    assert "the number of horizontally scaled instances is not fixed" in text


def test_lb_diagram_roundtrips_clean(design) -> None:
    """새 역할(ingress·hexagon)이 되파싱 검증을 통과해야 한다 — 그림 검사가
    실패하면 답 끝에 '자체 검증에서 걸린 것'이 붙는다."""
    text = deployment_answer(design, diagram=True)
    assert "Caught by our own verification" not in flat(text)
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
    assert any("we do not pick one arbitrarily" in flat(n.text) for n in plan.notes)


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
    from app.deployment.nim_agent.design_tools import _ENGINE_CONCEPT

    assert "kafka" not in _ENGINE_CONCEPT
    design["artifacts"][1]["engineHint"] = "kafka"
    assert any("kafka" in item for item in compose(design).unresolved)


def test_contract_violation_short_circuits(design) -> None:
    """계약을 어긴 입력으로 계획을 만들면 짐작이 섞인다 — 만들지 않는다."""
    design["artifacts"][0]["componentId"] = "ghost"
    plan = compose(design)
    assert plan.nodes == []
    assert any("input contract violation" in flat(item) for item in plan.unresolved)


# --- 요구사항 대조 (부합 측정이 답에 실리는가) ----------------------------------

def test_priced_node_carries_a_machine_readable_hourly(design) -> None:
    """예산 판정은 노트 문장("$0.0468/h")을 되파싱하지 않는다 — 문구 하나에
    판정이 흔들리면 안 되므로 기계 값이 따로 실린다."""
    node = compose(design).node("order-api")
    assert node.hourly_usd and node.hourly_usd > 0


def test_tiny_budget_is_confirmed_over(design) -> None:
    """하한(값 붙은 부분의 월합)만으로 초과가 확정되는 경우 — 비대칭의 확정 쪽."""
    design["requirements"]["monthlyBudgetUSD"] = 1
    text = flat(deployment_answer(design, diagram=False))
    assert "[Requirements comparison]" in text and "**over, confirmed**" in text


def test_loose_budget_is_not_called_compliant(design) -> None:
    """하한이 예산 아래여도 부합이라 말하지 않는다 — 미가격 구성원을 0으로 치는
    그 실패를 판정에서도 막는다."""
    design["requirements"]["monthlyBudgetUSD"] = 10_000
    text = flat(deployment_answer(design, diagram=False))
    assert "**cannot be asserted to fit**" in text


def test_no_budget_still_gets_a_verdict_line(design) -> None:
    """"기준 없음"도 판정이다 — 침묵이면 부분 답이 완전한 답처럼 읽힌다."""
    text = flat(deployment_answer(design, diagram=False))
    assert "Budget: no yardstick" in text


def test_multizone_requirement_is_judged_in_the_answer(design) -> None:
    text = flat(deployment_answer(design, diagram=False))
    assert "multiZone: reflected" in text


def test_steady_traffic_turns_the_burst_warning_into_a_verdict(design) -> None:
    """**⑥-A가 여는 판정.** 데모의 최저가 스펙(t3a.medium)은 버스트라 경고가
    붙는데, 지금까지는 이 앱에 문제인지 판단할 입력이 없었다 — trafficPattern이
    그 입력이고, steady면 상충으로 판정된다. 실제 costkb×perfkb 조인을 탄다."""
    design["requirements"]["trafficPattern"] = "steady"
    text = flat(deployment_answer(design, diagram=False))
    assert "trafficPattern(steady): **conflict**" in text


def test_stateful_serverless_hint_is_flagged_in_the_answer(design) -> None:
    design["requirements"]["stateless"] = False
    design["components"][0]["deployHint"] = {"compute": "serverlessFunction"}
    text = flat(deployment_answer(design, diagram=False))
    assert "possible conflict" in text and "we inferred" in text


# --- 답의 계약 -----------------------------------------------------------------

def test_answer_never_totals(design) -> None:
    text = flat(_render_plan_text(compose(design)))
    assert "**No total is produced**" in text


def test_answer_marks_inferences(design) -> None:
    text = flat(_render_plan_text(compose(design)))
    assert "we inferred" in text and "are not verified facts" in text


def test_answer_lists_what_it_could_not_answer(design) -> None:
    design["components"].append({"id": "mystery", "name": "정체불명"})
    assert "[Could not answer]" in _render_plan_text(compose(design))


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
    assert any("could not decide the deployment form" in flat(item)
               for item in plan.unresolved)
    # 값은 하나도 안 붙는다 — provider가 없다.
    assert not any("/h" in n.text for node in plan.nodes for n in node.notes)
    # 관리형 서비스도 없다 — ER도 시퀀스도 없으니 만들 근거가 없다.
    assert not [n for n in plan.nodes if n.role == "managed"]
    # 공유 인프라는 나온다. **우리가 VM으로 가정한 결과**이고, 그 가정은
    # 컴포넌트 노트에 적혀 있다 — 가정을 숨긴 채 그리는 것과는 다르다.
    assert any(_VM_ASSUMED in n.text for n in plan.node("svc").notes)


def _steady_stateless_design() -> dict:
    """deployHint 없는 컴포넌트 1개 + steady·stateless=true —
    비교 판정에서 서버리스만 상충이 없어 권고가 나는 입력(실측)."""
    return {
        "schemaVersion": "1", "name": "probe-app",
        "components": [{"id": "api", "name": "Api"}],
        "artifacts": [{
            "id": "o1", "kind": "openapi", "componentId": "api",
            "openapi": {"openapi": "3.0.0", "info": {"title": "api"}, "paths": {}},
        }],
        "requirements": {
            "provider": "aws", "region": "ap-northeast-2",
            "trafficPattern": "steady", "stateless": True,
        },
    }


def test_recommendation_is_flagged_where_the_assumption_is_made() -> None:
    """**권고가 각주로만 있으면 읽히지 않는다.**

    라이브 실측(X7, 5회 중 4회)에서 도구는 서버리스를 권고했는데 모델은 "VM으로
    가정했습니다"만 답에 옮겼다. 계획 본문·유일한 구체 가격·다이어그램이 전부 VM
    위에 있고 권고는 27줄 뒤 한 줄이었으니, 문서에서 압도적인 쪽이 읽힌 것이다.
    가정을 적은 **바로 그 노트 다음**에 권고가 붙어야 둘이 같이 읽힌다.
    """
    plan = compose(_steady_stateless_design())
    texts = [n.text for node in plan.nodes for n in node.notes]
    assumed = next(i for i, t in enumerate(texts) if t.startswith(_VM_ASSUMED))
    assert "serverless" in flat(texts[assumed + 1]), "가정 바로 다음에 권고가 없다"
    assert "do not fix them as they stand" in flat(texts[assumed + 1])


def test_plan_itself_stays_vm_when_recommendation_differs() -> None:
    """**권고는 권고이지 결정이 아니다.** 계획을 임의로 서버리스로 바꾸면
    이 저장소가 막아 온 실패(짐작을 결정으로 승격)가 된다 — 값·다이어그램은
    VM 기준으로 남고, 다르다는 사실만 밝힌다."""
    plan = compose(_steady_stateless_design())
    api = next(n for n in plan.nodes if n.id == "api")
    assert any("t3a" in n.text or "$" in n.text for n in api.notes), "VM 값이 사라졌다"
