import json

from evaluation.research_protocol.commands.run_capacity_recommendation import run


def test_protocol_connects_measurement_to_selection_and_preserves_deferral(tmp_path):
    measurement = tmp_path / "measurement.json"
    cases = tmp_path / "cases.json"
    measurement.write_text(json.dumps({
        "measurementKind": "single-development-load-point",
        "sustainableRpsPerInstance": 100,
        "p95LatencyMs": 50,
        "errorRate": 0,
        "p95CpuCores": 1,
        "p99RssBytes": 1024**3,
    }), encoding="utf-8")
    cases.write_text(json.dumps({"cases": [
        {
            "caseId": "selected",
            "target": {
                "targetRps": 80,
                "maxP95LatencyMs": 100,
                "persistentDataRequired": False,
            },
            "resourceSpec": {
                "provider": "aws",
                "region": "ap-northeast-2",
                "monthlyBudgetUSD": 500,
            },
        },
        {
            "caseId": "deferred",
            "target": {
                "targetRps": 80,
                "maxP95LatencyMs": 100,
                "persistentDataRequired": True,
            },
            "resourceSpec": {"provider": "aws", "region": "ap-northeast-2"},
        },
    ]}), encoding="utf-8")

    result = run(measurement, cases)

    assert result["cases"][0]["status"] == "selected"
    assert result["cases"][1]["status"] == "deferred"
    assert result["cases"][1]["vmSelection"] is None
