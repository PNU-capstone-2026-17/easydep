from app.core.cloudkb.depkb.neutral_sources import source_registry


def test_neutral_crosscheck_registry_uses_pinned_primary_sources():
    sources = source_registry()

    assert {source["model"] for source in sources.values()} == {
        "cloud-barista",
        "tosca",
        "occi",
    }
    cloud_barista = sources["cloud-barista.cb-tumblebug.c2c4e76"]
    assert cloud_barista["version"].startswith("git:")
    assert "github.com/cloud-barista" in cloud_barista["url"]
