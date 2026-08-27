from __future__ import annotations

import json
from pathlib import Path

from app.implementation.workflows.repair import (
    apply_repair_directives,
    repair_rounds,
    schedule_cross_phase_repair,
    schedule_source_conformance_repair,
)


def _write_run(run: Path, tasks: list[dict[str, object]]) -> None:
    reports = run / "reports"
    task_dir = reports / "implementation-tasks"
    task_dir.mkdir(parents=True)
    for task in tasks:
        task_id = str(task["task_id"])
        prompt = task_dir / f"{task_id}.prompt.md"
        prompt.write_text(f"base prompt for {task_id}", encoding="utf-8")
        task.update(
            {
                "prompt_file": prompt.relative_to(run).as_posix(),
                "prompt_sha256": task_id,
                "source_artifacts": {},
            }
        )
        (task_dir / f"{task_id}.task.json").write_text(
            json.dumps(task), encoding="utf-8"
        )
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
    )


def _tasks() -> list[dict[str, object]]:
    return [
        {
            "task_id": "implement-repositories",
            "task_type": "persistence-repositories",
            "allowed_write_paths": [
                "application/src/main/java/example/OrderRepository.java"
            ],
        },
        {
            "task_id": "implement-portfolio-api-adapter",
            "task_type": "api-adapter",
            "allowed_write_paths": [
                "application/src/test/java/example/PortfolioApiControllerTest.java"
            ],
        },
        {
            "task_id": "implement-application-wiring",
            "task_type": "configuration",
            "allowed_write_paths": [
                "application/src/main/java/example/Application.java"
            ],
        },
    ]


def test_h2_reserved_keyword_failure_targets_persistence_tasks(tmp_path: Path) -> None:
    tasks = _tasks() + [
        {
            "task_id": "implement-entities",
            "task_type": "persistence-entities",
            "allowed_write_paths": ["application/src/main/java/example/AcademicTermEntity.java"],
        },
        {
            "task_id": "implement-mapping",
            "task_type": "persistence-mapping",
            "allowed_write_paths": ["application/src/main/java/example/Mapper.java"],
        },
        {
            "task_id": "implement-schema",
            "task_type": "persistence-schema",
            "allowed_write_paths": ["application/src/main/resources/db/migration/V1__init.sql"],
        },
        {
            "task_id": "implement-e2e",
            "task_type": "integration-test",
            "allowed_write_paths": ["application/src/test/java/example/FlowTest.java"],
        },
    ]
    _write_run(tmp_path, tasks)

    repair = schedule_cross_phase_repair(
        tmp_path,
        "implement-e2e",
        {"stderr": 'JdbcSQLSyntaxErrorException: expected "identifier" near year'},
    )

    assert repair is not None
    assert repair["ownerTaskIds"] == [
        "implement-entities",
        "implement-mapping",
        "implement-schema",
    ]


def test_e2e_missing_repository_bean_targets_upstream_owners(tmp_path: Path) -> None:
    tasks = _tasks() + [
        {
            "task_id": "implement-e2e",
            "task_type": "integration-test",
            "allowed_write_paths": ["application/src/test/java/example/FlowTest.java"],
        },
    ]
    _write_run(tmp_path, tasks)

    repair = schedule_cross_phase_repair(
        tmp_path,
        "implement-e2e",
        {
            "stderr": (
                "NoSuchBeanDefinitionException: No qualifying bean of type "
                "'example.OrderRepository' available: expected at least 1 bean "
                "which qualifies as autowire candidate"
            )
        },
    )

    assert repair is not None
    assert repair["ownerTaskIds"] == [
        "implement-application-wiring",
        "implement-repositories",
    ]


def test_e2e_http_runtime_failure_targets_application_contract_owners(
    tmp_path: Path,
) -> None:
    tasks = _tasks() + [
        {
            "task_id": "implement-registration-control",
            "task_type": "control",
            "allowed_write_paths": ["application/src/main/java/example/RegistrationControlService.java"],
        },
        {
            "task_id": "implement-registration-boundary",
            "task_type": "boundary-adapter",
            "allowed_write_paths": ["application/src/main/java/example/RegistrationBoundaryAdapter.java"],
        },
        {
            "task_id": "implement-e2e",
            "task_type": "integration-test",
            "allowed_write_paths": ["application/src/test/java/example/FlowTest.java"],
        },
    ]
    _write_run(tmp_path, tasks)

    repair = schedule_cross_phase_repair(
        tmp_path,
        "implement-e2e",
        {
            "testResults": (
                "application/src/test/java/example/PortfolioApiControllerTest.java:42: "
                "AssertionFailedError: expected: <201 CREATED> but was: "
                "<500 INTERNAL_SERVER_ERROR>"
            )
        },
    )

    assert repair is not None
    assert repair["ownerTaskIds"] == [
        "implement-application-wiring",
        "implement-portfolio-api-adapter",
        "implement-registration-boundary",
        "implement-registration-control",
    ]


def test_repair_budget_is_cumulative_across_changed_evidence(tmp_path: Path) -> None:
    _write_run(tmp_path, _tasks())
    for attempt in range(1, 4):
        repair = schedule_cross_phase_repair(
            tmp_path,
            "implement-application-wiring",
            {
                "stderr": (
                    "C:/work/application/src/main/java/example/"
                    f"OrderRepository.java:{attempt}: error: failure {attempt}"
                )
            },
        )
        assert repair is not None
        assert repair["revision"] == attempt

    exhausted = schedule_cross_phase_repair(
        tmp_path,
        "implement-application-wiring",
        {
            "stderr": (
                "C:/work/application/src/main/java/example/"
                "OrderRepository.java:99: error: another failure"
            )
        },
    )
    assert exhausted is None
    plan = json.loads((tmp_path / "reports/repair-plan.json").read_text())
    assert len(plan["entries"]) == 1
    assert repair_rounds(plan) == 3


def test_warning_path_does_not_override_causal_owner(tmp_path: Path) -> None:
    _write_run(tmp_path, _tasks())
    repair = schedule_cross_phase_repair(
        tmp_path,
        "implement-application-wiring",
        {
            "stderr": (
                "warning: application/src/test/java/example/"
                "PortfolioApiControllerTest.java uses unchecked operations\n"
                "NoSuchBeanDefinitionException: repository bean failed"
            )
        },
    )
    assert repair is not None
    assert repair["ownerTaskIds"] == ["implement-repositories"]


def test_schema_type_failure_in_wiring_targets_persistence_owners(tmp_path: Path) -> None:
    tasks = _tasks() + [
        {
            "task_id": "implement-entities",
            "task_type": "persistence-entities",
            "allowed_write_paths": ["application/src/main/java/example/EnrollmentEntity.java"],
        },
        {
            "task_id": "implement-mapping",
            "task_type": "persistence-mapping",
            "allowed_write_paths": ["application/src/main/java/example/Mapper.java"],
        },
        {
            "task_id": "implement-schema",
            "task_type": "persistence-schema",
            "allowed_write_paths": ["application/src/main/resources/db/migration/V1__init.sql"],
        },
    ]
    _write_run(tmp_path, tasks)

    repair = schedule_cross_phase_repair(
        tmp_path,
        "implement-application-wiring",
        {
            "testResults": (
                "SchemaManagementException: Schema-validation: wrong column type "
                "encountered in column [enrollment_date]"
            )
        },
    )

    assert repair is not None
    assert repair["ownerTaskIds"] == [
        "implement-entities", "implement-mapping", "implement-schema"
    ]


def test_repair_prompt_is_idempotent_and_uses_real_bounded_evidence(
    tmp_path: Path,
) -> None:
    tasks = _tasks()
    _write_run(tmp_path, tasks)
    repair = schedule_cross_phase_repair(
        tmp_path,
        "implement-application-wiring",
        {
            "stderr": (
                "C:/work/application/src/main/java/example/"
                "OrderRepository.java:12: error: cannot find symbol"
            )
        },
    )
    assert repair is not None

    apply_repair_directives(tmp_path)
    prompt_path = (
        tmp_path
        / "reports/implementation-tasks/implement-repositories.prompt.md"
    )
    first = prompt_path.read_text(encoding="utf-8")
    apply_repair_directives(tmp_path)
    second = prompt_path.read_text(encoding="utf-8")

    assert first == second
    assert first.count("## Orchestrated repair and revalidation directives") == 1
    assert "OrderRepository.java:12" in first
    assert "{entry['evidence']}" not in first


def test_erd_conformance_failure_targets_persistence_repair(tmp_path: Path) -> None:
    _write_run(tmp_path, _tasks())

    repair = schedule_source_conformance_repair(
        tmp_path,
        {
            "violations": [
                {
                    "code": "ERD_ENTITY_NOT_IMPLEMENTED",
                    "message": "Order.name is missing",
                }
            ]
        },
    )

    assert repair is not None
    assert repair["ownerTaskIds"] == ["implement-repositories"]
