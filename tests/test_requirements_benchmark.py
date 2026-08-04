import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "evaluation" / "requirements" / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("requirements_benchmark", MODULE_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(benchmark)


def test_frozen_holdout_hashes_are_unchanged():
    assert benchmark.verify_holdout_hashes() == []


def test_score_matches_roles_only_on_traced_requirements(tmp_path):
    manifest = {
        "dataset": "dev_checkout_gateway",
        "metrics": {"llm_calls": 2, "prompt_tokens": 10, "completion_tokens": 20, "wall_seconds": 3},
        "summary": {"coverage": {"coverage_ratio": 1.0}, "spec_issues": {}},
    }
    actors = [
        {"name": "Shopper"}, {"name": "Support Agents"}, {"name": "Payment Gateway"}
    ]
    use_cases = [
        {"name": "Checkout", "primary_actor": "Shopper", "supporting_actors": ["Payment Gateway"],
         "requirement_ids": ["FR3", "FR4"]},
        {"name": "Cancel order", "primary_actor": "Support Agent",
         "supporting_actors": ["Payment Gateway"], "requirement_ids": ["FR6"]},
    ]
    files = {
        "manifest.json": manifest,
        "actors.json": actors,
        "use_cases.json": use_cases,
        "relationships.json": {"associations": []},
    }
    for name, payload in files.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    result = benchmark.score(tmp_path)
    assert result["actorRecall"] == 1.0
    assert result["roleAccuracy"] == 1.0
    assert result["unsupportedActors"] == []
    assert result["totalTokens"] == 30
