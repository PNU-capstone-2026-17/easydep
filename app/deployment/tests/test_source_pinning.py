"""소스 고정과 프로버넌스 (R1).

**왜 이 테스트가 있나**: 감사(2026-07-20) 중에 로컬 캐시된 AWS zip(2,783,390 B)이 같은
URL의 라이브(2,794,161 B)와 이미 달라진 것을 발견했다. `output/`의 산출물은 더 이상
그 URL에 존재하지 않는 입력에서 나온 것이었고, 그러면 어떤 수치도 재현하거나 반증할 수
없다. 실제로 핀을 적용하고 재빌드하자 노드가 1,631 → 1,638로 바뀌었다.

여기서 지키는 계약은 둘이다:
1. 고정 ref는 **한 곳**(`kbcommon/sources.py`)에만 있고, 같은 소스를 쓰는 KB들이
   같은 값을 본다.
2. 캐시는 **URL이 같을 때만** 재사용된다 — 안 그러면 핀을 바꿔도 옛 데이터를 읽으면서
   새 커밋을 가리킨다고 기록하게 된다(고정의 목적이 무너지는 경로).
"""

from __future__ import annotations

import json

import pytest

from kbcommon import fetch
from kbcommon.sources import SOURCES, unpinnable


# --- 1. 고정 ref가 한 곳에서 관리되는가 ---


def test_every_source_declares_a_pin_kind() -> None:
    assert SOURCES, "소스 레지스트리가 비어 있다"
    for key, source in SOURCES.items():
        assert source.key == key
        assert source.pin_kind in ("tag", "commit", "digest", "bundled"), key
        assert source.pin, key
        if source.pin_kind != "bundled":
            assert source.url.startswith("http"), key


def test_pinned_sources_carry_their_ref_in_the_url() -> None:
    """tag/commit 소스는 URL에 ref가 박혀 있어야 한다 — 안 그러면 고정이 아니다."""
    for source in SOURCES.values():
        if source.pin_kind in ("digest", "bundled"):
            continue
        assert source.pin in source.url, f"{source.key}: URL에 핀이 없다"


def test_no_source_silently_tracks_a_moving_branch() -> None:
    """`main`/`master`를 가리키는 소스가 없어야 한다 (R1의 본체)."""
    for source in SOURCES.values():
        for branch in ("/main/", "/master/"):
            assert branch not in source.url, f"{source.key}가 {branch}를 추적한다"


def test_unpinnable_sources_are_declared_as_such() -> None:
    """고정 불가 소스는 숨기지 말고 드러낸다 — **목록을 여기 못 박는다.**

    번들 파일(`bundled`)은 git이 버전 관리하므로 여기 포함되지 않는다.

    이 목록이 자라는 것은 나쁜 일이므로 **자동으로 늘어나지 않게** 한다. 늘리려면
    여기 이유를 적어야 하고, 그 강제가 이 단언의 목적이다.

        cfn-schema           AWS가 zip 하나를 계속 덮어쓴다. 버전 URL이 없다.
        azure-retail-prices  가격 API라 고정할 대상 자체가 없다(질의가 곧 URL).
                             받은 응답의 sha256만 남겨 **바뀐 사실은 놓치지 않는다.**
                             재배포 허가도 없어 산출물을 `data/`에 커밋하지 않는다.
    """
    keys = {s.key for s in unpinnable()}
    assert keys == {"cfn-schema", "azure-retail-prices"}, (
        f"고정 불가 소스가 예상과 다르다: {keys}"
    )


def test_graphkb_and_capacitykb_read_the_same_bicep_commit() -> None:
    """두 KB가 같은 Azure 커밋을 봐야 한다.

    따로 상수를 들고 있던 시절에는 한쪽만 갱신되면 조용히 다른 세계를 봤다.
    """
    from capacitykb.parsers import azure as cap_azure
    from graphkb.parsers import azure as graph_azure

    assert graph_azure.DEFAULT_BASE_URL == cap_azure.DEFAULT_BASE_URL
    assert SOURCES["bicep-types-az"].pin in graph_azure.DEFAULT_BASE_URL


def test_both_cfn_parsers_read_the_same_zip() -> None:
    from capacitykb.parsers import cfn as cap_cfn
    from graphkb.parsers import cfn as graph_cfn

    assert graph_cfn.DEFAULT_ZIP_URL == cap_cfn.DEFAULT_ZIP_URL


# --- 2. 캐시가 URL 변경을 알아채는가 ---


def _fake_download(monkeypatch, payload: bytes, headers: dict | None = None):
    """httpx.stream을 대체해 네트워크 없이 fetch_cached를 돌린다."""

    class _Response:
        def __init__(self):
            self.headers = headers or {}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield payload

    class _Ctx:
        def __enter__(self):
            return _Response()

        def __exit__(self, *exc):
            return False

    calls = []

    def _stream(method, url, **kwargs):
        calls.append(url)
        return _Ctx()

    monkeypatch.setattr(fetch.httpx, "stream", _stream)
    return calls


def test_provenance_records_what_was_actually_fetched(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDKB_CACHE_DIR", str(tmp_path))
    _fake_download(monkeypatch, b"hello", {"Last-Modified": "Sat, 18 Jul 2026 06:29:09 GMT"})

    path = fetch.fetch_cached("https://example.test/a.json", "a.json")
    record = fetch.provenance(path)

    assert record is not None
    assert record["url"] == "https://example.test/a.json"
    assert record["bytes"] == 5
    # sha256("hello")
    assert record["sha256"].startswith("2cf24dba5fb0a30e")
    assert record["last_modified"] == "Sat, 18 Jul 2026 06:29:09 GMT"


def test_cache_is_reused_for_the_same_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDKB_CACHE_DIR", str(tmp_path))
    calls = _fake_download(monkeypatch, b"hello")

    fetch.fetch_cached("https://example.test/a.json", "a.json")
    fetch.fetch_cached("https://example.test/a.json", "a.json")
    assert len(calls) == 1, "같은 URL인데 다시 받았다"


def test_changing_the_pin_invalidates_the_cache(tmp_path, monkeypatch) -> None:
    """**핵심 회귀**: 파일명이 같아도 URL이 바뀌면 다시 받아야 한다.

    고정 ref를 올렸는데 캐시를 재사용하면, 빌드는 새 커밋을 기록하면서 실제로는
    옛 데이터를 읽는다. 고정이 오히려 거짓말을 하게 되는 경로다.
    """
    monkeypatch.setenv("CLOUDKB_CACHE_DIR", str(tmp_path))
    calls = _fake_download(monkeypatch, b"hello")

    fetch.fetch_cached("https://example.test/OLDSHA/a.json", "a.json")
    fetch.fetch_cached("https://example.test/NEWSHA/a.json", "a.json")

    assert len(calls) == 2, "핀이 바뀌었는데 옛 캐시를 재사용했다"
    assert fetch.provenance(tmp_path / "a.json")["url"].endswith("NEWSHA/a.json")


def test_cache_without_provenance_is_refetched(tmp_path, monkeypatch) -> None:
    """정체를 모르는 캐시는 신뢰하지 않는다 (고정 도입 이전 파일)."""
    monkeypatch.setenv("CLOUDKB_CACHE_DIR", str(tmp_path))
    (tmp_path / "a.json").write_bytes(b"stale")
    calls = _fake_download(monkeypatch, b"hello")

    path = fetch.fetch_cached("https://example.test/a.json", "a.json")
    assert len(calls) == 1
    assert path.read_bytes() == b"hello"


# --- 3. 산출물이 출처를 싣는가 ---


def test_describe_source_falls_back_to_hashing_a_local_file(tmp_path) -> None:
    """프로버넌스가 없어도 "모른다"를 산출물에 남기지 않는다."""
    p = tmp_path / "local.json"
    p.write_bytes(b"hello")
    record = fetch.describe_source(p, "cfn-schema")

    assert record["local_file"] is True
    assert record["sha256"].startswith("2cf24dba5fb0a30e")
    assert record["pin_kind"] == "digest"  # 레지스트리 정보가 합쳐진다


def test_describe_source_set_changes_when_any_file_changes(tmp_path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    first = fetch.describe_source_set([a, b], "bicep-types-az")

    b.write_bytes(b"two!")
    second = fetch.describe_source_set([a, b], "bicep-types-az")

    assert first["files"] == 2
    assert first["sha256"] != second["sha256"], "파일이 바뀌었는데 묶음 해시가 같다"
    assert first["pin"] == SOURCES["bicep-types-az"].pin


def test_graph_roundtrips_provenance(tmp_path) -> None:
    from graphkb.model import Graph, Node

    graph = Graph()
    graph.add_node(Node(id="core::vm", layer="core", provider="common",
                        display_name="vm", source="test"))
    graph.provenance = [{"source": "cfn-schema", "sha256": "a" * 64}]
    out = tmp_path / "g.json"
    graph.save(out)  # 스키마 검증을 통과해야 한다

    assert json.loads(out.read_text(encoding="utf-8"))["_source"][0]["sha256"] == "a" * 64
    assert Graph.load(out).provenance == graph.provenance


def test_capacity_roundtrips_provenance(tmp_path) -> None:
    from capacitykb.model import CapacitySet, Constraint

    caps = CapacitySet()
    caps.add_constraint(Constraint(type_id="aws::AWS::EC2::Volume", property="Size",
                                   kind="min", value=1, evidence="cfn-schema"))
    caps.provenance = [{"source": "cfn-schema", "sha256": "b" * 64}]
    out = tmp_path / "c.json"
    caps.save(out)

    assert CapacitySet.load(out).provenance == caps.provenance


@pytest.mark.parametrize("name", ["aws-graph.json", "aws-capacity.json"])
def test_shipped_artifacts_declare_their_source(name) -> None:
    """빌드된 산출물에는 출처가 실려 있어야 한다 (없으면 skip — 빌드 전일 수 있다)."""
    from pathlib import Path

    path = Path("output") / name
    if not path.exists():
        pytest.skip(f"{name} 미빌드")
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.get("_source")
    assert sources, f"{name}에 _source가 없다 — 어느 입력에서 나왔는지 알 수 없다"
    for record in sources:
        assert len(record["sha256"]) == 64
