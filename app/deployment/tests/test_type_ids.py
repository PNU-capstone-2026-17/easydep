"""KB 사이의 조인 키(type_id) 정규화 테스트.

배경: id는 KB 사이의 **조인 키**인데 규칙이 KB마다 따로 있었다. graphkb에는 대표
표기를 고르는 로직이 있고 capacitykb에는 같은 로직이 복사돼 있으면서 정작 id를
만들 때는 쓰이지 않아, 같은 타입이 두 id로 갈렸다(kb-data-audit-2026-07-20 §1-(2)).
"""

from __future__ import annotations

from kbcommon.type_ids import make_type_id, read_azure_index

# 실제 index.json의 모양 — 같은 타입이 API 버전마다 다른 표기로 등재된다.
INDEX = {
    "resources": {
        "Microsoft.Compute/cloudServices@2025-03-01": {
            "$ref": "compute/microsoft.compute/2025-03-01/types.json#/1"
        },
        "microsoft.Compute/cloudServices@2025-07-01": {
            "$ref": "compute/microsoft.compute/2025-07-01/types.json#/1"
        },
        "Microsoft.Network/virtualNetworks@2025-01-01": {
            "$ref": "network/microsoft.network/2025-01-01/types.json#/2"
        },
        "Microsoft.Network/virtualNetworks@2026-01-01-preview": {
            "$ref": "network/microsoft.network/2026-01-01-preview/types.json#/2"
        },
    }
}


def test_casing_variants_collapse_to_one_type() -> None:
    """대소문자만 다른 등재는 한 타입이다 — ARM 타입명은 대소문자를 안 가린다."""
    index = read_azure_index(INDEX)
    assert len(index.latest) == 2


def test_representative_spelling_comes_from_the_latest_version() -> None:
    """대표 표기는 최신 안정 버전의 것. 2025-07-01이 이기므로 소문자 m이 대표다."""
    index = read_azure_index(INDEX)
    assert index.canonical("Microsoft.Compute/cloudServices") == (
        "microsoft.Compute/cloudServices"
    )


def test_preview_loses_to_stable() -> None:
    index = read_azure_index(INDEX)
    version, _path = index.latest["Microsoft.Network/virtualNetworks"]
    assert version == "2025-01-01"


def test_type_id_normalizes_before_prefixing() -> None:
    """**핵심 회귀**: 어느 표기로 들어와도 같은 조인 키가 나와야 한다.

    이게 깨졌을 때 capacitykb의 제약이 graphkb 노드와 안 이어졌다. 데이터가
    없어서가 아니라 철자가 달라서였다.
    """
    index = read_azure_index(INDEX)
    assert (
        index.type_id("Microsoft.Compute/cloudServices")
        == index.type_id("microsoft.Compute/cloudServices")
        == index.type_id("MICROSOFT.COMPUTE/CLOUDSERVICES")
    )


def test_unknown_type_is_left_alone() -> None:
    """모르는 이름을 지어내지 않는다 — 그대로 두고 조인에서 드러나게 한다."""
    index = read_azure_index(INDEX)
    assert index.canonical("Microsoft.Nope/things") == "Microsoft.Nope/things"


def test_make_type_id_is_the_only_place_that_knows_the_format() -> None:
    assert make_type_id("aws", "AWS::EC2::Instance") == "aws::AWS::EC2::Instance"


def test_both_parsers_agree_on_the_representative_spelling() -> None:
    """graphkb와 capacitykb가 같은 index로 같은 답을 내야 한다.

    두 파서가 각자 복사본을 들고 있던 시절에 여기가 갈렸다.
    """
    from capacitykb.parsers.azure import select_latest
    from graphkb.parsers.azure import parse_index

    graph, graph_latest = parse_index(INDEX)
    assert set(select_latest(INDEX)) == set(graph_latest)
    assert {n[len("azure::"):] for n in graph.nodes} == set(graph_latest)
