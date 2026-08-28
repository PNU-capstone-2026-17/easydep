from app.cloudkb import regions


def test_region_name_with_generic_suffix_resolves_from_bundled_catalog() -> None:
    matches = regions.resolve("Seoul region", provider="aws")

    assert [match.code for match in matches] == ["ap-northeast-2"]


def test_cloud_region_suffix_does_not_change_an_exact_region_code() -> None:
    matches = regions.resolve("ap-northeast-2", provider="aws")

    assert [match.code for match in matches] == ["ap-northeast-2"]
