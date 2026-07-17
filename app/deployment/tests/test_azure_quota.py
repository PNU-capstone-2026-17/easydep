"""Azure 서비스 쿼터(마크다운 표) 파서 테스트.

fixture는 실제 azure-docs `includes/*-limits.md`에서 발췌한 verbatim 행들이다
(특이 행 포함: 비수치 값, 각주, 링크, 콤마).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capacitykb.model import Quota
from capacitykb.parsers.azure_quota import parse_markdown

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "capacity" / "azure-limits"


def load(name: str) -> list[Quota]:
    text = (FIXTURE_DIR / name).read_text(encoding="utf-8")
    return parse_markdown(text, name)


@pytest.fixture(scope="module")
def vnet() -> list[Quota]:
    return load("azure-virtual-network-limits.md")


@pytest.fixture(scope="module")
def subscription() -> list[Quota]:
    return load("azure-subscription-limits.md")


def by_name(quotas: list[Quota], name: str) -> Quota | None:
    return next((q for q in quotas if q.name == name), None)


def test_golden_subnets_per_vnet(vnet: list[Quota]) -> None:
    """골든: | Subnets per virtual network |3,000 |."""
    found = by_name(vnet, "Subnets per virtual network")
    assert found.default == 3000
    assert found.scope == "virtual network"
    assert found.provider == "azure"
    assert found.evidence == "azure-limits-doc"
    assert found.confidence == 0.9
    assert found.source_doc == "azure-virtual-network-limits.md"


def test_comma_normalisation(vnet: list[Quota]) -> None:
    assert by_name(vnet, "Virtual networks").default == 1000
    assert by_name(vnet, "Network interface cards").default == 65536


def test_two_column_table_has_no_maximum(vnet: list[Quota]) -> None:
    assert by_name(vnet, "Virtual networks").maximum is None


def test_three_column_table_splits_default_and_maximum(subscription: list[Quota]) -> None:
    found = by_name(subscription, "vCPUs per subscription")
    assert found.default == 20
    assert found.maximum == 10000
    assert found.scope == "subscription"


def test_markdown_links_stripped_from_name(subscription: list[Quota]) -> None:
    """'| [Storage accounts](../articles/...) per subscription<sup>2</sup> |100 |100 |'."""
    found = by_name(subscription, "Storage accounts per subscription")
    assert found is not None
    assert found.default == 100
    assert "http" not in found.name and "[" not in found.name


def test_link_target_with_nested_parentheses(subscription: list[Quota]) -> None:
    """'[Local networks](/previous-versions/.../jj157100(v=azure.100)) per subscription'
    처럼 URL 안에 괄호가 있어도 이름에 ')'가 새어나오면 안 된다."""
    assert by_name(subscription, "Local networks per subscription").default == 10
    assert not any(")" in q.name and "(" not in q.name for q in subscription)


def test_footnote_lowers_confidence_and_notes(subscription: list[Quota]) -> None:
    """각주가 붙은 값은 조건에 따라 달라질 수 있다 (EA vs 종량제 등)."""
    found = by_name(subscription, "vCPUs per subscription")
    assert found.confidence == 0.7
    assert "각주" in found.note
    # 각주 없는 행은 0.9
    assert by_name(subscription, "DNS servers per subscription").confidence == 0.9


def test_non_numeric_values_preserved_as_string(vnet: list[Quota]) -> None:
    found = by_name(vnet, "Public IP addresses")
    assert found.default == 10
    assert found.maximum == "Contact support"
    assert found.confidence == 0.7  # 비수치 값 포함

    prefix = by_name(vnet, "Public IP prefix length")
    assert prefix.default == "/28"

    expression = by_name(vnet, "Private IP addresses per virtual machine")
    assert expression.default == "256 * N (N is number of NICs on VM)"


def test_number_with_trailing_text_stays_string(vnet: list[Quota]) -> None:
    """'500,000, up to 1,000,000 for two or more NICs.' 를 500000으로 읽으면 안 된다."""
    found = by_name(vnet, "Concurrent TCP or UDP flows per NIC of a virtual machine or role instance")
    assert isinstance(found.default, str)
    assert found.default.startswith("500,000")


def test_type_id_curation_link(vnet: list[Quota]) -> None:
    assert (
        by_name(vnet, "Subnets per virtual network").type_id
        == "azure::Microsoft.Network/virtualNetworks/subnets"
    )
    assert by_name(vnet, "Virtual networks").type_id == "azure::Microsoft.Network/virtualNetworks"
    # 큐레이션에 없는 쿼터는 null로 남는다 (이름 검색으로만 조회)
    assert by_name(vnet, "Private IP addresses per virtual machine").type_id is None


def test_scope_extraction(vnet: list[Quota]) -> None:
    assert by_name(vnet, "NSG rules per NSG").scope == "NSG"
    assert by_name(vnet, "Virtual networks").scope is None  # "per X"가 없으면 null


def test_headers_and_separators_not_records(vnet: list[Quota]) -> None:
    names = {q.name for q in vnet}
    assert "Resource" not in names
    assert not any(set(n) <= {"-", " "} for n in names)


def test_prose_lines_ignored(vnet: list[Quota]) -> None:
    """표 밖의 산문/노트/각주 줄은 레코드가 되지 않는다."""
    assert len(vnet) == 10  # 2열 표 7행 + 3열 표 3행
    assert all(q.default is not None or q.maximum is not None for q in vnet)
