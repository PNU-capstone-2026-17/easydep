from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.checkpoint_e2e import catalog
from evaluation.checkpoint_e2e.catalog import CHECKPOINTS, digest, write_json
from evaluation.checkpoint_e2e.evidence import semantic_signature, validate_state
from evaluation.checkpoint_e2e.graph import (
    generate_candidate,
    promote_candidate,
    run_all,
    run_one,
    validate_candidate,
)
from evaluation.checkpoint_e2e.transitions import run_transition
from evaluation.easydep.requirements.evaluate import (
    preclassified_errors,
    requirements_semantic_oracle,
)


class _FakeGraph:
    def __init__(self, node: str, delta: dict):
        self.node = node
        self.delta = delta
        self.calls = []

    def stream(self, state, stream_mode):
        self.calls.append((dict(state), stream_mode))
        yield {self.node: self.delta}


def test_checkpoint_order_has_only_adjacent_successors() -> None:
    assert len(CHECKPOINTS) == 10
    assert catalog.checkpoint_after("erd") == "deployment_diagram"
    with pytest.raises(ValueError, match="no successor"):
        catalog.checkpoint_after("deployment_diagram")


def test_e1_harness_case_adds_explicit_deployment_contract() -> None:
    case = catalog.case_definition("e1-aws")

    requirements = "\n".join(case["requirements"])
    facts = case["deploymentPlanningFacts"]
    assert "generated application" not in requirements
    assert any(item["id"] == "e1-generated-application" for item in facts)
    assert any(item["value"].get("image") == "postgres:16.4-bookworm" for item in facts)


def test_design_transition_calls_only_selected_generation_graph(monkeypatch) -> None:
    selected = _FakeGraph("extract_deployment_diagram", {"deployment_diagram_bundle": {"ok": True}})
    forbidden = _FakeGraph("extract_class_diagram", {"unexpected": True})
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.transitions.DESIGN_SUBGRAPHS",
        {
            "deployment_diagram": {"generate": selected},
            "class_diagram": {"generate": forbidden},
        },
    )
    records = []

    target, state = run_transition(
        "erd",
        {"erd_puml": "@startuml\n@enduml"},
        lambda *args: records.append(args),
    )

    assert target == "deployment_diagram"
    assert state["deployment_diagram_bundle"] == {"ok": True}
    assert len(selected.calls) == 1
    assert forbidden.calls == []
    assert records[0][0] == "deployment_diagram.extract_deployment_diagram"


def test_requirements_oracle_is_order_independent_but_keeps_stable_identity() -> None:
    first = [
        {"id": "one", "type": "FR", "text": "A member starts a workflow."},
        {"id": "two", "type": "NFR", "text": "The workflow preserves state."},
    ]
    second = list(reversed(first))

    assert requirements_semantic_oracle(first) == requirements_semantic_oracle(second)
    assert semantic_signature("requirements", {"classified": first}) == semantic_signature(
        "requirements", {"classified": second}
    )
    changed = [{**first[0], "id": "different"}, first[1]]
    assert requirements_semantic_oracle(first) != requirements_semantic_oracle(changed)


def test_classified_checkpoint_validates_without_running_downstream_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.evidence.blocking_issues",
        lambda *_args, **_kwargs: pytest.fail("requirements checkpoint must not run design gate"),
    )
    state = {
        "classified": [{"id": "R1", "type": "FR", "text": "A user completes a task."}],
        "capability_contract": {"schemaVersion": "test"},
        "resource_spec": {"schemaVersion": "test"},
    }

    report = validate_state("requirements", state)

    assert report["status"] == "passed"
    assert not report["warnings"]


def test_design_transition_does_not_generate_after_blocking_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.evidence.blocking_issues",
        lambda *_args, **_kwargs: ["unresolved policy invariant"],
    )
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.transitions.DESIGN_SUBGRAPHS",
        {"class_diagram": {"generate": pytest.fail}},
    )

    with pytest.raises(ValueError, match="blocked"):
        run_transition("usecase_diagram", {"diagram": "@startuml\n@enduml"}, lambda *_: None)


def test_design_checkpoint_consumes_shared_blocking_gate(monkeypatch) -> None:
    calls = []

    def gate(state, *, through):
        calls.append((state, through))
        return ["unresolved invariant"]

    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.evidence.import_module",
        lambda _name: SimpleNamespace(blocking_issues=gate),
    )

    report = validate_state("usecase_diagram", {"diagram": "@startuml\n@enduml"})

    assert report["status"] == "failed"
    assert report["errors"] == ["unresolved invariant"]
    assert calls and calls[0][1] == "relationships"


def test_preclassified_validation_rejects_malformed_checkpoint() -> None:
    errors = preclassified_errors([{"id": "", "type": "unknown", "text": ""}])

    assert errors


def _gold(root: Path, source: str, target: str, source_state: dict, target_state: dict) -> None:
    entries = []
    for checkpoint, state in ((source, source_state), (target, target_state)):
        write_json(root / "snapshots" / checkpoint / "state.json", state)
        signature = semantic_signature(checkpoint, state) if checkpoint != "input" else {"input": "test"}
        write_json(root / "oracles" / f"{checkpoint}.json", {"signature": signature})
        entries.append({"id": checkpoint, "sha256": digest(state)})
    write_json(
        root / "manifest.json",
        {
            "schemaVersion": "easydep-checkpoint-goldset",
            "caseId": "test",
            "checkpoints": entries,
        },
    )


def test_run_one_uses_gold_prefix_and_stops_after_target(tmp_path, monkeypatch) -> None:
    gold = tmp_path / "gold"
    source = {"raw_requirements": ["A requirement"]}
    target = {
        **source,
        "classified": [{"id": "FR1", "type": "FR", "text": "A requirement"}],
        "capability_contract": {"decisions": []},
        "resource_spec": {"schemaVersion": "4"},
    }
    _gold(gold, "input", "requirements", source, target)
    monkeypatch.setitem(
        catalog.CASES,
        "test",
        {"input": tmp_path / "input.json", "gold": gold},
    )
    write_json(
        tmp_path / "input.json",
        {"requirements": ["A requirement"], "scope": {}, "cloudConstraints": ""},
    )
    calls = []

    def fake_transition(source_checkpoint, state, record):
        calls.append((source_checkpoint, dict(state)))
        record("requirements.classify", state, {"classified": target["classified"]}, 0.1)
        return "requirements", target

    monkeypatch.setattr("evaluation.checkpoint_e2e.graph.run_transition", fake_transition)
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.write_outputs",
        lambda checkpoint, state, output: {"files": []},
    )

    result = run_one("test", "input", output_root=tmp_path / "runs", run_id="run")
    job = Path(result["root"]) / "jobs" / "01-input-to-requirements"

    assert calls == [("input", source)]
    assert (job / "tasks" / "01-requirements-classify" / "input.json").is_file()
    assert not (job / "output" / "class.puml").exists()
    assert result["verdict"] == "passed"


def test_gold_digest_mismatch_is_rejected(tmp_path, monkeypatch) -> None:
    gold = tmp_path / "gold"
    _gold(gold, "input", "requirements", {"value": 1}, {"value": 2})
    snapshot = gold / "snapshots" / "input" / "state.json"
    write_json(snapshot, {"value": 3})
    monkeypatch.setitem(
        catalog.CASES,
        "test-bad",
        {"input": tmp_path / "input.json", "gold": gold},
    )
    write_json(tmp_path / "input.json", {"requirements": ["x"], "scope": {}})

    with pytest.raises(ValueError, match="digest mismatch"):
        catalog.load_gold("test-bad", "input")


def test_candidate_validation_rejects_missing_checkpoint_sequence(tmp_path) -> None:
    write_json(
        tmp_path / "manifest.json",
        {"schemaVersion": "easydep-checkpoint-goldset", "checkpoints": []},
    )
    result = validate_candidate(tmp_path)
    assert result["status"] == "failed"
    assert "checkpoint order differs" in result["errors"][0]


def test_candidate_can_stop_at_an_intermediate_checkpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.CHECKPOINTS",
        ("input", "requirements", "use_cases"),
    )
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.case_definition",
        lambda _case_id: {
            "caseId": "test",
            "requirements": ["A requirement"],
            "resourceConstraintsText": "",
            "initialCloudConstraints": {},
            "deploymentPlanningFacts": [],
            "inputPath": "input.json",
        },
    )
    calls = []

    def transition(source, state, _record):
        calls.append(source)
        target = "requirements" if source == "input" else "use_cases"
        return target, {**state, target: True}

    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.run_transition", transition
    )
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.validate_state",
        lambda _checkpoint, _state: {"status": "passed", "errors": []},
    )

    manifest = generate_candidate(
        "test", tmp_path / "candidate", through="requirements"
    )

    assert calls == ["input"]
    assert manifest["status"] == "in_progress"
    assert [item["id"] for item in manifest["checkpoints"]] == [
        "input",
        "requirements",
    ]


def test_run_all_resume_reuses_matching_completed_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.CHECKPOINTS", ("input", "requirements")
    )
    root = tmp_path / "run-id"
    manifest = {
        "caseId": "test",
        "sourceCheckpoint": "input",
        "targetCheckpoint": "requirements",
        "verdict": "passed",
    }
    write_json(root / "jobs" / "01-input-to-requirements" / "manifest.json", manifest)
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.run_one",
        lambda *args, **kwargs: pytest.fail("completed job should not be rerun"),
    )

    result = run_all("test", output_root=tmp_path, run_id="run-id", resume=True)

    assert result["verdict"] == "passed"
    assert result["jobs"] == [manifest]


def test_promote_copies_only_canonical_gold_artifacts(tmp_path, monkeypatch) -> None:
    candidate = tmp_path / "candidate"
    target = tmp_path / "gold"
    write_json(candidate / "manifest.json", {"status": "complete"})
    write_json(candidate / "snapshots" / "input" / "state.json", {"ok": True})
    write_json(candidate / "oracles" / "input.json", {"signature": {}})
    write_json(candidate / "failures" / "deployment" / "state.json", {"bad": True})
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.validate_candidate",
        lambda path: {"status": "passed", "errors": []},
    )
    monkeypatch.setattr(
        "evaluation.checkpoint_e2e.graph.case_definition",
        lambda case_id: {"goldPath": target},
    )

    promote_candidate(candidate, "test")

    assert (target / "snapshots" / "input" / "state.json").is_file()
    assert not (target / "failures").exists()
