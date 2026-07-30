"""중립화 적용 지도(neutralization_map.json)의 구조적 불변식.

기계 층(호출 색인)은 재계산 가능해야 하고, 판정 층(기제 분류 — 우리 구성)은
**색인의 실물을 인용해야만** 선다. 인용 없는 판정은 부재가 곧 관측인 셀
(absenceIsEvidence)만 허용된다 — D1~D9 전례(라벨이 인용을 가림)의 재발 방지다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.depkb import extract_spider

_ARTIFACT = (
    Path(extract_spider.__file__).resolve().parent / "neutralization_map.json"
)

TYPES = ("network", "subnet", "firewall", "sshKey", "vm", "disk", "loadBalancer")
CSPS = ("aws", "azure", "gcp")


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(_ARTIFACT.read_text(encoding="utf-8"))


def test_source_is_pinned_by_hash(artifact) -> None:
    """핀 없는 소스는 재현이 안 된다 — 태그 이름만으로는 부족하고 해시까지."""
    assert artifact["_pin"]["tarballSha256"] == extract_spider.TARBALL_SHA256


@pytest.mark.skipif(not extract_spider.TARBALL.exists(),
                    reason="spider 타르볼 캐시가 없는 환경")
def test_call_index_is_recomputable_from_the_tarball(artifact) -> None:
    """색인은 타르볼의 사영이다 — 어긋나면 손이 들어간 것."""
    assert artifact["calls"] == extract_spider.scan_calls()


def test_every_judgment_cites_the_call_index(artifact) -> None:
    """판정 층의 모든 셀은 기계 층의 실물을 인용한다.

    인용이 빈 셀은 absenceIsEvidence 표시가 있어야 하고, 그 표시는 실제로
    색인에 해당 (csp, 핸들러) 호출이 **없어야** 참이다 — gcp KeyPair가 그
    유일한 예다(네이티브 호출 0건 = 값 인라인의 관측).
    """
    index = {(c["csp"], c["handler"], c["line"]) for c in artifact["calls"]}
    handlers_by_csp = {(c["csp"], c["handler"]) for c in artifact["calls"]}
    cells = artifact["judgments"]["cells"]
    for key, cell in cells.items():
        csp = key.rsplit(".", 1)[1]
        if not cell["cites"]:
            assert cell.get("absenceIsEvidence"), f"{key}: 인용 없는 판정"
            assert (csp, "KeyPairHandler") not in handlers_by_csp, (
                f"{key}: 부재를 주장하는데 색인에 호출이 있다"
            )
            continue
        for cite in cell["cites"]:
            assert (csp, cite["handler"], cite["line"]) in index, (
                f"{key}: 색인에 없는 인용 {cite}"
            )


def test_the_map_covers_all_cells_without_silent_gaps(artifact) -> None:
    """7타입 × 3CSP × 생성·삭제 42셀 전수 — 빠진 셀은 '아직 안 본 것'인데,
    표가 조용히 비면 '없음'으로 읽힌다. 남은 미결은 _note가 밝힌다."""
    cells = artifact["judgments"]["cells"]
    missing = [
        f"{t}.{op}.{p}" for t in TYPES for op in ("create", "delete")
        for p in CSPS if f"{t}.{op}.{p}" not in cells
    ]
    assert not missing, f"판정이 빠진 셀: {missing}"
    assert "미결" in artifact["judgments"]["_note"]


def test_headline_mechanisms_hold(artifact) -> None:
    """대표 판정 넷 — 바뀌면 소스 판이 바뀐 것이니 사람이 봐야 한다.

    azure VM은 합성+절단, gcp SG는 기제 치환, aws VPC는 IGW·Route 합성,
    gcp 키는 값 인라인(호출 부재). 이 넷이 '중립 그래프 하나가 벤더별로 다른
    세 문장의 평균'이라는 주장의 기둥이다.
    """
    cells = artifact["judgments"]["cells"]
    assert set(cells["vm.create.azure"]["mechanisms"]) == {"synthesis", "truncation"}
    assert cells["firewall.create.gcp"]["mechanisms"] == ["substitution"]
    assert "synthesis" in cells["network.create.aws"]["mechanisms"]
    assert cells["sshKey.create.gcp"]["mechanisms"] == ["value-inlining"]


def test_judgment_layer_declares_itself_as_ours(artifact) -> None:
    """기제 분류가 근거처럼 읽히지 않도록, 우리 구성임을 산출물이 스스로 밝힌다."""
    assert "우리 구성" in artifact["judgments"]["_note"]
