"""Azure 서비스 쿼터(마크다운 표) 파서 테스트.

fixture는 실제 azure-docs `includes/*-limits.md`에서 발췌한 verbatim 행들이다
(특이 행 포함: 비수치 값, 각주, 링크, 콤마).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.deployment.capacitykb.model import Quota
from app.deployment.capacitykb.parsers.azure_quota import parse_markdown

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
    assert found.basis == "stated"
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


def test_footnote_switches_label_and_notes(subscription: list[Quota]) -> None:
    """각주가 붙은 값은 조건에 따라 달라질 수 있다 (EA vs 종량제 등)."""
    found = by_name(subscription, "vCPUs per subscription")
    assert found.basis == "inferred"
    assert "footnote" in found.note
    # 각주 없는 행은 0.9
    assert by_name(subscription, "DNS servers per subscription").basis == "stated"


def test_non_numeric_values_preserved_as_string(vnet: list[Quota]) -> None:
    found = by_name(vnet, "Public IP addresses")
    assert found.default == 10
    assert found.maximum == "Contact support"
    assert found.basis == "inferred"  # 비수치 값 포함

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

# --- 전체 limits 문서를 읽는다 (D4) ---


def test_default_reads_every_limits_doc() -> None:
    """손으로 고른 목록을 없앴다.

    예전엔 2개만 읽었고 그 둘을 고른 이유가 "코어 리소스를 덮는다"는 우리 판단뿐이라,
    나머지 80개 문서의 쿼터는 물어봐도 "없음"이 나왔다.
    """
    from app.deployment.capacitykb.parsers.azure_quota import DEFAULT_INCLUDES
    assert DEFAULT_INCLUDES == (), "기본은 전체여야 한다"


def test_coverage_states_that_only_azure_has_quotas(tmp_path) -> None:
    """'쿼터가 없다'와 '그 클라우드는 아예 못 받는다'는 다르다.

    AWS Service Quotas와 GCP Cloud Quotas는 자격증명이 필요해 이 빌드에서 못 받는다.
    그 사실이 산출물에 적혀 있어야 사용자가 침묵을 오해하지 않는다.
    """
    from app.deployment.capacitykb.model import CapacitySet, Quota
    capacity = CapacitySet()
    capacity.add_quota(Quota(provider="azure", name="X", source_doc="d.md",
                             evidence="azure-limits-doc"))
    capacity.coverage = [{"provider": "azure", "types": 0,
                          "note": "쿼터는 Azure만 수록한다 — AWS/GCP는 자격증명이 필요하다."}]
    capacity.save(tmp_path / "azure-quota.json")
    loaded = CapacitySet.load(tmp_path / "azure-quota.json")
    assert "자격증명" in loaded.coverage[0]["note"]


def test_empty_include_list_does_not_silently_read_nothing(tmp_path) -> None:
    """목록을 안 주면 **전체**를 읽는다 — 빈 목록으로 조용히 성공하면 안 된다.

    로컬 디렉터리를 넘기는 경로에서 실제로 이 버그가 났고, CLI 테스트가 잡았다.
    """
    from app.deployment.capacitykb.parsers.azure_quota import build

    doc = tmp_path / "docs"
    doc.mkdir()
    (doc / "sample-limits.md").write_text(
        "| Resource | Limit |\n|---|---|\n| Widgets per thing | 42 |\n", encoding="utf-8"
    )
    got = build(tmp_path / "out.json", base_url=str(doc))
    assert [q.name for q in got.quotas] == ["Widgets per thing"]
