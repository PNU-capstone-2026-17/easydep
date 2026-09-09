from app.cloudkb.perfkb.agent_api import recommendation_profile


def test_burst_performance_is_structured_for_vm_selection() -> None:
    profile = recommendation_profile("aws", "t3.medium")

    assert profile["status"] == "warn"
    assert profile["sustainedCpu"] == {
        "value": False,
        "note": "Burstable instance — performance drops to baseline once the CPU credits run out.",
        "evidence": "aws-burstable-field",
        "basis": "stated",
    }
    assert any(
        item["key"] == "ebsBaselineMbps" and item["display"].endswith(" Mbps")
        for item in profile["attributes"]
    )
    network = next(
        item for item in profile["attributes"] if item["key"] == "networkPerformance"
    )
    assert "burst" in network["warning"]
