from __future__ import annotations

import json
from pathlib import Path

from evaluation.easydep.cloud_resources.score_selection import score, verify_snapshots
from evaluation.easydep.cloud_resources.select import run


def test_full_vm_selection_meets_frozen_oracle(tmp_path):
    run_dir = run("select-aws-steady-small", "full", tmp_path)

    result = score(run_dir)

    assert result["passed"] is True
    assert result["score"] == 1.0
    assert result["checks"]["capacitySatisfied"] is True
    assert result["checks"]["computeBudgetSatisfied"] is True
    assert result["checks"]["steadyPerformanceSuitable"] is True
    assert all(item["matched"] for item in result["knowledgeSnapshots"].values())


def test_vm_knowledge_ablation_is_not_credited_as_a_selection(tmp_path):
    run_dir = run("select-aws-steady-small", "no-vm-knowledge", tmp_path)

    result = score(run_dir)

    assert result["passed"] is False
    assert result["checks"]["statusCorrect"] is False
    assert result["checks"]["recommendationCorrect"] is False


def test_missing_capacity_requires_explicit_deferral(tmp_path):
    run_dir = run("defer-missing-capacity", "full", tmp_path)

    result = score(run_dir)
    selection = json.loads((run_dir / "vm-selection.json").read_text(encoding="utf-8"))

    assert result["passed"] is True
    assert selection["reason"] == "missing_capacity_floor"


def test_selection_oracle_is_bound_to_the_actual_frozen_snapshots():
    oracle = json.loads(Path(
        "evaluation/easydep/cloud_resources/selection_oracle.json"
    ).read_text(encoding="utf-8"))

    checks = verify_snapshots(oracle)

    assert checks["costSnapshotSha256"]["matched"] is True
    assert checks["performanceSnapshotSha256"]["matched"] is True
