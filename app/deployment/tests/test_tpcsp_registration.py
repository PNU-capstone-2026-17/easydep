"""tpcsp에 CSP를 더하면 **네 곳이 함께 움직여야 한다**.

`nhn`을 `PROVIDERS`에만 더했더니 실제로 이렇게 됐다:

    CLI 분기가 소스 목록을 따로 하드코딩하고 있어 nhn이 안 걸렸고,
    **다음 elif(azure-quota)로 흘러가** 오류 없이 `nhn-capacity.json`에
    Azure 쿼터 542건이 쓰였다.

조용히 틀린 산출물이 나오는 쪽이 빌드가 죽는 것보다 나쁘다. 오늘만 세 번째로 만난
"같은 목록이 두 벌"이라 여기서 묶는다.
"""

from __future__ import annotations

import pytest

from capacitykb.agent_api import CAPACITY_FILES
from capacitykb.cli import DEFAULT_OUTPUTS, _tpcsp_keys
from capacitykb.parsers.tpcsp import PROVIDERS
from graphkb.agent_api import GRAPH_FILES


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_cli_routes_every_provider_to_tpcsp(key) -> None:
    """**핵심 회귀.** 안 걸리면 다음 분기로 새서 엉뚱한 파서가 돈다."""
    assert key in _tpcsp_keys()


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_cli_knows_where_to_write(key) -> None:
    assert key in DEFAULT_OUTPUTS, f"{key}의 출력 경로가 없습니다"


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_agent_apis_read_what_the_build_writes(key) -> None:
    """빌드는 되는데 도구가 안 읽으면 데이터가 있어도 답이 안 나온다."""
    provider = PROVIDERS[key]["provider"]
    assert f"{provider}-capacity.json" in CAPACITY_FILES
    assert f"{provider}-graph.json" in GRAPH_FILES


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_output_filename_matches_the_provider_namespace(key) -> None:
    """`--source nhn` 이 `nhn-capacity.json`을 쓰는가 — 이름이 어긋나면 도구가 못 찾는다."""
    provider = PROVIDERS[key]["provider"]
    assert DEFAULT_OUTPUTS[key].name == f"{provider}-capacity.json"


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_source_is_pinned(key) -> None:
    from kbcommon.sources import SOURCES

    source = SOURCES[PROVIDERS[key]["source"]]
    assert source.pin and source.pin_kind in ("tag", "commit", "digest", "bundled")
