import json
from pathlib import Path

import evaluation.research_protocol.commands.run_app_cloud_snapshot_repairs as subject
from app.core.orchestration.contracts import ProviderKind, StepResult, StepStatus


def _write(root: Path, name: str, content: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class FakeLogicRepair:
    def run(self, payload, context):  # noqa: ARG002
        application = Path(payload["run_root"]) / "application"
        build = application / "build.gradle"
        build.write_text(
            build.read_text(encoding="utf-8")
            + "\ndependencies { implementation 'org.springframework.boot:spring-boot-starter-data-jpa' }\n",
            encoding="utf-8",
        )
        return StepResult(
            step="implementation.logic",
            provider=ProviderKind.LLM,
            status=StepStatus.COMPLETED,
            metrics={"llm_calls": 1},
        )


class FakeTestingAdapter:
    def __init__(self, timeout_seconds):  # noqa: ARG002
        pass

    def run(self, *, implementation_result, case_id):  # noqa: ARG002
        return {"passed": True, "unitTests": {"status": "passed"}}


def test_logic_repair_uses_only_owner_subtask_and_records_changed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    monkeypatch.setattr(subject, "TestingAdapter", FakeTestingAdapter)
    source = tmp_path / "source-run" / "03-implementation" / "application"
    _write(source, "src/main/java/example/App.java", "package example; class App {}")
    _write(source, "build.gradle", "plugins { id 'java' }\ndependencies {}\n")
    _write(tmp_path / "source-run", "manifest.json", json.dumps({"appId": "fixture-app"}))
    context = tmp_path / "context"
    _write(
        context,
        "01-requirements/result.json",
        json.dumps({"data": {"member_result": {"deployment_needs": {}}}}),
    )
    _write(
        context,
        "02-design/result.json",
        json.dumps({"data": {"design_result": {}, "cloud_design_result": {}}}),
    )
    case = {
        "id": "dependency",
        "group": "build-runtime-dependency",
        "sourceApplication": "source-run/03-implementation/application",
        "contextRun": "context",
        "boundary": "application",
        "expectedDiagnostic": "APP-DEP-001",
        "expectedRepairOwner": "implementation.logic",
        "repairMode": "logic",
        "mutations": [
            {
                "operation": "write",
                "path": "src/main/java/example/Entity.java",
                "content": "package example; import jakarta.persistence.Entity; @Entity class E {}",
            }
        ],
    }

    result = subject.run_case(case, logic_provider=FakeLogicRepair())

    assert result["stepStatus"] == "completed"
    assert result["diagnosticResolved"] is True
    assert result["applicationTestsPassed"] is True
    assert result["upstreamStagesExecuted"] == []
    assert result["executedSubtasks"] == ["implementation.logic"]
    assert result["changedFiles"]["modified"] == ["build.gradle"]


def test_successful_candidate_can_be_retained_by_content_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    monkeypatch.setattr(subject, "TestingAdapter", FakeTestingAdapter)
    source = tmp_path / "source-run/03-implementation/application"
    _write(source, "src/main/java/example/App.java", "package example; class App {}")
    _write(source, "build.gradle", "plugins { id 'java' }\ndependencies {}\n")
    _write(tmp_path / "source-run", "manifest.json", json.dumps({"appId": "fixture-app"}))
    _write(
        tmp_path / "context",
        "01-requirements/result.json",
        json.dumps({"data": {"member_result": {"deployment_needs": {}}}}),
    )
    _write(
        tmp_path / "context",
        "02-design/result.json",
        json.dumps({"data": {"design_result": {}, "cloud_design_result": {}}}),
    )
    case = {
        "id": "dependency",
        "group": "build-runtime-dependency",
        "sourceApplication": "source-run/03-implementation/application",
        "contextRun": "context",
        "boundary": "application",
        "expectedDiagnostic": "APP-DEP-001",
        "expectedRepairOwner": "implementation.logic",
        "repairMode": "logic",
        "mutations": [{
            "operation": "write",
            "path": "src/main/java/example/Entity.java",
            "content": "package example; import jakarta.persistence.Entity; @Entity class E {}",
        }],
    }

    result = subject.run_case(
        case,
        logic_provider=FakeLogicRepair(),
        retain_root=tmp_path / ".easydep/research-candidates",
    )

    retained = result["retainedCandidate"]
    destination = tmp_path / retained["path"]
    assert destination.is_dir()
    assert destination.parent.name.endswith(retained["sha256"][:12])
    assert retained["sha256"] == result["outputSha256"]


def test_changed_files_distinguishes_added_modified_and_removed():
    assert subject._changed_files(
        {"same": "1", "modified": "old", "removed": "x"},
        {"same": "1", "modified": "new", "added": "y"},
    ) == {
        "added": ["added"],
        "modified": ["modified"],
        "removed": ["removed"],
    }
