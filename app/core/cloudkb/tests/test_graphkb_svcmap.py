"""svcmap — 앱 개념(app::) ↔ 벤더 관리형 서비스.

여기 테스트가 고정하는 것은 대응 자체보다 **대응의 근거 규율**이다.

    basis는 넷 다 inferred      표가 명시하는 건 서비스 이름이고 타입 id 바인딩은 우리 검수
    독립 근거가 없으면 이유를    note 없는 svcmap-reviewed는 빌드가 경고한다
    app 층은 core가 아니다       core_concept(provider=="common")에 걸리면 안 된다
    실행 경계를 밝힌다           관리형 대응은 안내이지 tumblebug로 배포 가능이 아니다
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.graphkb.parsers.svcmap import (
    CONCEPTS,
    EVIDENCE_CROSS,
    EVIDENCE_DIAGRAMS,
    EVIDENCE_MS,
    EVIDENCE_REVIEWED,
    _row_matches,
)
from app.core.cloudkb.kbcommon.basis import INFERRED, basis_of, needs_hedge
from app.core.cloudkb.kbcommon.display import evidence_name

# --- 근거 규율 -----------------------------------------------------------------

@pytest.mark.parametrize("label", [EVIDENCE_CROSS, EVIDENCE_MS,
                                   EVIDENCE_DIAGRAMS, EVIDENCE_REVIEWED])
def test_every_svcmap_label_is_inferred_and_hedged(label: str) -> None:
    """**교차 확인이 있어도 basis는 안 올라간다.** 표가 명시하는 것은 서비스 이름
    수준이고, 타입 id에 붙이는 마지막 걸음은 언제나 우리 손 검수다 — 다리를
    건너면 등급이 떨어지는 그 규칙이 여기서도 지켜진다.
    """
    assert basis_of(label) == INFERRED
    assert needs_hedge(basis_of(label), reviewed=True)


@pytest.mark.parametrize("label", [EVIDENCE_CROSS, EVIDENCE_MS,
                                   EVIDENCE_DIAGRAMS, EVIDENCE_REVIEWED])
def test_every_svcmap_label_has_a_display_name(label: str) -> None:
    """이름 없는 라벨이 답변에 그대로 실린 사고가 있었다(kcc-hierarchy)."""
    assert evidence_name(label) != label


def test_hand_only_bindings_carry_a_reason() -> None:
    """독립 근거가 없으면 **왜 그래도 담았는지**가 표에 적혀 있어야 한다."""
    for concept, spec in CONCEPTS.items():
        for provider, bindings in spec["bindings"].items():
            for binding in bindings:
                if not binding["ms"] and not binding["dg"]:
                    assert binding["note"], (
                        f"{concept}/{provider}: 독립 근거도 이유도 없다"
                    )


def test_tencent_is_deliberately_absent() -> None:
    """MS 표에도 diagrams에도 없다(실측). 손 검수만으로 넣으면 기존 82엣지와
    같은 처지라 P1에서 뺐다 — 조용히 들어오면 그 결정이 무효가 된다."""
    for concept, spec in CONCEPTS.items():
        assert "tencent" not in spec["bindings"], concept


def test_gcp_cdn_is_deliberately_absent() -> None:
    """Cloud CDN은 백엔드 서비스의 플래그라 1:1 타입이 없다 — 억지로 대면
    'CDN을 만들었다'로 읽힌다."""
    assert "gcp" not in CONCEPTS["cdn"]["bindings"]


def test_expansion_concepts_exist(  # 보강 2 (2026-07-24)
) -> None:
    """관리형 k8s·검색·스트림·API 게이트웨이 — 클라우드 네이티브에서 흔한데
    대응이 통째로 없던 축. 같은 근거 규율(독립 소스 둘·손검수엔 사유)을 탄다."""
    for concept in ("containerService", "searchIndex", "eventStream", "apiGateway"):
        assert concept in CONCEPTS, concept


def test_pubsub_carries_both_queue_and_stream_on_purpose() -> None:
    """Pub/Sub는 큐·스트림 축을 하나가 겸한다(GCP의 설계). 한쪽만 담으면
    'GCP엔 스트림이 없다'로 읽힌다 — 겸한다는 노트가 바인딩에 있어야 한다."""
    queue = CONCEPTS["messageQueue"]["bindings"]["gcp"][0]
    stream = CONCEPTS["eventStream"]["bindings"]["gcp"][0]
    assert queue["type_id"] == stream["type_id"] == "gcp::PubSubTopic"
    assert "both queue and stream" in stream["note"]


# --- MS 표 매칭 ----------------------------------------------------------------

def test_keyword_matches_link_text_not_description() -> None:
    """**실측에서 났던 결함.** 행 전체에서 찾으면 설명 칸의 지나가는 언급에
    걸린다 — 'Amazon RDS'가 Compute Optimizer 행에 먼저 걸렸다."""
    optimizer_row = (
        "| [AWS Compute Optimizer](https://a) | [Azure Advisor](https://b) "
        "| Both analyze usage of EC2 and Amazon RDS to right-size |"
    )
    rds_row = "| Relational database | [Amazon RDS](https://a) | [Azure SQL Database](https://b) |"
    assert not _row_matches(optimizer_row, ("Amazon RDS",))
    assert _row_matches(rds_row, ("Amazon RDS",))


def test_keyword_is_substring_of_link_text() -> None:
    """링크 텍스트가 'Simple Queue Service (SQS)'처럼 길어도 잡혀야 한다."""
    row = "| [Simple Queue Service (SQS)](https://a) | [Queue Storage](https://b) |"
    assert _row_matches(row, ("Simple Queue Service",))


# --- 산출물 조인 ---------------------------------------------------------------

def _bundled(name: str) -> dict:
    from pathlib import Path

    from app.core.cloudkb.kbcommon import artifact

    path = artifact.resolve(Path("output"), name)
    assert path is not None, f"{name}이 output/에도 data/에도 없다"
    return artifact.load_json(path)


def test_every_binding_type_exists_in_some_graph() -> None:
    """**대응의 벤더 쪽 끝은 실재하는 타입 id여야 한다.** 아니면 capacitykb·
    graphkb 조인이 그 자리에서 끊긴다 (core 매핑의 '전부 대조, 0건 실패' 선례)."""
    from app.core.cloudkb.graphkb.agent_api import GRAPH_FILES

    known: set[str] = set()
    for name in GRAPH_FILES:
        if name == "svcmap-graph.json":
            continue
        try:
            known.update(n["id"] for n in _bundled(name)["nodes"])
        except AssertionError:
            continue
    missing = [
        binding["type_id"]
        for spec in CONCEPTS.values()
        for bindings in spec["bindings"].values()
        for binding in bindings
        if binding["type_id"] not in known
    ]
    assert not missing, f"그래프에 없는 바인딩: {missing}"


def test_artifact_app_nodes_are_not_core() -> None:
    """provider가 'common'이면 `core_concept`가 app 개념을 core로 집는다 —
    그러면 resource_guideline이 관리형 서비스에 VM 값을 붙이려 든다."""
    data = _bundled("svcmap-graph.json")
    for node in data["nodes"]:
        if node["id"].startswith("app::"):
            assert node["layer"] == "app"
            assert node["provider"] == "app"


def test_artifact_edges_all_reviewed_inferred() -> None:
    data = _bundled("svcmap-graph.json")
    for edge in data["edges"]:
        assert edge["reviewed"] is True
        assert edge["basis"] == "inferred"
        assert edge["type"] == "equivalent_to"


def test_coverage_keeps_the_matched_source_rows() -> None:
    """엣지에 note 칸이 없으므로 **표가 실제로 뭐라고 했는지**는 여기가 유일한
    자리다. 이게 비면 '교차 확인'이 검증 불가능한 주장이 된다."""
    data = _bundled("svcmap-graph.json")
    ms_rows = [
        b for c in data["_coverage"] for b in c["bindings"]
        if b["evidence"] in (EVIDENCE_CROSS, EVIDENCE_MS)
    ]
    assert ms_rows, "MS 근거 엣지가 하나도 없다"
    for binding in ms_rows:
        assert binding["msRow"], f"{binding['typeId']}: MS 근거인데 원문 행이 없다"


# --- 답의 계약 -----------------------------------------------------------------

def test_equivalent_types_reaches_managed_services_and_draws_the_boundary() -> None:
    from app.core.cloudkb.graphkb import agent_api

    agent_api._load_merged_cached.cache_clear()
    try:
        text = agent_api.equivalent_types("AWS::DynamoDB::Table")
        flat = " ".join(text.split()).lower()
        assert "microsoft.documentdb/databaseaccounts" in flat
        assert "a guess" in flat
        # 실행 경계 — 이게 빠지면 "우리 도구로 만들 수 있다"로 읽힌다.
        assert "guidance, not a guarantee it can be deployed" in flat
        assert "does not mean cb-tumblebug's execution path (vm, k8s) can create" in flat
    finally:
        agent_api._load_merged_cached.cache_clear()


def test_core_concept_is_not_polluted_by_app_layer() -> None:
    """벤더 DB 타입에서 core_concept를 물으면 **None이어야 한다** — app 개념이
    core로 잡히면 비용 조인이 관리형 서비스에 VM 단가를 붙인다."""
    from app.core.cloudkb.graphkb import agent_api

    agent_api._load_merged_cached.cache_clear()
    agent_api.concepts_with_spec.cache_clear()
    try:
        link = agent_api.core_concept("AWS::DynamoDB::Table")
        assert link is None
    finally:
        agent_api._load_merged_cached.cache_clear()
        agent_api.concepts_with_spec.cache_clear()
