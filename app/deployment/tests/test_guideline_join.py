"""축을 엮은 답 — bundlekb × graphkb × costkb × perfkb.

`research.md` 목표 2의 근거 문장이 요구하는 두 반쪽을 잇는다. 여기 테스트가 고정하는
것은 **잇는 방식이 아니라 잇지 않기로 한 것들**이다.

    합계를 내지 않는다        값 없는 구성원을 0으로 두고 더하면 실제보다 낮다
    다리의 등급을 밝힌다      벤더 타입에서 오면 짐작 한 단계를 거친다
    '가격 축 없음'과 '못 골랐음'을 가른다   뜻이 반대인데 한 칸에 넣으면 둘 다 오독된다
    계획 하나만 본다          17벌을 늘어놓으면 서로 다른 계획이 한 계획으로 읽힌다
"""

from __future__ import annotations

import json

import pytest

from bundlekb import dataset as bundle_dataset
from graphkb import agent_api as graph_api
from nim_agent.guideline_tools import _guideline

VM = "core::vm"
NODE_GROUP = "core::k8sNodeGroup"


#: 스키마가 요구하는 출처 블록. **빠지면 costkb가 조용히 번들 36건으로 폴백**하고,
#: 그러면 픽스처를 안 보면서도 테스트가 통과할 수 있다(첫 판에서 실제로 그랬다).
_SOURCE = {
    "source": "test", "pin_kind": "tag", "pin": "v0",
    "url": "test://fixture", "sha256": "0" * 64, "bytes": 1,
    "fetched_at": None, "local_file": True,
}


def _node(node_id: str, layer: str, provider: str, name: str) -> dict:
    return {
        "id": node_id, "layer": layer, "provider": provider,
        "kind": "resource_type", "display_name": name, "source": "test",
    }


def _edge(src: str, dst: str, kind: str, evidence: str, basis: str,
          reviewed: bool, via: str = "") -> dict:
    return {
        "from": src, "to": dst, "type": kind, "via_property": via,
        "required": False, "cardinality": "one",
        "evidence": evidence, "basis": basis, "reviewed": reviewed,
    }


@pytest.fixture()
def built(tmp_path):
    """core 그래프 · 매핑 · 번들 · 스펙을 최소로 갖춘 환경."""
    out = tmp_path / "output"
    out.mkdir()

    (out / "core-graph.json").write_text(json.dumps({
        "nodes": [
            _node(VM, "core", "common", "vm"),
            _node("core::spec", "core", "common", "spec"),
            _node("core::vNet", "core", "common", "vNet"),
            _node(NODE_GROUP, "core", "common", "k8sNodeGroup"),
        ],
        "edges": [
            _edge(VM, "core::spec", "references", "swagger-field", "stated", True,
                  via="specId"),
            _edge(NODE_GROUP, "core::spec", "references", "swagger-field", "stated",
                  True, via="specId"),
        ],
        "_source": [_SOURCE],
    }, ensure_ascii=False), encoding="utf-8")

    (out / "mapping-graph.json").write_text(json.dumps({
        "nodes": [
            _node(VM, "core", "common", "vm"),
            _node("aws::AWS::EC2::Instance", "vendor", "aws", "Instance"),
        ],
        "edges": [
            _edge(VM, "aws::AWS::EC2::Instance", "equivalent_to",
                  "cb-spider-driver", "inferred", True),
        ],
        "_source": [_SOURCE],
    }, ensure_ascii=False), encoding="utf-8")

    (out / "tumblebug-bundles.json").write_text(json.dumps({
        "bundles": [
            {
                "id": "tumblebug::dynamic-vm", "name": "동적 VM", "provider": "core",
                "evidence": "tumblebug-dynamic", "anchor": VM,
                "description": None, "caveat": "이 도구가 만드는 것입니다",
                "members": [
                    {"typeId": VM, "tier": "always", "note": None},
                    {"typeId": "core::vNet", "tier": "always", "note": None},
                ],
            },
            {
                "id": "tumblebug::infra-many", "name": "infra-many", "provider": "core",
                "evidence": "tumblebug-template", "anchor": VM,
                "description": None, "caveat": None,
                "members": [{"typeId": VM, "tier": "always", "note": None, "count": 4}],
            },
        ],
        "cooccurrence": [],
        "_source": [_SOURCE],
    }, ensure_ascii=False), encoding="utf-8")

    # 스펙 이름을 일부러 실재하지 않는 것으로 둔다 — costkb는 산출물을 못 읽으면
    # **조용히 번들 36건으로 폴백**하므로, 실재하는 이름을 쓰면 폴백이 통과해 버린다.
    # `_note`가 빠져 스키마를 위반하던 첫 판이 실제로 그렇게 통과할 뻔했다.
    (out / "tumblebug-cost.json").write_text(json.dumps({
        "_note": "테스트 픽스처",
        "specs": [
            {"id": "aws+ap-northeast-2+test.large", "provider": "aws",
             "region": "ap-northeast-2", "specName": "test.large", "vCPU": 2,
             "memGiB": 8.0, "hourlyUSD": 0.1, "architecture": "x86_64",
             "infraType": "node", "acceleratorCount": 0, "acceleratorMemoryGB": 0},
        ],
        "_source": [_SOURCE],
    }, ensure_ascii=False), encoding="utf-8")

    graph_api._load_merged_cached.cache_clear()
    graph_api.concepts_with_spec.cache_clear()
    bundle_dataset.clear_caches()
    from costkb import dataset as cost_dataset

    cost_dataset.clear_caches()
    cost_dataset.DEFAULT_OUTPUT_DIR = out
    graph_api.DEFAULT_OUTPUT_DIR = out
    bundle_dataset.DEFAULT_OUTPUT_DIR = out
    yield out
    graph_api._load_merged_cached.cache_clear()
    graph_api.concepts_with_spec.cache_clear()
    bundle_dataset.clear_caches()
    cost_dataset.clear_caches()


# --- 다리 ---------------------------------------------------------------------

def test_priceable_concepts_come_from_the_graph(built) -> None:
    """**손으로 적지 않는다.** 적었으면 노드 그룹이 조용히 빠졌을 것이다."""
    assert graph_api.concepts_with_spec(str(built)) == frozenset({VM, NODE_GROUP})


def test_vendor_type_crosses_the_bridge_and_says_so(built) -> None:
    link = graph_api.core_concept("aws::AWS::EC2::Instance", output_dir=built)
    assert link is not None and link.core_id == VM
    assert link.hedged, "cb-spider 대응은 짐작이다"


def test_core_type_does_not_cross_anything(built) -> None:
    link = graph_api.core_concept(VM, output_dir=built)
    assert link is not None and not link.hedged


def test_unmapped_type_is_not_forced_onto_core(built) -> None:
    assert graph_api.core_concept("core::vNet", output_dir=built).core_id == "core::vNet"


# --- 답의 계약 -----------------------------------------------------------------

def test_answer_never_totals(built) -> None:
    """**가장 중요한 계약.** 값 없는 구성원을 0으로 두고 더하면 예산이 틀어진다."""
    text = " ".join(
        _guideline(VM, "aws", "ap-northeast-2", None, 2, 4, None, output_dir=built).split()
    ).lower()
    assert "**no total is produced.**" in text
    assert "[what cannot be given a value — this dataset has no price axis]" in text


def test_priced_and_unpriced_are_separate_sections(built) -> None:
    text = _guideline(VM, "aws", "ap-northeast-2", None, 2, 4, None, output_dir=built)
    assert "test.large" in text          # 값이 붙은 것
    assert "vNet" in text              # 값이 안 붙는 것도 감추지 않는다


def test_hedge_travels_with_the_price(built) -> None:
    """짐작을 거쳐 붙은 값이 원본이 선언한 값처럼 읽히면 안 된다."""
    text = " ".join(_guideline("aws::AWS::EC2::Instance", "aws", "ap-northeast-2", None,
                               2, 4, None, output_dir=built).split()).lower()
    assert "mapping is **a guess (reviewed)**" in text and "core::vm" in text


def test_core_query_carries_no_hedge(built) -> None:
    text = " ".join(
        _guideline(VM, "aws", "ap-northeast-2", None, 2, 4, None, output_dir=built).split()
    ).lower()
    assert "mapping is **a guess" not in text


def test_no_price_axis_is_not_the_same_as_could_not_pick(built) -> None:
    """뜻이 반대다 — 한 칸에 넣으면 '조건을 바꾸면 나온다'와 '원래 공짜'가 섞인다."""
    text = " ".join(
        _guideline(VM, "aws", "nowhere-9", None, 2, 4, None, output_dir=built).split()
    ).lower()
    assert "[would get a value, but none could be picked this time]" in text
    assert "check the region code or the criteria" in text
    # vNet은 여전히 '가격 축이 없음' 쪽이다
    assert "[what cannot be given a value — this dataset has no price axis]" in text


def test_count_multiplies_but_is_labelled_as_such(built) -> None:
    text = " ".join(_guideline(VM, "aws", "ap-northeast-2", None, 2, 4, "infra-many",
                               output_dir=built).split())
    assert "×4" in text and "4 × $0.1000/h = " in text


def test_one_plan_at_a_time(built) -> None:
    """17벌을 늘어놓으면 서로 다른 계획이 한 계획처럼 읽힌다."""
    text = _guideline(VM, "aws", "ap-northeast-2", None, 2, 4, None, output_dir=built)
    # 값이 붙은 줄이 계획마다 한 번씩 찍히던 것이 이 검사가 막는 것이다.
    assert text.count("$0.1000/h") == 1
    flat = " ".join(text.split()).lower()
    assert "there are also 1 other plans centred on the same resource" in flat


def test_default_plan_is_what_happens_without_choosing(built) -> None:
    bundle = bundle_dataset.default_bundle_for(VM, built)
    assert bundle is not None and bundle.evidence == "tumblebug-dynamic"


def test_source_caveat_is_not_dropped(built) -> None:
    text = _guideline(VM, "aws", "ap-northeast-2", None, 2, 4, None, output_dir=built)
    assert "이 도구가 만드는 것입니다" in text
