from app.cloudkb import regions


def test_region_name_with_generic_suffix_resolves_from_bundled_catalog() -> None:
    matches = regions.resolve("Seoul region", provider="aws")

    assert [match.code for match in matches] == ["ap-northeast-2"]


def test_cloud_region_suffix_does_not_change_an_exact_region_code() -> None:
    matches = regions.resolve("ap-northeast-2", provider="aws")

    assert [match.code for match in matches] == ["ap-northeast-2"]


def test_formatted_provider_region_label_requires_matching_display_name_and_code() -> None:
    matches = regions.resolve("  east us  ( EASTUS ) ", provider="azure")

    assert [match.code for match in matches] == ["eastus"]


def test_formatted_provider_prefixed_region_label_resolves_when_description_and_code_agree() -> None:
    matches = regions.resolve(
        "AWS Seoul Region (ap-northeast-2)", provider="aws"
    )

    assert [match.code for match in matches] == ["ap-northeast-2"]


def test_formatted_label_preserves_parentheses_in_catalog_display_name() -> None:
    matches = regions.resolve(
        "AWS South Korea (Seoul) Cloud Region (ap-northeast-2)", provider="aws"
    )

    assert [match.code for match in matches] == ["ap-northeast-2"]


def test_formatted_provider_region_label_does_not_fall_back_to_partial_name() -> None:
    assert regions.resolve("East US (eastus2)", provider="azure") == ()
    assert regions.resolve("East US (eastus)", provider="aws") == ()
    assert regions.resolve("East US (unknown-code)", provider="azure") == ()


def test_exact_display_name_precedes_partial_name_search() -> None:
    assert [match.code for match in regions.resolve("East US", provider="azure")] == ["eastus"]
    assert [match.code for match in regions.resolve("East US region", provider="azure")] == [
        "eastus"
    ]


def test_short_partial_display_name_remains_ambiguous() -> None:
    assert len(regions.resolve("US", provider="azure")) > 1
