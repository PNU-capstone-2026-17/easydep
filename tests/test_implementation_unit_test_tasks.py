from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.implementation.planning.design_context import (
    TaskSpec,
    generate_backend_unit_test_tasks,
    generate_frontend_unit_test_tasks,
)


def _subject(run: Path, *, task_type: str, source: str) -> TaskSpec:
    context_path = run / "reports" / f"{task_type}.context.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(
        json.dumps(
            {
                "readSourcePaths": ["reports/admitted-contract.json"],
                "designInputs": {"requirements": "design/requirements.json"},
            }
        ),
        encoding="utf-8",
    )
    return TaskSpec(
        task_id=f"implement-{task_type}-subject",
        control="subject implementation",
        prompt_file="reports/subject.prompt.md",
        context_file=context_path.relative_to(run).as_posix(),
        allowed_write_paths=[source],
        required_output_paths=[source],
        immutable_paths=["application/src/main/java/com/example/Api.java"],
        source_artifacts={},
        prompt_sha256="subject",
        llm={"model": "glm"},
        owner="frontend" if task_type == "frontend-implementation" else "backend",
        task_type=task_type,
        requirement_ids=["REQ-1"],
        use_case_ids=["UC-1"],
        source_refs=["api:getOrder"],
    )


def test_backend_unit_test_task_is_subject_dependent_and_test_file_only(
    tmp_path: Path, monkeypatch
) -> None:
    from app.implementation.planning import design_context

    monkeypatch.setattr(design_context, "llm_config", lambda _spec: {"model": "glm"})
    run = tmp_path / "run"
    run.mkdir()
    subject = _subject(
        run,
        task_type="backend-implementation",
        source="application/src/main/java/com/example/orders/control/RegistrationControl.java",
    )

    task = generate_backend_unit_test_tasks(
        SimpleNamespace(name="orders"), run, [subject]
    )[0]

    assert task.task_type == "backend-unit-test"
    assert task.owner == "backend"
    assert task.owner_tool_mode == "editor"
    assert task.depends_on == [subject.task_id]
    assert task.allowed_write_paths == task.required_output_paths == [
        "application/src/test/java/com/example/orders/control/RegistrationControlTest.java"
    ]
    assert task.required_test_paths == task.required_output_paths
    assert task.verification_profile == {
        "unitTestSubjectPaths": [
            "application/src/main/java/com/example/orders/control/RegistrationControl.java"
        ],
        "unitTestClass": "com.example.orders.control.RegistrationControlTest",
    }
    assert (
        "application/src/main/java/com/example/orders/control/RegistrationControl.java"
        in task.immutable_paths
    )
    prompt = (run / task.prompt_file).read_text(encoding="utf-8")
    assert "Do not mirror implementation branches" in prompt


def test_frontend_unit_test_task_is_subject_dependent_and_test_file_only(
    tmp_path: Path, monkeypatch
) -> None:
    from app.implementation.planning import design_context

    monkeypatch.setattr(design_context, "llm_config", lambda _spec: {"model": "glm"})
    run = tmp_path / "run"
    run.mkdir()
    subject = _subject(
        run,
        task_type="frontend-implementation",
        source="application/frontend/src/features/view-registrations.tsx",
    )

    task = generate_frontend_unit_test_tasks(
        SimpleNamespace(name="orders"), run, [subject]
    )[0]

    assert task.task_type == "frontend-unit-test"
    assert task.owner == "frontend"
    assert task.owner_tool_mode == "editor"
    assert task.depends_on == [subject.task_id]
    assert task.allowed_write_paths == task.required_output_paths == [
        "application/frontend/src/features/view-registrations.test.tsx"
    ]
    assert task.required_test_paths == task.required_output_paths
    assert task.verification_profile == {
        "unitTestSubjectPaths": [
            "application/frontend/src/features/view-registrations.tsx"
        ]
    }
    assert "Do not mirror implementation branches" in (
        run / task.prompt_file
    ).read_text(encoding="utf-8")


def test_planners_persist_unit_tasks_and_pass_them_to_integration(
    tmp_path: Path, monkeypatch
) -> None:
    import app.implementation.generation.orchestrator as orchestrator
    from app.implementation.planning import design_context

    monkeypatch.setattr(design_context, "llm_config", lambda _spec: {"model": "glm"})
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    manifest_path = reports / "run-manifest.json"
    manifest_path.write_text(json.dumps({"implementation_tasks": []}), encoding="utf-8")
    backend = _subject(
        run,
        task_type="backend-implementation",
        source="application/src/main/java/com/example/orders/RegistrationControl.java",
    )
    frontend = _subject(
        run,
        task_type="frontend-implementation",
        source="application/frontend/src/features/registrations.tsx",
    )
    monkeypatch.setattr(orchestrator, "generate_backend_owner_tasks", lambda *_: [backend])
    monkeypatch.setattr(orchestrator, "generate_frontend_tasks", lambda *_: [frontend])
    captured_prior: list[dict[str, object]] = []

    def integration(_spec, _run, prior):
        captured_prior.extend(prior)
        return TaskSpec(
            task_id="integration",
            control="integration",
            prompt_file="reports/integration.prompt.md",
            context_file="reports/integration.context.json",
            allowed_write_paths=[],
            required_output_paths=[],
            immutable_paths=[],
            source_artifacts={},
            prompt_sha256="integration",
            llm={"model": "glm"},
            owner="implementation",
            task_type="integration-implementation",
        )

    monkeypatch.setattr(orchestrator, "generate_vertical_integration_task", integration)
    spec = SimpleNamespace(name="orders")

    orchestrator.plan_backend_owner_task(spec, run)
    orchestrator.plan_frontend_tasks(spec, run)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = manifest["implementation_tasks"]
    types = {task["task_type"] for task in tasks}
    assert {
        "backend-implementation",
        "backend-unit-test",
        "frontend-implementation",
        "frontend-unit-test",
        "integration-implementation",
    } <= types
    assert {task["task_type"] for task in captured_prior} >= {
        "backend-unit-test",
        "frontend-unit-test",
    }
    backend_test = next(task for task in tasks if task["task_type"] == "backend-unit-test")
    frontend_test = next(task for task in tasks if task["task_type"] == "frontend-unit-test")
    assert backend_test["depends_on"] == [backend.task_id]
    assert frontend_test["depends_on"] == [frontend.task_id]
