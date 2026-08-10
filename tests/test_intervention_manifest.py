from evaluation.research_protocol.commands.build_intervention_manifest import build


def test_only_unresolved_and_eligible_necessity_becomes_an_intervention():
    manifest = build()
    assert [case["claimId"] for case in manifest["cases"]] == [
        "gcp.backend-service-backend-group.necessity"
    ]
    case = manifest["cases"][0]
    assert case["functionalOracle"]["independentFromProvisioning"] is True
    assert case["replications"] == 3
    assert case["status"] == "completed-confirmed"
    assert len(case["resultSha256"]) == 64
