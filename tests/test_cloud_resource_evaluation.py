import json

from evaluation.easydep.cloud_resources.run import run
from evaluation.easydep.cloud_resources.score import score


def test_full_and_ablation_arms_are_executable_and_distinct(tmp_path):
    full_dir = run("azure-docker-vm", "full", tmp_path)
    ablated_dir = run("azure-docker-vm", "no-cloud-kb", tmp_path)

    full = score(full_dir)
    ablated = score(ablated_dir)

    assert full["nodes"]["recall"] == 1.0
    assert full["edges"]["recall"] == 1.0
    assert ablated["nodes"]["recall"] < full["nodes"]["recall"]
    assert ablated["edges"]["recall"] == 0.0
    assert json.loads((ablated_dir / "cloud-plan.json").read_text(encoding="utf-8"))[
        "kbDisabled"
    ] is True
