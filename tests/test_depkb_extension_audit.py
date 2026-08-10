from app.core.cloudkb.depkb.neutral_candidates.extension_audit import (
    make_extension_sample,
    validate_extension_audit,
)


def test_extension_sample_is_deterministic_stratified_and_excludes_mapped_nodes():
    inventory = {
        "provider": "aws",
        "source": {"identity": "test", "version": "1"},
        "elements": [
            {
                "nativeId": f"AWS::EC2::{name}",
                "nativeForm": form,
                "sourceLocator": f"test#/{name}",
            }
            for name, form in (
                ("Instance", "standaloneResource"),
                ("Volume", "standaloneResource"),
                ("Attachment", "childResource"),
            )
        ],
    }
    projection = {"mappings": [{"nativeIds": ["AWS::EC2::Instance"]}]}

    first = make_extension_sample(inventory, projection, limit=2)
    second = make_extension_sample(inventory, projection, limit=2)

    assert first == second
    assert {item["nativeId"] for item in first["samples"]} == {
        "AWS::EC2::Volume",
        "AWS::EC2::Attachment",
    }
    validate_extension_audit(first, require_complete=False)
