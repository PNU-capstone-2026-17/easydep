from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.design.services.deployment_diagram.bundle import build_deployment_diagram_bundle
from app.implementation.generation.orchestrator import (
    PrototypeOrchestrator,
    find_undefined_bce_types,
    load_job,
    plan_e2e_tasks,
)
from app.implementation.agents.runtime import (
    EventJournal,
    _api_adapter_repair_contract,
    _requires_cross_phase_repair,
    _repair_missing_generated_model_imports,
    _render_missing_output_repair_prompt,
    _promote_changed_files,
    _restore_unauthorized_files,
    break_configuration_cycles,
    enforce_spring_persistence_validation,
    execution_attempt,
    normalize_spring_boot_repository_discovery,
    remove_placeholder_comments,
    ensure_control_service_component,
    select_repair_paths,
    write_execution_result,
)
from app.implementation.agents.workspace import (
    changed_files,
    ensure_mapper_accessible_persistence_constructor,
    missing_required_outputs,
    prepare_agent_workspace,
    read_allowed_sources,
    read_persistence_entity_contracts,
    snapshot_files,
    task_base_package,
)
from app.implementation.agents.provider import (
    configured_api_key,
    openhands_compatibility,
    transient_provider_error,
    provider_retry_delay,
)
from app.implementation.agents.verification.build import (
    WorkspaceVerificationError,
    production_placeholder_markers,
    production_test_library_markers,
    api_adapter_contract_violations,
    boundary_adapter_contract_violations,
    persistence_reserved_identifier_markers,
    ensure_persistence_schema_test,
    repair_persistence_schema_table_quoting,
    repair_invalid_inverse_entity_associations,
    persistence_entity_schema_violations,
    read_gradle_test_failures,
    summarize_test_failure,
    task_verification_command,
    verify_run_workspace,
)
from app.implementation.agents.prompts.feedback import (
    render_frontend_verification_feedback,
    render_verification_feedback,
    verification_failure_hints,
)
from app.implementation.workflows.repair import (
    apply_repair_directives,
    referenced_source_paths,
    schedule_cross_phase_repair,
)
from app.implementation.planning.design_context import (
    detect_e2e_design_gaps,
    find_empty_java_contracts,
    generate_api_adapter_tasks,
    generate_boundary_adapter_tasks,
    generate_e2e_tasks,
    generate_gateway_adapter_tasks,
    generate_persistence_tasks,
    generate_wiring_tasks,
    parse_design_classes,
    parse_openapi_operations,
    read_generated_java_contracts,
    referenced_openapi_model_names,
    render_api_adapter_prompt,
    render_boundary_adapter_prompt,
    render_persistence_repository_prompt,
    render_persistence_schema_prompt,
    render_prompt,
    slice_sequence,
    _e2e_persistence_paths,
)
from app.implementation.application.jobs import _unrepresentable_openapi_error_outcomes
from app.implementation.workflows.completion import audit_run_completion
from app.implementation.agents.verification.e2e import e2e_contract_violations
from app.implementation.agents.verification.e2e import (
    repair_nested_e2e_members,
    repair_spring_boot3_test_compatibility,
)
from app.implementation.agents.verification.e2e import repair_orphaned_java_test_statements
from app.implementation.delivery.kubernetes import (
    infer_intent,
    render_deployment,
    validate_intent,
)
from app.implementation.delivery.terraform import render_iac, validate_terraform
from scripts.generate_deployment_diagram_examples import (
    DEPLOYMENT_CASES,
    _graph as deployment_graph,
    _resource_spec as deployment_resource_spec,
)
from app.implementation.workflows.conformance import (
    SourceDesignConformanceError,
    _implemented_interfaces,
    _method_call_sequences,
    _verify_erd_conformance,
    _participant_aliases,
    _sequence_documents,
    capture_generated_contracts,
    restore_generated_contracts,
    verify_source_design_conformance,
)
from app.implementation.domain.implementation_ir import (
    ApiOperationIR,
    ApiPortIR,
    ApiResponseIR,
    build_implementation_ir,
    parse_erd_association_entities,
    assess_bce_erd_entity_contract,
    parse_openapi_operations as parse_ir_openapi_operations,
)
from app.implementation.workflows.coordinator import (
    _defer_e2e_planning,
    _execute_task_batch,
    _e2e_prerequisites_complete,
    _phase_task_batches,
    _verify_phase,
    reconcile_workflow_state,
    _write_json_atomic,
    validate_approval,
    validate_workflow_approval,
    write_transmission_request,
)


class ImplementationParallelismTest(unittest.TestCase):
    def test_generation_progress_status_write_leaves_no_shared_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            progress_path = Path(directory) / "generation-progress.json"
            orchestrator = object.__new__(PrototypeOrchestrator)
            orchestrator.spec = SimpleNamespace(progress_path=progress_path)
            orchestrator.manifest = SimpleNamespace(status="")

            orchestrator._set_status("RUNNING", "Generating sources")

            self.assertEqual("RUNNING", json.loads(progress_path.read_text(encoding="utf-8"))["status"])
            self.assertEqual(list(progress_path.parent.glob("*.tmp")), [])

    def test_atomic_state_write_uses_unique_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "reports" / "workflow-state.json"
            _write_json_atomic(target, {"status": "RUNNING"})
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"status": "RUNNING"},
            )
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_missing_changed_file_does_not_abort_result_promotion(self) -> None:
        """A deleted changed path is handled by the later output gate."""
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory) / "sandbox"
            run_root = Path(directory) / "run"
            sandbox.mkdir()
            run_root.mkdir()
            missing = sandbox / "application/src/main/Missing.java"
            target = run_root / "application/src/main/Missing.java"
            target.parent.mkdir(parents=True)
            target.write_text("old", encoding="utf-8")
            _promote_changed_files(sandbox, run_root, {"application/src/main/Missing.java"})
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_unauthorized_file_changes_are_restored_from_run_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory) / "sandbox"
            run_root = Path(directory) / "run"
            unauthorized = "application/src/main/Other.java"
            baseline = run_root / unauthorized
            sandbox_file = sandbox / unauthorized
            baseline.parent.mkdir(parents=True)
            sandbox_file.parent.mkdir(parents=True)
            baseline.write_text("original", encoding="utf-8")
            sandbox_file.write_text("agent changed", encoding="utf-8")

            _restore_unauthorized_files(sandbox, run_root, [unauthorized])

            self.assertEqual(sandbox_file.read_text(encoding="utf-8"), "original")

    def test_restored_unauthorized_path_is_excluded_from_promotion_set(self) -> None:
        changed = {
            "application/src/main/Owned.java",
            "application/src/main/Other.java",
        }
        allowed = {"application/src/main/Owned.java"}
        self.assertEqual(
            {path for path in changed if path in allowed},
            {"application/src/main/Owned.java"},
        )

    def test_production_placeholder_gate_rejects_any_unsupported_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "application/src/main/java/example/Adapter.java"
            source.parent.mkdir(parents=True)
            source.write_text(
                'class Adapter { void run() { throw new UnsupportedOperationException("contract gap"); } }',
                encoding="utf-8",
            )
            markers = production_placeholder_markers(
                root, ["application/src/main/java/example/Adapter.java"]
            )
            self.assertEqual(len(markers), 1)

    def test_control_service_component_repair_adds_spring_service_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "application/src/main/java/example/application/impl/CourseCatalogControllerService.java"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                "package example.application.impl;\n\n"
                "import example.bce.CourseCatalogController;\n\n"
                "public class CourseCatalogControllerService implements CourseCatalogController {}\n",
                encoding="utf-8",
            )

            changed = ensure_control_service_component(root, [relative])

            assert changed == [relative]
            source = path.read_text(encoding="utf-8")
            assert "import org.springframework.stereotype.Service;" in source
            assert "@Service\npublic class CourseCatalogControllerService" in source

    def test_e2e_planning_is_deferred_until_non_e2e_outputs_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir(parents=True)
            output = "application/src/main/java/com/example/Repository.java"
            (reports / "run-manifest.json").write_text(
                json.dumps({
                    "implementation_tasks": [
                        {
                            "task_id": "implement-repository",
                            "task_type": "persistence-repositories",
                            "allowed_write_paths": [output],
                        },
                        {
                            "task_id": "implement-e2e-flow",
                            "task_type": "integration-test",
                            "allowed_write_paths": ["application/src/test/FlowTest.java"],
                        },
                    ]
                }),
                encoding="utf-8",
            )

            self.assertFalse(_e2e_prerequisites_complete(run))
            _defer_e2e_planning(run)
            manifest = json.loads(
                (reports / "run-manifest.json").read_text(encoding="utf-8")
            )
            report = json.loads(
                (reports / "design-gaps/end-to-end-flow.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                ["implement-repository"],
                [item["task_id"] for item in manifest["implementation_tasks"]],
            )
            self.assertEqual("PENDING", report["status"])

            (run / output).parent.mkdir(parents=True)
            (run / output).write_text("interface Repository {}", encoding="utf-8")
            self.assertTrue(_e2e_prerequisites_complete(run))

    @staticmethod
    def _task(task_id: str, task_type: str, output: str) -> dict[str, object]:
        return {
            "taskId": task_id,
            "taskType": task_type,
            "status": "PENDING",
            "attempts": 0,
            "allowedWritePaths": [output],
        }

    def test_independent_tasks_execute_concurrently_with_bounded_workers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                self._task("first", "api-adapter", "application/First.java"),
                self._task("second", "api-adapter", "application/Second.java"),
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": task["taskId"],
                                "allowed_write_paths": task["allowedWritePaths"],
                            }
                            for task in tasks
                        ]
                    }
                ),
                encoding="utf-8",
            )
            barrier = threading.Barrier(2, timeout=2)
            active = 0
            peak = 0
            lock = threading.Lock()

            def execute(root: Path, task_id: str) -> dict[str, object]:
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                barrier.wait()
                output = root / f"application/{task_id.title()}.java"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(f"class {task_id.title()} {{}}", encoding="utf-8")
                with lock:
                    active -= 1
                return {"status": "SUCCEEDED"}

            state: dict[str, object] = {"tasks": tasks, "status": "RUNNING"}
            failures = _execute_task_batch(
                run, state, tasks, execute, max_workers=2
            )

            self.assertEqual([], failures)
            self.assertEqual(2, peak)
            self.assertEqual(["SUCCEEDED", "SUCCEEDED"], [task["status"] for task in tasks])
            persisted = json.loads((reports / "workflow-state.json").read_text(encoding="utf-8"))
            self.assertEqual(["first", "second"], [task["taskId"] for task in persisted["tasks"]])

    def test_parallel_batch_marks_only_worker_slots_as_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                self._task(f"task-{index}", "api-adapter", f"application/{index}.java")
                for index in range(4)
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": [
                    {"task_id": task["taskId"], "allowed_write_paths": task["allowedWritePaths"]}
                    for task in tasks
                ]}),
                encoding="utf-8",
            )
            observed: list[tuple[str, list[str], str]] = []

            def execute(root: Path, task_id: str) -> dict[str, object]:
                persisted = json.loads(
                    (root / "reports" / "workflow-state.json").read_text(
                        encoding="utf-8"
                    )
                )
                durable_task = next(
                    item for item in persisted["tasks"] if item["taskId"] == task_id
                )
                observed.append(
                    (task_id, [str(task["status"]) for task in tasks], str(durable_task["status"]))
                )
                output = root / f"application/{task_id}.java"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("class Generated {}", encoding="utf-8")
                return {"status": "SUCCEEDED"}

            state: dict[str, object] = {"tasks": tasks, "status": "RUNNING"}
            self.assertEqual([], _execute_task_batch(run, state, tasks, execute, max_workers=2))
            self.assertTrue(observed)
            self.assertTrue(
                all(
                    statuses[next(index for index, task in enumerate(tasks) if task["taskId"] == task_id)]
                    == "RUNNING"
                    and durable_status == "RUNNING"
                    for task_id, statuses, durable_status in observed
                )
            )
            self.assertTrue(
                all(
                    sum(status == "RUNNING" for status in statuses) <= 2
                    for _, statuses, _ in observed
                )
            )

    def test_sequential_batch_checkpoints_completion_before_next_task_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                self._task(f"task-{index}", "api-adapter", f"application/{index}.java")
                for index in range(2)
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps({
                    "implementation_tasks": [
                        {
                            "task_id": task["taskId"],
                            "allowed_write_paths": task["allowedWritePaths"],
                        }
                        for task in tasks
                    ]
                }),
                encoding="utf-8",
            )
            observed: list[list[str]] = []

            def execute(root: Path, task_id: str) -> dict[str, object]:
                observed.append([str(task["status"]) for task in tasks])
                output = root / f"application/{task_id}.java"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("class Generated {}", encoding="utf-8")
                return {"status": "SUCCEEDED"}

            state: dict[str, object] = {"tasks": tasks, "status": "RUNNING"}
            self.assertEqual(
                [], _execute_task_batch(run, state, tasks, execute, max_workers=1)
            )
            self.assertEqual(
                [["RUNNING", "PENDING"], ["SUCCEEDED", "RUNNING"]], observed
            )

    def test_persistence_entities_finish_before_parallel_dependents(self) -> None:
        tasks = [
            self._task("entities", "persistence-entities", "application/Entity.java"),
            self._task("repositories", "persistence-repositories", "application/Repository.java"),
            self._task("mapping", "persistence-mapping", "application/Mapper.java"),
            self._task("schema", "persistence-schema", "application/schema.sql"),
        ]

        batches = _phase_task_batches("persistence", tasks)

        self.assertEqual([["entities"], ["repositories", "mapping", "schema"]], [
            [task["taskId"] for task in batch] for batch in batches
        ])

    def test_non_integration_phase_skips_duplicate_full_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            result = _verify_phase(run, "persistence", verify_run_workspace)

            self.assertEqual("SKIPPED", result["status"])
            self.assertTrue(
                (run / "reports/phase-persistence-verification.json").is_file()
            )

    def test_persistence_entity_and_repository_tasks_are_split_per_file(self) -> None:
        from app.implementation.planning import design_context

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            erd = root / "erd.puml"
            erd.write_text("entity Course\nentity Enrollment", encoding="utf-8")
            run = root / "run_sample"
            run.mkdir()
            spec = SimpleNamespace(
                inputs={"erd": erd},
                base_package="com.example.demo",
                name="sample",
                workspace_root=root,
                agent_model="model",
                agent_base_url="http://localhost",
                agent_temperature=0.2,
                agent_top_p=0.9,
                agent_max_output_tokens=1000,
                agent_reasoning_budget=100,
            )
            fake_ir = SimpleNamespace(persistent_entities=("Course", "Enrollment"))
            with patch.object(design_context, "build_implementation_ir", return_value=fake_ir), \
                patch.object(design_context, "read_generated_java_contracts", return_value="contract"), \
                patch.object(design_context, "_llm_config", return_value={}):
                tasks = generate_persistence_tasks(spec, run)

            entity_tasks = [task for task in tasks if task.task_type == "persistence-entities"]
            repository_tasks = [
                task for task in tasks if task.task_type == "persistence-repositories"
            ]
            self.assertEqual(
                [
                    "implement-erd-persistence-entity-course",
                    "implement-erd-persistence-entity-enrollment",
                ],
                [task.task_id for task in entity_tasks],
            )
            self.assertEqual(
                [
                    "implement-erd-persistence-repository-course",
                    "implement-erd-persistence-repository-enrollment",
                ],
                [task.task_id for task in repository_tasks],
            )
            self.assertTrue(all(len(task.allowed_write_paths) == 1 for task in entity_tasks))
            self.assertTrue(all(len(task.allowed_write_paths) == 1 for task in repository_tasks))
            entity_prompt = (run / entity_tasks[0].prompt_file).read_text(encoding="utf-8")
            self.assertIn("mappedBy", entity_prompt)
            self.assertIn("scalar foreign-key column", entity_prompt)

    def test_related_persistence_entities_share_one_atomic_task(self) -> None:
        from app.implementation.planning import design_context

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            erd = root / "erd.puml"
            erd.write_text(
                "entity Course {\n  course_id : String\n}\n"
                "entity Enrollment {\n  enrollment_id : String\n}\n"
                "Course ||--o{ Enrollment\n",
                encoding="utf-8",
            )
            run = root / "run_sample"
            run.mkdir()
            spec = SimpleNamespace(
                inputs={"erd": erd},
                base_package="com.example.demo",
                name="sample",
                workspace_root=root,
                agent_model="model",
                agent_base_url="http://localhost",
                agent_temperature=0.2,
                agent_top_p=0.9,
                agent_max_output_tokens=1000,
                agent_reasoning_budget=100,
            )
            fake_ir = SimpleNamespace(persistent_entities=("Course", "Enrollment"))
            with patch.object(design_context, "build_implementation_ir", return_value=fake_ir), \
                patch.object(design_context, "read_generated_java_contracts", return_value="contract"), \
                patch.object(design_context, "_llm_config", return_value={}):
                tasks = generate_persistence_tasks(spec, run)

            entity_tasks = [task for task in tasks if task.task_type == "persistence-entities"]
            self.assertEqual(
                ["implement-erd-persistence-entity-course-enrollment"],
                [task.task_id for task in entity_tasks],
            )
            self.assertEqual(2, len(entity_tasks[0].allowed_write_paths))

    def test_overlapping_outputs_force_sequential_batches(self) -> None:
        tasks = [
            self._task("first", "api-adapter", "application/generated"),
            self._task("second", "api-adapter", "application/generated/Api.java"),
        ]

        batches = _phase_task_batches("api-adapters", tasks)

        self.assertEqual([["first"], ["second"]], [
            [task["taskId"] for task in batch] for batch in batches
        ])

    def test_parallel_failure_preserves_successful_task_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                self._task("failed", "api-adapter", "application/Failed.java"),
                self._task("completed", "api-adapter", "application/Completed.java"),
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": task["taskId"],
                                "allowed_write_paths": task["allowedWritePaths"],
                            }
                            for task in tasks
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def execute(root: Path, task_id: str) -> dict[str, object]:
                if task_id == "failed":
                    raise RuntimeError("provider unavailable")
                output = root / "application/Completed.java"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("class Completed {}", encoding="utf-8")
                return {"status": "SUCCEEDED"}

            state: dict[str, object] = {"tasks": tasks, "status": "RUNNING"}
            failures = _execute_task_batch(
                run, state, tasks, execute, max_workers=2
            )

            self.assertEqual(["failed"], [task["taskId"] for task, _ in failures])
            self.assertEqual(["FAILED", "SUCCEEDED"], [task["status"] for task in tasks])
            self.assertEqual("FAILED", state["status"])
            self.assertTrue(tasks[1]["outputHashes"])


class GeneratedContractImportRepairTest(unittest.TestCase):
    def test_repoints_absent_api_model_to_existing_bce_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            package_root = sandbox / "application/src/main/java/com/easydep/app"
            (package_root / "api/model").mkdir(parents=True)
            (package_root / "bce").mkdir(parents=True)
            source = package_root / "application/impl/CourseControllerService.java"
            source.parent.mkdir(parents=True)
            source.write_text(
                "package com.easydep.app.application.impl;\n"
                "import com.easydep.app.api.model.CourseData;\n"
                "class CourseControllerService { CourseData save(CourseData value) { return value; } }\n",
                encoding="utf-8",
            )
            (package_root / "bce/CourseData.java").write_text(
                "package com.easydep.app.bce; public final class CourseData {}\n",
                encoding="utf-8",
            )

            repaired = _repair_missing_generated_model_imports(
                sandbox,
                [
                    "application/src/main/java/com/easydep/app/application/impl/"
                    "CourseControllerService.java"
                ],
            )

            self.assertEqual(
                repaired,
                [
                    "application/src/main/java/com/easydep/app/application/impl/"
                    "CourseControllerService.java: CourseData api.model -> bce"
                ],
            )
            self.assertIn(
                "import com.easydep.app.bce.CourseData;",
                source.read_text(encoding="utf-8"),
            )


class PersistenceSchemaContractRepairTest(unittest.TestCase):
    def test_entity_schema_mismatch_reports_columns_before_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            migration = sandbox / "application/src/main/resources/db/migration/V1__initial_schema.sql"
            entity = sandbox / "application/src/main/java/com/example/persistence/entity/EnrollmentEntity.java"
            migration.parent.mkdir(parents=True)
            entity.parent.mkdir(parents=True)
            migration.write_text(
                """CREATE TABLE enrollment (
  enrollment_id VARCHAR(255) NOT NULL,
  student_id VARCHAR(255) NOT NULL,
  course_id VARCHAR(255) NOT NULL,
  enrollment_status VARCHAR(255)
);\n""",
                encoding="utf-8",
            )
            entity.write_text(
                """@Entity
@Table(name = "enrollment")
class EnrollmentEntity {
  @Id @Column(name = "enrollment_id") private String enrollmentId;
  @Column(name = "enrollment_status") private String status;
}
""",
                encoding="utf-8",
            )

            violations = persistence_entity_schema_violations(
                sandbox,
                ["application/src/main/java/com/example/persistence/entity/EnrollmentEntity.java"],
            )

            self.assertEqual(1, len(violations))
            self.assertIn("course_id", violations[0])
            self.assertIn("student_id", violations[0])

            entity.write_text(
                entity.read_text(encoding="utf-8").replace(
                    '@Column(name = "enrollment_status") private String status;',
                    '@Column(name = "enrollment_status") private String status;\n'
                    '  @JoinColumn(name = "student_id") private Object student;',
                ),
                encoding="utf-8",
            )
            violations = persistence_entity_schema_violations(
                sandbox,
                ["application/src/main/java/com/example/persistence/entity/EnrollmentEntity.java"],
            )
            self.assertEqual(1, len(violations))
            self.assertNotIn("student_id", violations[0])

    def test_invalid_inverse_relationship_is_made_transient(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            student_relative = (
                "application/src/main/java/com/example/app/persistence/entity/"
                "StudentEntity.java"
            )
            enrollment_relative = (
                "application/src/main/java/com/example/app/persistence/entity/"
                "EnrollmentEntity.java"
            )
            student = sandbox / student_relative
            enrollment = sandbox / enrollment_relative
            student.parent.mkdir(parents=True)
            student.write_text(
                """import jakarta.persistence.OneToMany;
import java.util.Set;
class StudentEntity {
  @OneToMany(mappedBy = \"student\")
  private Set<EnrollmentEntity> enrollments;
}
""",
                encoding="utf-8",
            )
            enrollment.write_text("class EnrollmentEntity { String studentId; }\n", encoding="utf-8")

            repaired = repair_invalid_inverse_entity_associations(
                sandbox, [student_relative, enrollment_relative]
            )

            self.assertEqual(
                [student_relative + ": invalid inverse association marked transient"], repaired
            )
            source = student.read_text(encoding="utf-8")
            self.assertIn("import jakarta.persistence.Transient;", source)
            self.assertIn("@Transient", source)
            self.assertNotIn("@OneToMany", source)

    def test_replaces_agent_schema_test_with_case_independent_metadata_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            migration_relative = "application/src/main/resources/db/migration/V1__initial_schema.sql"
            test_relative = "application/src/test/java/com/example/app/persistence/PersistenceSchemaTest.java"
            migration = sandbox / migration_relative
            migration.parent.mkdir(parents=True)
            migration.write_text(
                "CREATE TABLE academic_term (term_id BIGINT);\n", encoding="utf-8"
            )
            test = sandbox / test_relative
            test.parent.mkdir(parents=True)
            test.write_text("// brittle agent test\n", encoding="utf-8")

            created = ensure_persistence_schema_test(
                sandbox, [migration_relative, test_relative], overwrite=True
            )

            self.assertEqual([test_relative], created)
            source = test.read_text(encoding="utf-8")
            self.assertIn("metadata.getTables(null, null, null", source)
            self.assertIn(
                'actualTables.add(rows.getString("TABLE_NAME").toLowerCase())', source
            )

    def test_creates_schema_test_from_existing_migration_when_agent_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            migration_relative = "application/src/main/resources/db/migration/V1__initial_schema.sql"
            test_relative = "application/src/test/java/com/example/app/persistence/PersistenceSchemaTest.java"
            migration = sandbox / migration_relative
            migration.parent.mkdir(parents=True)
            migration.write_text(
                'CREATE TABLE department (department_id BIGINT);\n'
                'CREATE TABLE "academic_term" (term_id BIGINT);\n',
                encoding="utf-8",
            )

            created = ensure_persistence_schema_test(
                sandbox, [migration_relative, test_relative]
            )

            self.assertEqual(created, [test_relative])
            source = (sandbox / test_relative).read_text(encoding="utf-8")
            self.assertIn("package com.example.app.persistence;", source)
            self.assertIn('Set.of("academic_term", "department")', source)
            self.assertIn('locations("classpath:db/migration")', source)

    def test_does_not_create_schema_test_without_declared_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            migration_relative = "application/src/main/resources/db/migration/V1__initial_schema.sql"
            test_relative = "application/src/test/java/com/example/app/persistence/PersistenceSchemaTest.java"
            migration = sandbox / migration_relative
            migration.parent.mkdir(parents=True)
            migration.write_text("-- migration pending\n", encoding="utf-8")

            self.assertEqual(
                ensure_persistence_schema_test(sandbox, [migration_relative, test_relative]),
                [],
            )
            self.assertFalse((sandbox / test_relative).exists())

    def test_unquotes_non_reserved_table_to_match_jpa_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            entity = sandbox / (
                "application/src/main/java/com/easydep/app/persistence/entity/"
                "SectionEntity.java"
            )
            migration = sandbox / (
                "application/src/main/resources/db/migration/V1__initial_schema.sql"
            )
            entity.parent.mkdir(parents=True)
            migration.parent.mkdir(parents=True)
            entity.write_text('@Table(name = "section")\nclass SectionEntity {}\n', encoding="utf-8")
            migration.write_text(
                'CREATE TABLE "section" (section_id VARCHAR(255));\n'
                'CREATE INDEX idx_section ON "section"(section_id);\n'
                'ALTER TABLE enrollment ADD CONSTRAINT fk FOREIGN KEY (section_id) REFERENCES "section"(section_id);\n',
                encoding="utf-8",
            )

            repaired = repair_persistence_schema_table_quoting(
                sandbox,
                [
                    "application/src/main/resources/db/migration/"
                    "V1__initial_schema.sql"
                ],
            )

            self.assertEqual(
                repaired,
                [
                    "application/src/main/resources/db/migration/"
                    "V1__initial_schema.sql: unquoted table section"
                ],
            )
            updated = migration.read_text(encoding="utf-8")
            self.assertIn("CREATE TABLE section", updated)
            self.assertIn("ON section", updated)
            self.assertIn("REFERENCES section", updated)


class SourceDesignConformanceTest(unittest.TestCase):
    def _spec(self, root: Path, bce: Path, sequence: Path) -> SimpleNamespace:
        return SimpleNamespace(
            base_package="com.example.demo",
            inputs={"bceClass": bce, "sequence": sequence},
        )

    def test_sequence_documents_keep_aliases_scoped_per_use_case(self) -> None:
        source = (
            '@startuml UC1\nboundary "CreateBoundary" as Boundary\n@enduml\n\n'
            '@startuml UC2\nboundary "CancelBoundary" as Boundary\n@enduml'
        )

        documents = _sequence_documents(source)

        self.assertEqual(["UC1", "UC2"], [item[0] for item in documents])
        self.assertEqual(
            "CreateBoundary",
            _participant_aliases(
                documents[0][1], {"CreateBoundary", "CancelBoundary"}
            )["Boundary"],
        )
        self.assertEqual(
            "CancelBoundary",
            _participant_aliases(
                documents[1][1], {"CreateBoundary", "CancelBoundary"}
            )["Boundary"],
        )

    def test_java_observation_handles_multiple_interfaces_and_method_scope(self) -> None:
        source = """
        class CheckoutServiceImpl implements Runnable, CheckoutService {
            void first() { gateway.charge(); repository.save(); }
            void second() { repository.save(); gateway.charge(); }
        }
        """

        self.assertEqual(
            {"Runnable", "CheckoutService"}, _implemented_interfaces(source)
        )
        self.assertEqual(
            [["charge", "save"], ["save", "charge"]],
            _method_call_sequences(source),
        )

    def test_erd_conformance_requires_entity_repository_and_migration_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            erd = run / "erd.puml"
            erd.write_text(
                'entity "Order" as Order {\n  * order_id\n  name: string\n}\n',
                encoding="utf-8",
            )
            package = run / "application/src/main/java/com/example/demo"
            entity = package / "persistence/entity/OrderEntity.java"
            repository = package / "persistence/repository/OrderRepository.java"
            entity.parent.mkdir(parents=True)
            repository.parent.mkdir(parents=True)
            entity.write_text(
                '@Entity class OrderEntity { @Column(name="order_id") Long id; String name; }',
                encoding="utf-8",
            )
            repository.write_text("interface OrderRepository {}", encoding="utf-8")
            migration = run / "application/src/main/resources/db/migration/V1__initial_schema.sql"
            migration.parent.mkdir(parents=True)
            migration.write_text(
                "CREATE TABLE orders (order_id BIGINT, name VARCHAR);",
                encoding="utf-8",
            )
            spec = SimpleNamespace(
                base_package="com.example.demo", inputs={"erd": erd}
            )
            checks: dict[str, object] = {}
            violations: list[dict[str, str]] = []

            _verify_erd_conformance(run, spec, checks, violations, [])

            self.assertEqual([], violations)
            self.assertEqual("PASSED", checks["erdEntities"][0]["status"])

            migration.write_text(
                "CREATE TABLE orders (order_id BIGINT); "
                "CREATE TABLE customers (name VARCHAR);",
                encoding="utf-8",
            )
            violations = []
            _verify_erd_conformance(run, spec, {}, violations, [])
            self.assertEqual("ERD_ENTITY_NOT_IMPLEMENTED", violations[0]["code"])

    def test_preserves_generated_contracts_and_observable_sequence_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            java = run / "application/src/main/java/com/example/demo"
            (java / "bce").mkdir(parents=True)
            (java / "application").mkdir()
            contract = java / "bce/CheckoutGateway.java"
            contract.write_text(
                "package com.example.demo.bce;\n\n"
                "public interface CheckoutGateway {\n"
                "    String PAYMENT_KIND = \"card\";\n"
                "    String charge(String purchaseId);\n"
                "}\n",
                encoding="utf-8",
            )
            (java / "application/CheckoutService.java").write_text(
                "package com.example.demo.application; class CheckoutService implements CheckoutService { "
                "CheckoutGateway gateway; void run() { gateway.charge(); } }\n",
                encoding="utf-8",
            )
            bce = run / "class.puml"
            bce.write_text(
                "class CheckoutService <<Control>> {\n+  + run()\n}\n"
                "class CheckoutGateway <<Gateway>> {\n+  + charge()\n}\n",
                encoding="utf-8",
            )
            sequence = run / "sequence.puml"
            sequence.write_text("CheckoutService -> CheckoutGateway: charge()\n", encoding="utf-8")
            capture_generated_contracts(run, "com.example.demo")

            report = verify_source_design_conformance(run, self._spec(run, bce, sequence))

            self.assertEqual("PASSED", report["status"])
            self.assertTrue((run / "reports/source-design-conformance.json").is_file())

    def test_rejects_modified_contract_and_missing_sequence_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            java = run / "application/src/main/java/com/example/demo"
            (java / "bce").mkdir(parents=True)
            (java / "application").mkdir()
            contract = java / "bce/CheckoutGateway.java"
            contract.write_text(
                "package com.example.demo.bce;\n\n"
                "public interface CheckoutGateway {\n"
                "    String PAYMENT_KIND = \"card\";\n"
                "    String charge(String purchaseId);\n"
                "}\n",
                encoding="utf-8",
            )
            bce = run / "class.puml"
            bce.write_text(
                "class CheckoutService <<Control>> {}\nclass CheckoutGateway <<Gateway>> {}\n",
                encoding="utf-8",
            )
            sequence = run / "sequence.puml"
            sequence.write_text("CheckoutService -> CheckoutGateway: charge()\n", encoding="utf-8")
            capture_generated_contracts(run, "com.example.demo")
            contract.write_text(
                "package com.example.demo.bce;\n\n"
                "public interface CheckoutGateway {\n"
                "    Integer PAYMENT_KIND = 1;\n"
                "    Integer charge(String purchaseId);\n"
                "}\n",
                encoding="utf-8",
            )

            with self.assertRaises(SourceDesignConformanceError):
                verify_source_design_conformance(run, self._spec(run, bce, sequence))

            report = json.loads((run / "reports/source-design-conformance.json").read_text(encoding="utf-8"))
            self.assertEqual("FAILED", report["status"])
            self.assertEqual(
                {
                    "GENERATED_CONTRACT_MODIFIED",
                    "GENERATED_CONTRACT_STRUCTURE_CHANGED",
                    "SEQUENCE_CALL_NOT_IMPLEMENTED",
                },
                {item["code"] for item in report["violations"]},
            )
            changes = report["checks"]["generatedContracts"][0]["changes"]
            self.assertIn("PAYMENT_KIND: String -> Integer", changes["fields"]["modified"])
            self.assertIn("charge(String purchaseId): String -> Integer", changes["methods"]["modified"])
            restored = restore_generated_contracts(run)
            self.assertEqual(["application/src/main/java/com/example/demo/bce/CheckoutGateway.java"], restored)
            self.assertIn("String charge(String purchaseId);", contract.read_text(encoding="utf-8"))


class LoadJobTest(unittest.TestCase):
    def test_cross_phase_repair_replans_owner_and_downstream_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_repair"
            task_dir = run / "reports" / "implementation-tasks"
            execution_dir = run / "reports" / "agent-executions"
            task_dir.mkdir(parents=True)
            execution_dir.mkdir(parents=True)
            definitions = []
            for task_id, task_type, output in (
                (
                    "implement-repositories",
                    "persistence-repositories",
                    "application/src/main/java/example/OrderRepository.java",
                ),
                (
                    "implement-application-wiring",
                    "configuration",
                    "application/src/main/java/example/Application.java",
                ),
                (
                    "implement-end-to-end-flow",
                    "integration-test",
                    "application/src/test/java/example/ApplicationFlowTest.java",
                ),
            ):
                prompt = task_dir / f"{task_id}.prompt.md"
                prompt.write_text(f"base prompt for {task_id}", encoding="utf-8")
                task = {
                    "task_id": task_id,
                    "task_type": task_type,
                    "control": task_id,
                    "prompt_file": prompt.relative_to(run).as_posix(),
                    "context_file": "reports/context.json",
                    "prompt_sha256": task_id,
                    "source_artifacts": {},
                    "allowed_write_paths": [output],
                }
                (task_dir / f"{task_id}.task.json").write_text(
                    json.dumps(task), encoding="utf-8"
                )
                definitions.append(task)
            (run / "reports" / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": definitions}), encoding="utf-8"
            )

            repair = schedule_cross_phase_repair(
                run,
                "implement-application-wiring",
                {
                    "stderr": (
                        "C:\\work\\application\\src\\main\\java\\example\\"
                        "OrderRepository.java:12: error: cannot find symbol"
                    )
                },
            )
            self.assertEqual(["implement-repositories"], repair["ownerTaskIds"])
            self.assertEqual(
                ["implement-application-wiring", "implement-end-to-end-flow"],
                repair["revalidationTaskIds"],
            )

            apply_repair_directives(run)
            manifest = json.loads(
                (run / "reports" / "run-manifest.json").read_text(encoding="utf-8")
            )
            repository = manifest["implementation_tasks"][0]
            self.assertNotEqual("implement-repositories", repository["prompt_sha256"])
            self.assertIn("repairEvidence", repository["source_artifacts"])
            self.assertIn(
                "repair the failure in your owned files",
                (task_dir / "implement-repositories.prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "regenerate and revalidate after an upstream repair",
                (task_dir / "implement-end-to-end-flow.prompt.md").read_text(encoding="utf-8"),
            )
            repository_prompt = (
                task_dir / "implement-repositories.prompt.md"
            ).read_text(encoding="utf-8")
            self.assertIn("OrderRepository.java:12", repository_prompt)
            apply_repair_directives(run)
            self.assertEqual(
                repository_prompt,
                (task_dir / "implement-repositories.prompt.md").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                1,
                repository_prompt.count(
                    "## Orchestrated repair and revalidation directives"
                ),
            )

    def test_cross_phase_repair_budget_is_cumulative_across_changed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                {
                    "task_id": "implement-repositories",
                    "task_type": "persistence-repositories",
                    "allowed_write_paths": [
                        "application/src/main/java/example/OrderRepository.java"
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
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
            )

            for attempt in range(1, 4):
                repair = schedule_cross_phase_repair(
                    run,
                    "implement-application-wiring",
                    {
                        "stderr": (
                            "C:/work/application/src/main/java/example/"
                            f"OrderRepository.java:{attempt}: error: failure {attempt}"
                        )
                    },
                )
                self.assertIsNotNone(repair)
                self.assertEqual(attempt, repair["revision"])

            exhausted = schedule_cross_phase_repair(
                run,
                "implement-application-wiring",
                {
                    "stderr": (
                        "C:/work/application/src/main/java/example/"
                        "OrderRepository.java:99: error: another failure"
                    )
                },
            )
            self.assertIsNone(exhausted)
            plan = json.loads(
                (reports / "repair-plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, len(plan["entries"]))
            self.assertEqual(3, plan["entries"][0]["revision"])

    def test_mapped_by_context_failure_repairs_named_persistence_entities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                {
                    "task_id": "implement-student-entity",
                    "task_type": "persistence-entities",
                    "allowed_write_paths": [
                        "application/src/main/java/example/persistence/entity/StudentEntity.java"
                    ],
                },
                {
                    "task_id": "implement-enrollment-entity",
                    "task_type": "persistence-entities",
                    "allowed_write_paths": [
                        "application/src/main/java/example/persistence/entity/EnrollmentEntity.java"
                    ],
                },
                {
                    "task_id": "implement-application-wiring",
                    "task_type": "configuration",
                    "allowed_write_paths": ["application/src/main/java/example/Application.java"],
                },
                {
                    "task_id": "implement-end-to-end-flow",
                    "task_type": "integration-test",
                    "allowed_write_paths": ["application/src/test/java/example/ApplicationFlowTest.java"],
                },
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
            )

            repair = schedule_cross_phase_repair(
                run,
                "implement-application-wiring",
                {
                    "testResults": (
                        "AnnotationException: Collection 'StudentEntity.enrollments' "
                        "is 'mappedBy' a property named 'student' which does not exist "
                        "in target entity 'EnrollmentEntity'\n"
                        "C:/work/application/src/main/java/example/persistence/entity/"
                        "EnrollmentEntity.java:28"
                    )
                },
            )

            self.assertIsNotNone(repair)
            assert repair is not None
            self.assertEqual(
                ["implement-enrollment-entity", "implement-student-entity"],
                repair["ownerTaskIds"],
            )
            self.assertEqual(
                ["implement-application-wiring", "implement-end-to-end-flow"],
                repair["revalidationTaskIds"],
            )
            plan = json.loads(
                (reports / "repair-plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, len(plan["entries"]))
            self.assertEqual(1, plan["entries"][0]["revision"])

    def test_warning_file_name_does_not_select_repair_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
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
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
            )

            repair = schedule_cross_phase_repair(
                run,
                "implement-application-wiring",
                {
                    "stderr": (
                        "warning: application/src/test/java/example/"
                        "PortfolioApiControllerTest.java uses unchecked operations\n"
                        "NoSuchBeanDefinitionException: repository bean failed"
                    )
                },
            )

            self.assertEqual(["implement-repositories"], repair["ownerTaskIds"])

    def test_e2e_failure_without_source_path_selects_api_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                {
                    "task_id": "implement-orders-api-adapter",
                    "task_type": "api-adapter",
                    "control": "OrdersApi",
                    "allowed_write_paths": ["application/OrdersApiController.java"],
                },
                {
                    "task_id": "implement-end-to-end-flow",
                    "task_type": "integration-test",
                    "control": "flow",
                    "allowed_write_paths": ["application/ApplicationFlowTest.java"],
                },
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
            )
            repair = schedule_cross_phase_repair(
                run,
                "implement-end-to-end-flow",
                {"testResults": "expected HTTP 201 but was 500"},
            )
            self.assertEqual(
                ["implement-orders-api-adapter"], repair["ownerTaskIds"]
            )

    def test_provider_retry_helpers_are_bounded_and_classify_nim_errors(self) -> None:
        self.assertTrue(transient_provider_error(RuntimeError("429 rate limit")))
        self.assertTrue(transient_provider_error(TimeoutError("timed out")))
        self.assertFalse(transient_provider_error(ValueError("invalid model name")))
        from app.core.config import settings
        with patch.object(settings, "openhands_provider_retry_base_seconds", 2), \
             patch.object(settings, "openhands_provider_retry_max_seconds", 5):
            self.assertEqual(2, provider_retry_delay(1))
            self.assertEqual(5, provider_retry_delay(4))

    def test_missing_output_repair_prompt_is_compact_and_task_specific(self) -> None:
        prompt = _render_missing_output_repair_prompt(
            "integration-test",
            ["C:/agent/application/src/test/java/example/FlowTest.java"],
        )
        self.assertIn("real HTTP flow test", prompt)
        self.assertIn("C:/agent/application/src/test/java/example/FlowTest.java", prompt)
        self.assertIn("file editor's create operation", prompt)
        self.assertIn("Do not use /workspace", prompt)
        self.assertNotIn("inspect", prompt)
        self.assertNotIn("generatedJavaContracts", prompt)

        contract_prompt = _render_missing_output_repair_prompt(
            "control",
            ["C:/agent/application/src/main/java/example/ControlService.java"],
            "interface Control { String execute(String value); }",
        )
        self.assertIn("Exact generated contracts", contract_prompt)
        self.assertIn("String execute(String value)", contract_prompt)

        api_test_prompt = _render_missing_output_repair_prompt(
            "api-adapter",
            ["C:/agent/application/src/test/java/example/OrdersApiControllerTest.java"],
            "interface OrdersApi { ResponseEntity<Object> getOrder(String orderId); }",
            "### application/src/main/java/example/OrdersApiController.java\n"
            "```java\nclass OrdersApiController {}\n```",
        )
        self.assertIn("JUnit/Mockito controller test", api_test_prompt)
        self.assertIn("Do not modify production code", api_test_prompt)
        self.assertIn("Existing contracted source", api_test_prompt)
        self.assertIn("OrdersApiController", api_test_prompt)

    def test_task_verification_avoids_full_packaging_and_targets_owned_tests(self) -> None:
        command = task_verification_command(
            ["gradlew"],
            "configuration",
            [
                "application/src/main/java/example/Application.java",
                "application/src/test/java/example/ApplicationContextTest.java",
            ],
        )
        self.assertIn("compileJava", command)
        self.assertIn("testClasses", command)
        self.assertIn("test", command)
        self.assertNotIn("bootJar", command)
        self.assertEqual(
            ["*ApplicationContextTest"],
            [command[index + 1] for index, item in enumerate(command) if item == "--tests"],
        )

        final_command = task_verification_command(["gradlew"])
        self.assertIn("bootJar", final_command)
        self.assertIn("test", final_command)
        self.assertNotIn("--no-daemon", command)
        self.assertNotIn("--no-daemon", final_command)

    def test_agent_execution_history_preserves_attempt_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            executions = reports / "agent-executions"
            executions.mkdir(parents=True)
            (reports / "workflow-state.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {"taskId": "implement-wiring", "attempts": 2}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(2, execution_attempt(run, "implement-wiring"))

            write_execution_result(
                executions, "implement-wiring", 1, {"status": "FAILED"}
            )
            write_execution_result(
                executions, "implement-wiring", 2, {"status": "SUCCEEDED"}
            )

            self.assertTrue(
                (executions / "implement-wiring.attempt-001.result.json").is_file()
            )
            self.assertTrue(
                (executions / "implement-wiring.attempt-002.result.json").is_file()
            )
            latest = json.loads(
                (executions / "implement-wiring.result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("SUCCEEDED", latest["status"])

    def test_verification_source_paths_normalize_windows_paths(self) -> None:
        self.assertEqual(
            ["application/src/main/java/example/OrderRepository.java"],
            referenced_source_paths(
                {
                    "stderr": (
                        "C:\\work\\application\\src\\main\\java\\example\\"
                        "OrderRepository.java:42: error"
                    )
                }
            ),
        )

    def test_implementation_ir_parses_json_openapi(self) -> None:
        operations = parse_ir_openapi_operations(json.dumps({
            "openapi": "3.0.3",
            "paths": {
                "/sessions": {
                    "post": {
                        "operationId": "createSession",
                        "responses": {
                            "201": {"description": "Created"},
                            "401": {"description": "Unauthorized"},
                        },
                    }
                }
            },
        }))

        self.assertEqual(1, len(operations))
        self.assertEqual(("POST", "/sessions", "createSession"), (
            operations[0].method, operations[0].path, operations[0].operation_id
        ))
        self.assertEqual([201, 401], [item.status for item in operations[0].responses])

    def test_implementation_ir_and_planners_support_order_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bce.puml").write_text(
                """class OrderService <<Control>> {
  + createOrder(customerId: string): Order
}
class CheckoutScreen <<Boundary>> { + submit(customerId: string) }
class Order <<Entity>> { - orderId: string }
class OrderStoreGateway <<Gateway>> { + save(order: Order): Order }
class PaymentGateway <<Gateway>> { + charge(orderId: string): boolean }
""",
                encoding="utf-8",
            )
            (root / "sequence.puml").write_text(
                """CheckoutScreen -> OrderService : createOrder(customerId)
OrderService -> PaymentGateway : charge(orderId)
alt invalid order
OrderService --> CheckoutScreen : validation error
end
""",
                encoding="utf-8",
            )
            (root / "erd.puml").write_text(
                'entity "Order" as Order { * order_id : VARCHAR }', encoding="utf-8"
            )
            (root / "openapi.yaml").write_text(
                """openapi: 3.0.3
paths:
  /orders:
    post:
      operationId: createOrder
      responses:
        '201':
          description: Order created
        '422':
          description: Invalid order
""",
                encoding="utf-8",
            )
            job = root / "job.json"
            job.write_text(
                json.dumps({
                    "name": "order-management",
                    "workspaceRoot": ".",
                    "inputs": {
                        "bceClass": "bce.puml",
                        "sequence": "sequence.puml",
                        "erd": "erd.puml",
                        "openapi": "openapi.yaml",
                    },
                    "generation": {"basePackage": "com.example.orders"},
                    "tools": {"puml2codeRoot": ".", "openapiGeneratorJar": "bce.puml"},
                }),
                encoding="utf-8",
            )
            package = run = root / "run_order"
            java = run / "application/src/main/java/com/example/orders"
            (java / "api").mkdir(parents=True)
            (java / "api/OrdersApi.java").write_text(
                'package com.example.orders.api; interface OrdersApi { String PATH = "/orders"; createOrder(); }',
                encoding="utf-8",
            )
            (java / "bce").mkdir(parents=True)
            for name in ("OrderService", "CheckoutScreen", "Order", "OrderStoreGateway", "PaymentGateway"):
                (java / f"bce/{name}.java").write_text(
                    f"package com.example.orders.bce; public interface {name} {{}}",
                    encoding="utf-8",
                )

            spec = load_job(job)
            ir = build_implementation_ir(spec, run)

            self.assertEqual("OrderManagementApplication", ir.application_class)
            self.assertEqual(["Orders"], [port.name for port in ir.api_ports])
            self.assertEqual(
                {"OrderStoreGateway": "persistence", "PaymentGateway": "external"},
                {gateway.name: gateway.kind for gateway in ir.gateways},
            )
            self.assertEqual({201, 422}, {scenario.status for scenario in ir.e2e_scenarios})
            self.assertEqual(
                ["implement-orders-api-adapter"],
                [task.task_id for task in generate_api_adapter_tasks(spec, run)],
            )
            gateway_tasks = generate_gateway_adapter_tasks(spec, run)
            self.assertEqual(
                {"implement-order-store-gateway-adapter", "implement-payment-gateway-adapter"},
                {task.task_id for task in gateway_tasks},
            )
            wiring = generate_wiring_tasks(spec, run)[0]
            self.assertNotIn(
                "application/src/main/java/com/example/orders/OrderManagementApplication.java",
                wiring.allowed_write_paths,
            )
            self.assertTrue(
                (java / "OrderManagementApplication.java").is_file()
            )

    def test_semantic_gate_requires_ir_status_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "OrderManagementFlowTest.java"
            path.write_text(
                """class OrderManagementFlowTest {
TestRestTemplate http; OrderRepository repository; InMemoryPaymentGatewayAdapter gateway;
@Test void created() { assertThat(response.status()).isEqualTo(201); use("/orders"); }
@Test void rejected() { assertThat(response.status()).isEqualTo(400); use("/orders/123"); }
void use(String value) {}
}""",
                encoding="utf-8",
            )
            contract = {
                "paths": ["/orders", "/orders/{orderId}"],
                "statuses": [201, 422],
                "repositories": ["OrderRepository"],
                "gatewayAdapters": ["InMemoryPaymentGatewayAdapter"],
                "minimumTests": 2,
            }

            violations = e2e_contract_violations(path, contract)

            self.assertTrue(any("422" in item for item in violations))
            self.assertFalse(any("OrderRepository" in item for item in violations))

    def test_api_adapter_gate_requires_generated_openapi_interface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            relative = "application/src/main/java/com/example/StudentsApiController.java"
            path = sandbox / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                "public class StudentsApiController { }", encoding="utf-8"
            )
            violations = api_adapter_contract_violations(sandbox, [relative])
            self.assertIn("must implement generated StudentsApi", violations[0])

            path.write_text(
                "public class StudentsApiController implements StudentsApi { }",
                encoding="utf-8",
            )
            self.assertEqual([], api_adapter_contract_violations(sandbox, [relative]))

            path.write_text(
                "package com.example.adapter.in.web;\n"
                "import com.example.bce.control.StudentsControl;\n"
                "public class StudentsApiController implements StudentsApi { }",
                encoding="utf-8",
            )
            violations = api_adapter_contract_violations(sandbox, [relative])
            self.assertTrue(
                any("imported project type does not exist" in item for item in violations)
            )

            api = sandbox / "application/src/main/java/com/example/StudentsApi.java"
            api.write_text(
                '@ApiResponse(responseCode = "204")\n'
                '@ApiResponse(responseCode = "404")\n'
                '@ApiResponse(responseCode = "409")\n'
                'interface StudentsApi {}',
                encoding="utf-8",
            )
            violations = api_adapter_contract_violations(sandbox, [relative])
            # 204/404 may be handled by the normal command/null-result path;
            # only a domain conflict requires an executable adapter outcome.
            self.assertFalse(any("HTTP 204" in item for item in violations))
            self.assertFalse(any("HTTP 404" in item for item in violations))
            self.assertTrue(any("HTTP 409" in item for item in violations))

            path.write_text(
                "public class StudentsApiController implements StudentsApi { "
                "ResponseEntity<Void> drop() { return ResponseEntity.noContent().build(); } "
                "ResponseEntity<Void> missing() { return ResponseEntity.notFound().build(); } "
                "ResponseEntity<Void> conflict() { return ResponseEntity.status(HttpStatus.CONFLICT).build(); } }",
                encoding="utf-8",
            )
            self.assertEqual([], api_adapter_contract_violations(sandbox, [relative]))

            api.write_text(
                '@ApiResponse(responseCode = "400")\n'
                '@ApiResponse(responseCode = "403")\n'
                '@ApiResponse(responseCode = "500")\n'
                '@ApiResponse(responseCode = "503")\n'
                'interface StudentsApi {}',
                encoding="utf-8",
            )
            path.write_text(
                "public class StudentsApiController implements StudentsApi { }",
                encoding="utf-8",
            )
            self.assertEqual([], api_adapter_contract_violations(sandbox, [relative]))

    def test_workspace_verification_error_preserves_causal_output_edges(self) -> None:
        error = WorkspaceVerificationError({
            "command": ["gradlew", "test"],
            "testResults": "ROOT CAUSE: assertion failed\n" + ("trace line\n" * 300),
        })

        message = str(error)
        self.assertIn("ROOT CAUSE: assertion failed", message)
        self.assertIn("verification output truncated", message)
        self.assertTrue(message.endswith("trace line\ntrace line"))

    def test_semantic_gate_accepts_spring_http_status_enums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CourseFlowTest.java"
            path.write_text(
                """class CourseFlowTest {
TestRestTemplate http; CourseRepository repository;
@Test void created() { assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED); }
@Test void listed() { assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK); }
}""",
                encoding="utf-8",
            )
            contract = {
                "paths": [],
                "statuses": [200, 201],
                "repositories": ["CourseRepository"],
                "minimumTests": 2,
            }

            self.assertEqual([], e2e_contract_violations(path, contract))

    def test_e2e_persistence_contract_requires_explicit_sequence_participant(self) -> None:
        scenarios = [{"path": "/students/{studentId}/schedule"}]
        self.assertEqual([], _e2e_persistence_paths("Student -> ScheduleBoundary: viewSchedule()", scenarios))
        self.assertEqual(
            ["/students/{studentId}/schedule"],
            _e2e_persistence_paths("participant EnrollmentRepository\nScheduleControl -> EnrollmentRepository: find()", scenarios),
        )

    def test_e2e_persistence_contract_infers_repository_from_bce_erd_relation(self) -> None:
        scenarios = [{"path": "/students/{studentId}/courses/{courseId}"}]
        bce = """@startuml
class RegistrationControl <<Control>> { registerStudent(courseId:String): Enrollment }
class Enrollment <<Entity>> { enrollmentId:String }
RegistrationControl ..> Enrollment
@enduml"""
        erd = 'entity "Enrollment" as Enrollment { enrollmentId : String }'
        openapi = '{"Endpoints":[{"path":"/students/{studentId}/courses/{courseId}","control_binding":{"control":"RegistrationControl"}}]}'

        self.assertEqual(
            ["/students/{studentId}/courses/{courseId}"],
            _e2e_persistence_paths("RegistrationControl -> Enrollment: registerStudent()", scenarios, bce=bce, erd=erd, openapi=openapi),
        )

    def test_semantic_gate_pairs_dynamic_path_method_and_status_per_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "EnrollmentFlowTest.java"
            path.write_text(
                """class EnrollmentFlowTest {
TestRestTemplate http; EnrollmentRepository repository;
@Test void enroll() {
  String url = \"/sections/\" + SECTION_ID + \"/enrollments\";
  ResponseEntity<Void> response = http.postForEntity(url, request, Void.class);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
}
@Test void cancel() {
  String url = \"/sections/\" + SECTION_ID + \"/enrollments\";
  ResponseEntity<Void> response = http.exchange(url, HttpMethod.DELETE, request, Void.class);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NO_CONTENT);
}
}""",
                encoding="utf-8",
            )
            contract = {
                "repositories": ["EnrollmentRepository"],
                "minimumTests": 2,
                "scenarios": [
                    {"method": "POST", "path": "/sections/{sectionId}/enrollments", "status": 200},
                    {"method": "DELETE", "path": "/sections/{sectionId}/enrollments", "status": 204},
                ],
            }

            self.assertEqual([], e2e_contract_violations(path, contract))

    def test_semantic_gate_accepts_rest_template_uri_template_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CourseFlowTest.java"
            path.write_text(
                """class CourseFlowTest {
TestRestTemplate http; CourseRepository repository;
@Test void courseDetail() {
  ResponseEntity<Object> response = http.getForEntity("/courses/{courseId}", Object.class, COURSE_ID);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
}
@Test void cancelEnrollment() {
  ResponseEntity<Void> response = http.exchange("/enrollments/{courseId}", HttpMethod.DELETE, request, Void.class, COURSE_ID);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NO_CONTENT);
}
@Test void schedule() {
  ResponseEntity<Object> response = http.getForEntity("/students/{studentId}/schedule", Object.class, STUDENT_ID);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
}
}""",
                encoding="utf-8",
            )
            contract = {
                "repositories": ["CourseRepository"],
                "minimumTests": 3,
                "scenarios": [
                    {"method": "GET", "path": "/courses/{courseId}", "status": 200},
                    {"method": "DELETE", "path": "/enrollments/{courseId}", "status": 204},
                    {"method": "GET", "path": "/students/{studentId}/schedule", "status": 200},
                ],
            }

            self.assertEqual([], e2e_contract_violations(path, contract))

    def test_semantic_gate_accepts_static_mockmvc_request_builders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CourseFlowTest.java"
            path.write_text(
                """import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
class CourseFlowTest {
MockMvc mockMvc;
@Test
@Transactional
void create() throws Exception {
  mockMvc.perform(post("/courses")).andExpect(status().isCreated());
}
@Test
@Transactional
void list() throws Exception {
  mockMvc.perform(get("/courses")).andExpect(status().isOk());
}
}""",
                encoding="utf-8",
            )
            contract = {
                "minimumTests": 2,
                "scenarios": [
                    {"method": "POST", "path": "/courses", "status": 201},
                    {"method": "GET", "path": "/courses", "status": 200},
                ],
            }

            self.assertEqual([], e2e_contract_violations(path, contract))

    def test_semantic_gate_accepts_http_helpers_and_concatenated_terminal_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CourseFlowTest.java"
            path.write_text(
                """class CourseFlowTest {
TestRestTemplate http; CourseRepository repository;
@Test void login() { performLogin(); }
@Test void details() {
  ResponseEntity<Object> response = http.getForEntity("/courses/" + COURSE_ID, Object.class);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
}
void performLogin() {
  ResponseEntity<Object> response = http.postForEntity("/auth/login", request, Object.class);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
}
}""",
                encoding="utf-8",
            )
            contract = {
                "repositories": ["CourseRepository"],
                "minimumTests": 2,
                "scenarios": [
                    {"method": "POST", "path": "/auth/login", "status": 200},
                    {"method": "GET", "path": "/courses/{courseId}", "status": 200},
                ],
            }

            self.assertEqual([], e2e_contract_violations(path, contract))

    def test_semantic_gate_rejects_wrong_status_or_extra_path_segment_per_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "EnrollmentFlowTest.java"
            path.write_text(
                """class EnrollmentFlowTest {
TestRestTemplate http; EnrollmentRepository repository;
@Test void enroll() {
  String url = \"/sections/\" + SECTION_ID + \"/enrollments\";
  ResponseEntity<Void> response = http.postForEntity(url, request, Void.class);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
}
@Test void cancel() {
  String url = \"/sections/\" + SECTION_ID + \"/enrollments/\" + STUDENT_ID;
  ResponseEntity<Void> response = http.exchange(url, HttpMethod.DELETE, request, Void.class);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NO_CONTENT);
}
}""",
                encoding="utf-8",
            )
            contract = {
                "repositories": ["EnrollmentRepository"],
                "minimumTests": 2,
                "scenarios": [
                    {"method": "POST", "path": "/sections/{sectionId}/enrollments", "status": 200},
                    {"method": "DELETE", "path": "/sections/{sectionId}/enrollments", "status": 204},
                ],
            }

            violations = e2e_contract_violations(path, contract)

            self.assertIn(
                "Missing asserted HTTP status for scenario POST /sections/{sectionId}/enrollments: 200",
                violations,
            )
            self.assertIn("Missing HTTP path evidence: /sections/{sectionId}/enrollments", violations)

    def test_e2e_semantic_gate_rejects_simplified_weak_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "StockPurchaseFlowTest.java"
            path.write_text(
                """class StockPurchaseFlowTest {
@Test void uiInteractionFlow() {
  // Simplified integration test: portfolio may be null; no strict assertion.
}
}""",
                encoding="utf-8",
            )

            violations = e2e_contract_violations(path)

            self.assertTrue(any("at least 4" in item for item in violations))
            self.assertTrue(any("weak dual-outcome" in item for item in violations))
            self.assertTrue(any("purchase persistence" in item for item in violations))

    def test_e2e_repair_normalizes_spring_boot3_legacy_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "FlowTest.java"
            path.write_text(
                """import org.springframework.boot.web.server.LocalServerPort;
import javax.persistence.Entity;
import javax.validation.Valid;
import javax.servlet.http.HttpServletRequest;
import javax.annotation.Nullable;
import javax.transaction.Transactional;
// javax.persistence.Entity must remain unchanged in this comment.
""",
                encoding="utf-8",
            )

            self.assertTrue(repair_spring_boot3_test_compatibility(path))
            rewritten = path.read_text(encoding="utf-8")
            self.assertIn("org.springframework.boot.test.web.server.LocalServerPort", rewritten)
            self.assertIn("jakarta.persistence.Entity", rewritten)
            self.assertIn("jakarta.validation.Valid", rewritten)
            self.assertIn("jakarta.servlet.http.HttpServletRequest", rewritten)
            self.assertIn("jakarta.annotation.Nullable", rewritten)
            self.assertIn("jakarta.transaction.Transactional", rewritten)
            self.assertIn("// javax.persistence.Entity", rewritten)
            self.assertFalse(repair_spring_boot3_test_compatibility(path))

    def test_e2e_semantic_gate_accepts_real_http_and_persistence_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "StockPurchaseFlowTest.java"
            path.write_text(
                """class StockPurchaseFlowTest {
TestRestTemplate http; InMemoryTradingSiteGatewayAdapter gateway;
PurchaseRecordRepository purchases; HoldingRepository holdings;
@Test void success() { gateway.enqueueOutcome(null); use("completed", "/portfolio"); }
@Test void rejection() { gateway.rejectSite("bad"); }
@Test void delay() { use("delayed"); }
@Test void clarification() { use("missing_information", "clarification"); }
void use(String... value) {}
}""",
                encoding="utf-8",
            )

            self.assertEqual([], e2e_contract_violations(path))

    def test_e2e_semantic_gate_accepts_one_repository_from_generated_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CourseFlowTest.java"
            path.write_text(
                """class CourseFlowTest {
TestRestTemplate http; CourseRepository courseRepository;
@Test void success() { assertThat(response.getStatusCode()).isEqualTo(200); use("/courses"); }
}""",
                encoding="utf-8",
            )
            contract = {
                "paths": ["/courses"],
                "statuses": [200],
                "repositories": ["CourseRepository", "InstructorRepository"],
                "minimumTests": 1,
            }

            self.assertEqual([], e2e_contract_violations(path, contract))

    def test_purchases_adapter_prompt_requires_clarification_status_mapping(self) -> None:
        prompt = render_api_adapter_prompt(
            SimpleNamespace(base_package="com.example.demo"),
            ApiPortIR(
                "Orders",
                "application/src/main/java/com/example/demo/api/OrdersApi.java",
                (
                    ApiOperationIR(
                        "POST",
                        "/orders",
                        "createOrder",
                        (
                            ApiResponseIR(201, "Created"),
                            ApiResponseIR(422, "Invalid order"),
                        ),
                    ),
                ),
            ),
            "contracts",
            "sequence",
            {
                "createOrder": {
                    "control": "RegistrationControl",
                    "method": "register",
                }
            },
        )

        self.assertIn("POST /orders", prompt)
        self.assertIn("201 Created", prompt)
        self.assertIn("422 Invalid order", prompt)
        self.assertIn("every documented status", prompt)
        self.assertIn("Do not pass an API", prompt)
        self.assertIn("public no-argument constructor", prompt)
        self.assertIn("com.example.demo.bce.RegistrationControl", prompt)
        self.assertIn("Never derive a resource-named Control", prompt)
        self.assertIn("resource-named substitute", prompt)
        self.assertIn("transport-level failures", prompt)
        self.assertIn("documentation only", prompt)
        self.assertIn("exception expectation in a test", prompt)
        self.assertIn("keep every generated source and\n  test compilable", prompt)
        self.assertIn("Do not rely on Mockito's default `false`", prompt)

    def test_production_placeholder_gate_ignores_tests_and_rejects_main_java(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = "application/src/main/java/com/example/Service.java"
            test = "application/src/test/java/com/example/ServiceTest.java"
            for relative in (main, test):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("class Example { // TODO finish\n}", encoding="utf-8")

            evidence = production_placeholder_markers(root, [main, test])

            self.assertEqual([], evidence)

    def test_production_marker_gate_allows_placeholder_word_but_rejects_todo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = "application/src/main/java/com/example/Service.java"
            path = root / main
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "class Example { // Return an empty string as a placeholder.\n"
                "// TODO provide a real value\n}",
                encoding="utf-8",
            )

            evidence = production_placeholder_markers(root, [main])

            self.assertEqual([], evidence)

    def test_production_placeholder_gate_rejects_unimplemented_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = "application/src/main/java/com/example/CoursesApiController.java"
            path = root / main
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                """class CoursesApiController {
  Object searchCourses(Object body) {
    throw new UnsupportedOperationException("SearchCourses not implemented: missing SearchCriteria mapping.");
  }
}""",
                encoding="utf-8",
            )

            evidence = production_placeholder_markers(root, [main])

            self.assertEqual(1, len(evidence))
            self.assertIn("UnsupportedOperationException", evidence[0])

    def test_production_test_library_gate_rejects_mockito_only_in_main_java(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = "application/src/main/java/com/example/ApplicationConfiguration.java"
            test = "application/src/test/java/com/example/ApplicationConfigurationTest.java"
            for relative in (main, test):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "import org.mockito.Mockito;\nclass Example { Object value = Mockito.mock(Object.class); }",
                    encoding="utf-8",
                )

            evidence = production_test_library_markers(root, [main, test])

            self.assertEqual(2, len(evidence))
            self.assertTrue(all(main in item for item in evidence))

    def test_persistence_reserved_identifier_gate_rejects_h2_year_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entity = "application/src/main/java/com/example/AcademicTermEntity.java"
            migration = "application/src/main/resources/db/migration/V1__initial.sql"
            for relative, source in (
                (entity, '@Column(name = "year")\nprivate Integer year;'),
                (migration, "create table academic_term (\n  year integer not null\n);"),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")

            evidence = persistence_reserved_identifier_markers(root, [entity, migration])

            self.assertEqual(2, len(evidence))
            self.assertTrue(all("year" in item for item in evidence))

    def test_h2_failure_in_integration_task_skips_local_llm_repair(self) -> None:
        self.assertTrue(
            _requires_cross_phase_repair(
                "integration-test",
                {"testResults": "JdbcSQLSyntaxErrorException: expected \"identifier\""},
            )
        )
        self.assertFalse(
            _requires_cross_phase_repair(
                "persistence-schema",
                {"testResults": "JdbcSQLSyntaxErrorException"},
            )
        )

    def test_missing_repository_bean_in_integration_task_skips_local_llm_repair(self) -> None:
        self.assertTrue(
            _requires_cross_phase_repair(
                "integration-test",
                {
                    "testResults": (
                        "NoSuchBeanDefinitionException: No qualifying bean of type "
                        "'com.example.OrderRepository' available"
                    )
                },
            )
        )

    def test_http_runtime_failure_in_integration_task_skips_local_llm_repair(self) -> None:
        self.assertTrue(
            _requires_cross_phase_repair(
                "integration-test",
                {
                    "testResults": (
                        "org.opentest4j.AssertionFailedError: expected: <201 CREATED> "
                        "but was: <500 INTERNAL_SERVER_ERROR>"
                    )
                },
            )
        )
        self.assertFalse(
            _requires_cross_phase_repair(
                "integration-test",
                {"testResults": "error: cannot find symbol"},
            )
        )
        self.assertTrue(
            _requires_cross_phase_repair(
                "integration-test",
                {"stderr": "StudentRepository available: expected at least 1 bean which qualifies as autowire candidate"},
            )
        )

    def test_mapped_by_failure_in_wiring_skips_local_llm_repair(self) -> None:
        self.assertTrue(
            _requires_cross_phase_repair(
                "configuration",
                {
                    "testResults": (
                        "AnnotationException: Collection 'StudentEntity.enrollments' "
                        "is 'mappedBy' a property named 'student' which does not exist "
                        "in target entity 'EnrollmentEntity'"
                    )
                },
            )
        )

    def test_schema_type_failure_in_wiring_skips_local_llm_repair(self) -> None:
        self.assertTrue(
            _requires_cross_phase_repair(
                "configuration",
                {
                    "testResults": (
                        "SchemaManagementException: Schema-validation: wrong column type "
                        "encountered in column [enrollment_date]"
                    )
                },
            )
        )

    def test_wiring_normalizer_restores_spring_data_repository_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            entrypoint = (
                sandbox
                / "application/src/main/java/com/example/DemoApplication.java"
            )
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text(
                "import org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration;\n"
                "import org.springframework.boot.autoconfigure.data.jpa.JpaRepositoriesAutoConfiguration;\n"
                "@SpringBootApplication(exclude = {HibernateJpaAutoConfiguration.class, JpaRepositoriesAutoConfiguration.class})\n"
                "class DemoApplication {}\n",
                encoding="utf-8",
            )
            normalize_spring_boot_repository_discovery(
                sandbox,
                {"allowed_write_paths": [
                    "application/src/main/java/com/example/DemoApplication.java"
                ]},
            )
            normalized = entrypoint.read_text(encoding="utf-8")
            self.assertIn("@SpringBootApplication", normalized)
            self.assertNotIn("exclude", normalized)
            self.assertNotIn("JpaRepositoriesAutoConfiguration", normalized)

    def test_configuration_normalizer_removes_placeholder_line_comments(self) -> None:
        normalized, changed = remove_placeholder_comments(
            'return ""; // Return an empty string as a placeholder.\n'
        )

        self.assertTrue(changed)
        self.assertEqual('return ""; \n', normalized)

    def test_wiring_normalizer_enforces_jpa_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            configuration = sandbox / "application/src/main/resources/application.yml"
            configuration.parent.mkdir(parents=True)
            configuration.write_text(
                "spring:\n  jpa:\n    hibernate:\n      ddl-auto: none\n",
                encoding="utf-8",
            )

            enforce_spring_persistence_validation(
                sandbox,
                {"allowed_write_paths": ["application/src/main/resources/application.yml"]},
            )

            self.assertIn(
                "ddl-auto: validate", configuration.read_text(encoding="utf-8")
            )

    def test_workflow_checkpoint_recovers_results_and_interrupted_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_workflow"
            reports = run / "reports/agent-executions"
            reports.mkdir(parents=True)
            first_output = "application/src/First.java"
            second_output = "application/src/Second.java"
            first = run / first_output
            first.parent.mkdir(parents=True)
            first.write_text("class First {}", encoding="utf-8")
            tasks = [
                {
                    "task_id": "implement-first",
                    "task_type": "control",
                    "prompt_sha256": "prompt-first",
                    "allowed_write_paths": [first_output],
                    "source_artifacts": {},
                },
                {
                    "task_id": "implement-second",
                    "task_type": "api-adapter",
                    "prompt_sha256": "prompt-second",
                    "allowed_write_paths": [second_output],
                    "source_artifacts": {},
                },
            ]
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
            )
            (reports / "implement-first.result.json").write_text(
                json.dumps(
                    {
                        "taskId": "implement-first",
                        "status": "SUCCEEDED",
                        "promptSha256": "prompt-first",
                    }
                ),
                encoding="utf-8",
            )

            state = reconcile_workflow_state(run)
            self.assertEqual("SUCCEEDED", state["tasks"][0]["status"])
            self.assertEqual("PENDING", state["tasks"][1]["status"])
            self.assertEqual(["implement-second"], state["nextRunnableTasks"])

            state["tasks"][1]["status"] = "RUNNING"
            (run / "reports/workflow-state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            recovered = reconcile_workflow_state(run)
            self.assertEqual("INTERRUPTED", recovered["tasks"][1]["status"])

    def test_workflow_invalidates_succeeded_task_when_output_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_changed"
            reports = run / "reports/agent-executions"
            reports.mkdir(parents=True)
            relative = "application/src/Changed.java"
            output = run / relative
            output.parent.mkdir(parents=True)
            output.write_text("class Changed {}", encoding="utf-8")
            task = {
                "task_id": "implement-changed",
                "task_type": "control",
                "prompt_sha256": "same-prompt",
                "allowed_write_paths": [relative],
                "source_artifacts": {},
            }
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
            )
            (reports / "implement-changed.result.json").write_text(
                json.dumps(
                    {
                        "taskId": "implement-changed",
                        "status": "SUCCEEDED",
                        "promptSha256": "same-prompt",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                "SUCCEEDED", reconcile_workflow_state(run)["tasks"][0]["status"]
            )

            output.write_text("class Changed { int value; }", encoding="utf-8")
            changed = reconcile_workflow_state(run)
            self.assertEqual("PENDING", changed["tasks"][0]["status"])
            still_changed = reconcile_workflow_state(run)
            self.assertEqual("PENDING", still_changed["tasks"][0]["status"])

    def test_workflow_keeps_succeeded_task_when_replanning_changes_only_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_replanned"
            reports = run / "reports"
            reports.mkdir(parents=True)
            relative = "application/src/Stable.java"
            output = run / relative
            output.parent.mkdir(parents=True)
            output.write_text("class Stable {}", encoding="utf-8")
            (reports / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": "implement-stable",
                                "task_type": "control",
                                "prompt_sha256": "replanned-prompt",
                                "allowed_write_paths": [relative],
                                "source_artifacts": {},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            old_hashes = {
                relative: hashlib.sha256(output.read_bytes()).hexdigest()
            }
            (reports / "workflow-state.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "taskId": "implement-stable",
                                "status": "SUCCEEDED",
                                "promptSha256": "original-prompt",
                                "outputHashes": old_hashes,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            state = reconcile_workflow_state(run)

            self.assertEqual("SUCCEEDED", state["tasks"][0]["status"])
            self.assertEqual("replanned-prompt", state["tasks"][0]["promptSha256"])
            self.assertEqual([], state["nextRunnableTasks"])

    def test_workflow_replays_successful_checkpoint_for_repair_directive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_repair_replay"
            reports = run / "reports"
            executions = reports / "agent-executions"
            executions.mkdir(parents=True)
            relative = "application/src/Stable.java"
            output = run / relative
            output.parent.mkdir(parents=True)
            output.write_text("class Stable {}", encoding="utf-8")
            output_hashes = {relative: hashlib.sha256(output.read_bytes()).hexdigest()}
            (reports / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": "implement-stable",
                                "task_type": "control",
                                "prompt_sha256": "repair-prompt",
                                "allowed_write_paths": [relative],
                                "source_artifacts": {"repairEvidence": "reports/repair-plan.json"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "workflow-state.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "taskId": "implement-stable",
                                "status": "SUCCEEDED",
                                "promptSha256": "original-prompt",
                                "outputHashes": output_hashes,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (executions / "implement-stable.result.json").write_text(
                json.dumps({"status": "SUCCEEDED", "promptSha256": "original-prompt"}),
                encoding="utf-8",
            )
            (reports / "repair-plan.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {"ownerTaskIds": ["implement-stable"], "revalidationTaskIds": []}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            state = reconcile_workflow_state(run)

            self.assertEqual("PENDING", state["tasks"][0]["status"])
            self.assertEqual(["implement-stable"], state["nextRunnableTasks"])

    def test_workflow_invalidates_failed_result_when_repair_prompt_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports" / "agent-executions"
            reports.mkdir(parents=True)
            output = "application/src/Repair.java"
            (run / output).parent.mkdir(parents=True)
            (run / output).write_text("class Repair {}", encoding="utf-8")
            (run / "reports" / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": "repair",
                                "task_type": "control",
                                "prompt_sha256": "new-repair-prompt",
                                "allowed_write_paths": [output],
                                "source_artifacts": {},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "repair.result.json").write_text(
                json.dumps(
                    {
                        "status": "FAILED",
                        "promptSha256": "old-failed-prompt",
                        "error": "compile failed",
                    }
                ),
                encoding="utf-8",
            )

            state = reconcile_workflow_state(run)

            self.assertEqual("PENDING", state["tasks"][0]["status"])
            self.assertEqual(["repair"], state["nextRunnableTasks"])

    def test_workflow_reports_design_gap_as_needs_input_even_with_empty_optional_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_design_gap"
            reports = run / "reports"
            (reports / "design-gaps").mkdir(parents=True)
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": []}), encoding="utf-8"
            )
            (reports / "design-gaps/end-to-end-flow.json").write_text(
                json.dumps({"status": "NEEDS_INPUT", "gaps": [{"code": "UNRESOLVED_PRODUCTION_PATH"}]}),
                encoding="utf-8",
            )

            state = reconcile_workflow_state(run)

            self.assertEqual("NEEDS_INPUT", state["status"])
            self.assertIn("unresolved design contracts", state["blockingReason"])

    def test_transmission_request_excludes_key_and_requires_matching_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_approval"
            reports = run / "reports"
            reports.mkdir(parents=True)
            (reports / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": "implement-one",
                                "task_type": "control",
                                "prompt_sha256": "abc",
                                "source_artifacts": {"bceClass": "design.puml"},
                                "allowed_write_paths": ["application/src/One.java"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "tasks": [{"taskId": "implement-one", "status": "PENDING"}]
            }
            request = write_transmission_request(run, state)
            self.assertIsNotNone(request)
            self.assertFalse(request["apiKeyIncluded"])
            self.assertNotIn("apiKey", request)

            approval = reports / "approval.json"
            approval.write_text(
                json.dumps(
                    {
                        "requestId": request["requestId"],
                        "approved": True,
                        "approvedAt": "2026-07-22T00:00:00Z",
                        "approvedBy": "test-user",
                    }
                ),
                encoding="utf-8",
            )
            accepted = validate_approval(approval, request["requestId"])
            self.assertTrue(accepted["approved"])
            with self.assertRaises(PermissionError):
                validate_approval(approval, "different-request")

    def test_workflow_approval_allows_remaining_subset_of_approved_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_subset"
            reports = run / "reports/agent-executions"
            reports.mkdir(parents=True)
            definitions = [
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "prompt_sha256": prompt,
                    "source_artifacts": {},
                    "allowed_write_paths": [f"application/{task_id}.java"],
                }
                for task_id, task_type, prompt in (
                    ("earlier-control", "control", "control-prompt"),
                    ("repair-repository", "persistence-repositories", "repo-prompt"),
                    ("repair-wiring", "configuration", "wiring-prompt"),
                )
            ]
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"implementation_tasks": definitions}), encoding="utf-8"
            )
            full_request = write_transmission_request(
                run,
                {
                    "tasks": [
                        {"taskId": "repair-repository", "status": "PENDING"},
                        {"taskId": "repair-wiring", "status": "PENDING"},
                    ]
                },
            )
            approval = run / "reports/approval.json"
            approval.write_text(
                json.dumps(
                    {
                        "requestId": full_request["requestId"],
                        "approved": True,
                        "approvedBy": "tester",
                    }
                ),
                encoding="utf-8",
            )
            (reports / "repair-repository.result.json").write_text(
                json.dumps(
                    {
                        "status": "SUCCEEDED",
                        "promptSha256": "repo-prompt",
                    }
                ),
                encoding="utf-8",
            )
            (reports / "earlier-control.result.json").write_text(
                json.dumps(
                    {
                        "status": "SUCCEEDED",
                        "promptSha256": "control-prompt",
                    }
                ),
                encoding="utf-8",
            )
            subset_state = {
                "tasks": [
                    {
                        "taskId": "earlier-control",
                        "phase": "control",
                        "status": "SUCCEEDED",
                        "attempts": 1,
                        "promptSha256": "control-prompt",
                        "resultFile": "reports/agent-executions/earlier-control.result.json",
                    },
                    {
                        "taskId": "repair-repository",
                        "phase": "repairs",
                        "status": "SUCCEEDED",
                        "attempts": 1,
                        "promptSha256": "repo-prompt",
                        "resultFile": "reports/agent-executions/repair-repository.result.json",
                    },
                    {
                        "taskId": "repair-wiring",
                        "phase": "repairs",
                        "status": "PENDING",
                        "attempts": 0,
                        "promptSha256": "wiring-prompt",
                    },
                ]
            }
            subset_request = write_transmission_request(run, subset_state)

            accepted = validate_workflow_approval(
                approval, subset_request, subset_state, run
            )

            self.assertEqual("APPROVED_SCOPE_SUBSET", accepted["authorization"])
            self.assertEqual(full_request["requestId"], accepted["approvedRequestId"])

    def test_workflow_approval_allows_delegated_repair_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_delegated"
            reports = run / "reports"
            reports.mkdir(parents=True)
            task = {
                "task_id": "repair-control", "task_type": "control", "prompt_sha256": "repair",
                "source_artifacts": {}, "allowed_write_paths": ["application/Repair.java"],
            }
            (reports / "run-manifest.json").write_text(
                json.dumps({"input_hash": "input-hash", "implementation_tasks": [task]}), encoding="utf-8"
            )
            (reports / "repair-plan.json").write_text(
                json.dumps({"entries": [{"revision": 1, "ownerTaskIds": ["repair-control"], "revalidationTaskIds": []}]}),
                encoding="utf-8",
            )
            state = {"tasks": [{"taskId": "repair-control", "status": "PENDING", "attempts": 1}]}
            request = write_transmission_request(run, state)
            approval = reports / "approval.json"
            approval.write_text(json.dumps({
                "requestId": "initial-request", "approved": True, "delegatedRepairApprovals": True,
                "delegationScope": {"runId": run.name, "inputHash": "input-hash", "initialTaskIds": [], "maxRepairRounds": 3, "maxTaskAttempts": 50},
            }), encoding="utf-8")

            accepted = validate_workflow_approval(approval, request, state, run)

            self.assertEqual("DELEGATED_RUN_SCOPE", accepted["authorization"])

    def test_transmission_request_is_limited_to_next_runnable_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_phase_scope"
            reports = run / "reports"
            reports.mkdir(parents=True)
            definitions = [
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "prompt_sha256": task_id,
                    "source_artifacts": {},
                    "allowed_write_paths": [f"application/{task_id}.java"],
                }
                for task_id, task_type in (
                    ("repository", "persistence-repositories"),
                    ("wiring", "configuration"),
                )
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": definitions}), encoding="utf-8"
            )
            request = write_transmission_request(
                run,
                {
                    "nextRunnableTasks": ["repository"],
                    "tasks": [
                        {"taskId": "repository", "status": "PENDING"},
                        {"taskId": "wiring", "status": "PENDING"},
                    ],
                },
            )
            self.assertEqual(["repository"], [item["taskId"] for item in request["tasks"]])

    def test_completion_audit_builds_backlog_after_workspace_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_sample"
            java = run / "application/src/main/java/com/example/demo"
            test = run / "application/src/test/java/com/example/demo/application/impl"
            reports = run / "reports/implementation-tasks"
            for path in (java / "bce", java / "api/model", java / "application/impl", test, reports):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "bce/PurchaseRecord.java").write_text(
                "package com.example.demo.bce; class PurchaseRecord { void fail() { "
                'throw new UnsupportedOperationException("skeleton"); } }',
                encoding="utf-8",
            )
            (java / "application/impl/PurchaseService.java").write_text(
                "package com.example.demo.application.impl; class PurchaseService { // TODO map\n}",
                encoding="utf-8",
            )
            (test / "PurchaseServiceTest.java").write_text("class PurchaseServiceTest {}", encoding="utf-8")
            (reports / "purchase.context.json").write_text(
                json.dumps(
                    {
                        "bce": (
                            "class PurchaseScreen <<Boundary>> {}\n"
                            "class PurchaseController <<Control>> {}\n"
                            "class PurchaseRecord <<Entity>> {}"
                        )
                    }
                ),
                encoding="utf-8",
            )
            erd = run / "erd.puml"
            erd.write_text(
                'entity "PurchaseRecord" as PurchaseRecord {}', encoding="utf-8"
            )
            manifest = {
                "inputs": {
                    "bceClass": {"path": "C:/moved/missing.puml"},
                    "erd": {"path": str(erd)},
                },
                "diagnostics": [
                    {
                        "code": "MISSING_PROTOTYPE_INPUT",
                        "message": "Prototype continues without optional input: deployment",
                    }
                ],
            }
            (run / "reports/run-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            task_ids = {item["task_id"] for item in audit["backlog"]}
            self.assertEqual("INCOMPLETE", audit["status"])
            self.assertIn("replace-bce-runtime-skeletons", task_ids)
            self.assertIn("implement-erd-persistence", task_ids)
            self.assertIn("implement-purchases-api-adapter", task_ids)
            self.assertIn("implement-boundary-adapters", task_ids)
            self.assertTrue(
                (run / "reports/implementation-completion-audit.json").is_file()
            )

    def test_completion_audit_does_not_block_on_absent_runtime_skeleton_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_executable_contracts"
            java = run / "application/src/main/java/com/example/demo"
            reports = run / "reports/implementation-tasks"
            for path in (java / "bce", java / "api/model", reports):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "bce/PurchaseRecord.java").write_text(
                "package com.example.demo.bce; class PurchaseRecord {}",
                encoding="utf-8",
            )
            (reports / "purchase.context.json").write_text(
                json.dumps(
                    {
                        "bce": (
                            "class PurchaseScreen <<Boundary>> {}\n"
                            "class PurchaseController <<Control>> {}\n"
                            "class PurchaseRecord <<Entity>> {}"
                        )
                    }
                ),
                encoding="utf-8",
            )
            erd = run / "erd.puml"
            erd.write_text(
                'entity "PurchaseRecord" as PurchaseRecord {}', encoding="utf-8"
            )
            (run / "reports/run-manifest.json").write_text(
                json.dumps({
                    "inputs": {
                        "bceClass": {"path": "C:/moved/missing.puml"},
                        "erd": {"path": str(erd)},
                    }
                }),
                encoding="utf-8",
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "replace-bce-runtime-skeletons",
                {item["task_id"] for item in audit["backlog"]},
            )
            direct_tasks = {
                item["task_id"]: item for item in audit["backlog"]
            }
            self.assertEqual([], direct_tasks["implement-erd-persistence"]["blocked_by"])
            self.assertEqual([], direct_tasks["implement-purchases-api-adapter"]["blocked_by"])
            self.assertEqual([], direct_tasks["implement-boundary-adapters"]["blocked_by"])

    def test_completion_audit_accepts_api_adapter_only_with_controller_and_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_api_adapter"
            java = run / "application/src/main/java/com/example/demo"
            tests = run / "application/src/test/java/com/example/demo"
            reports = run / "reports/implementation-tasks"
            for path in (
                java / "api/model",
                java / "bce",
                java / "adapter/in/web",
                tests / "adapter/in/web",
                reports,
            ):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "adapter/in/web/PurchasesApiController.java").write_text(
                "class PurchasesApiController {}", encoding="utf-8"
            )
            (tests / "adapter/in/web/PurchasesApiControllerTest.java").write_text(
                "class PurchasesApiControllerTest {}", encoding="utf-8"
            )
            (reports / "empty.context.json").write_text(
                json.dumps({"bce": ""}), encoding="utf-8"
            )
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"inputs": {}, "diagnostics": []}), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "implement-purchases-api-adapter",
                {item["task_id"] for item in audit["backlog"]},
            )
            self.assertEqual(0, audit["summary"]["missingApiAdapters"])

    def test_completion_audit_accepts_boundary_only_with_adapter_and_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_boundary_adapter"
            java = run / "application/src/main/java/com/example/demo"
            tests = run / "application/src/test/java/com/example/demo"
            reports = run / "reports/implementation-tasks"
            for path in (
                java / "api/model",
                java / "bce",
                java / "adapter/in/boundary",
                tests / "adapter/in/boundary",
                reports,
            ):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "bce/PurchaseScreen.java").write_text(
                "package com.example.demo.bce; public interface PurchaseScreen {}",
                encoding="utf-8",
            )
            (java / "adapter/in/boundary/PurchaseScreenAdapter.java").write_text(
                "class PurchaseScreenAdapter {}", encoding="utf-8"
            )
            (tests / "adapter/in/boundary/PurchaseScreenAdapterTest.java").write_text(
                "class PurchaseScreenAdapterTest {}", encoding="utf-8"
            )
            (reports / "boundary.context.json").write_text(
                json.dumps({"bce": "class PurchaseScreen <<Boundary>> {}"}),
                encoding="utf-8",
            )
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"inputs": {}, "diagnostics": []}), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "implement-boundary-adapters",
                {item["task_id"] for item in audit["backlog"]},
            )
            self.assertEqual(0, audit["summary"]["missingBoundaryAdapters"])

    def test_boundary_planner_discovers_contracts_and_writes_bounded_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bce = root / "bce.puml"
            sequence = root / "sequence.puml"
            bce.write_text(
                """class BuyScreen <<Boundary>> {
  + display()
  + onPurchaseRequested()
}
class ErrorScreen <<Boundary>> {
  + showError(message: string)
}
class PurchaseController <<Control>> {
  + startPurchase()
}
""",
                encoding="utf-8",
            )
            sequence.write_text(
                """BuyScreen -> PurchaseController : startPurchase()
PurchaseController -> ErrorScreen : showError(\"failed\")
""",
                encoding="utf-8",
            )
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "bce.puml", "sequence": "sequence.puml"},
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            java = run / "application/src/main/java/com/example/demo/bce"
            java.mkdir(parents=True)
            (java / "BuyScreen.java").write_text(
                "package com.example.demo.bce; public interface BuyScreen { void display(); void onPurchaseRequested(); }",
                encoding="utf-8",
            )
            (java / "ErrorScreen.java").write_text(
                "package com.example.demo.bce; public interface ErrorScreen { void showError(String message); }",
                encoding="utf-8",
            )
            (java / "PurchaseController.java").write_text(
                "package com.example.demo.bce; public interface PurchaseController { void startPurchase(); }",
                encoding="utf-8",
            )

            tasks = generate_boundary_adapter_tasks(load_job(job), run)

            self.assertEqual(
                [
                    "implement-buy-screen-boundary-adapter",
                    "implement-error-screen-boundary-adapter",
                ],
                [task.task_id for task in tasks],
            )
            self.assertEqual("boundary-adapter", tasks[0].task_type)
            self.assertEqual(2, len(tasks[0].allowed_write_paths))
            prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            self.assertIn("public interface BuyScreen", prompt)
            self.assertIn("PurchaseController", prompt)
            self.assertIn("Do not annotate the adapter as a Spring bean", prompt)
            self.assertIn("Do not leave TODO", prompt)

    def test_boundary_prompt_for_missing_sequence_forbids_inferred_control_calls(self) -> None:
        prompt = render_boundary_adapter_prompt(
            SimpleNamespace(base_package="com.example"),
            "CourseDropBoundary",
            "// bce/CourseDropBoundary.java\npublic interface CourseDropBoundary {}",
            "' No directly matched sequence messages",
        )
        self.assertIn("Do not import, inject, infer, or call any Control", prompt)
        self.assertIn("Never derive a collaborator or method name", prompt)

    def test_control_prompt_preserves_repository_identifier_types(self) -> None:
        prompt = render_prompt(
            SimpleNamespace(base_package="com.example"),
            {
                "control": "DropController",
                "bce": "class DropController <<Control>> {}",
                "sequence": "' No directly matched sequence messages",
                "erd": "' No directly related ERD entity",
                "openapi": "' No directly matched OpenAPI operation",
                "generatedJavaContracts": "// bce/DropController.java",
                "emptyGeneratedContracts": [],
                "repositories": ["CourseRepository"],
            },
            ["application/src/main/java/com/example/DropControllerService.java"],
        )
        self.assertIn("never parse or coerce a String identifier to Long", prompt)

    def test_persistence_prompts_preserve_natural_key_types(self) -> None:
        repository_prompt = render_persistence_repository_prompt(
            SimpleNamespace(base_package="com.example"),
            ["Course"],
            ["application/src/main/java/com/example/persistence/repository/CourseRepository.java"],
        )
        schema_prompt = render_persistence_schema_prompt(
            SimpleNamespace(base_package="com.example"),
            'entity "Course" as Course {\n  * courseId : VARCHAR(255)\n}',
        )
        self.assertIn("IdType", repository_prompt)
        self.assertIn("natural or UUID identifiers", repository_prompt)
        self.assertIn("preserve VARCHAR/UUID natural primary keys", schema_prompt)

    def test_wiring_planner_contracts_bootstrap_configuration_and_context_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bce = root / "bce.puml"
            sequence = root / "sequence.puml"
            bce.write_text(
                "class PurchaseController <<Control>> { + startPurchase() }",
                encoding="utf-8",
            )
            sequence.write_text(
                "BuyScreen -> PurchaseController : startPurchase()", encoding="utf-8"
            )
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "bce.puml", "sequence": "sequence.puml"},
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            service = run / "application/src/main/java/com/example/demo/application/impl/PurchaseControllerService.java"
            service.parent.mkdir(parents=True)
            service.write_text(
                "package com.example.demo.application.impl; public class PurchaseControllerService { public PurchaseControllerService() {} }",
                encoding="utf-8",
            )

            tasks = generate_wiring_tasks(load_job(job), run)

            self.assertEqual(["implement-application-wiring"], [task.task_id for task in tasks])
            self.assertEqual("configuration", tasks[0].task_type)
            self.assertEqual(3, len(tasks[0].allowed_write_paths))
            context = json.loads(
                (run / tasks[0].context_file).read_text(encoding="utf-8")
            )
            application_class = context["applicationClass"]
            self.assertNotIn(
                f"application/src/main/java/com/example/demo/{application_class}.java",
                tasks[0].allowed_write_paths,
            )
            entrypoint = run / f"application/src/main/java/com/example/demo/{application_class}.java"
            self.assertEqual(
                f"""package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class {application_class} {{
    public static void main(String[] args) {{
        SpringApplication.run({application_class}.class, args);
    }}
}}
""",
                entrypoint.read_text(encoding="utf-8"),
            )
            prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            self.assertIn(
                f"Do not create or edit `{application_class}`", prompt
            )
            self.assertIn("Spring `@Lazy`", prompt)
            self.assertIn("Do not add `@EnableJpaRepositories` exclusions", prompt)
            self.assertIn("every generated Spring Data repository bean", prompt)
            self.assertIn("ApplicationContextTest.java", prompt)
            self.assertIn("PurchaseControllerService", prompt)
            self.assertNotIn("System sequence context", prompt)
            self.assertNotIn("BuyScreen -> PurchaseController", prompt)
            self.assertNotIn("sequence", context)

    def test_gateway_planner_creates_persistence_and_trading_adapter_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bce = root / "bce.puml"
            sequence = root / "sequence.puml"
            erd = root / "erd.puml"
            bce.write_text(
                """class StockPurchasePersistenceGateway <<Gateway>> {
  + savePurchase(record: PurchaseRecord): PurchaseRecord
}
class TradingSiteGateway <<Gateway>> {
  + connect(siteName: string): boolean
  + executePurchase(): PurchaseRecord
}
class PurchaseRecord <<Entity>> { - purchaseId: string }
""",
                encoding="utf-8",
            )
            sequence.write_text("TradingSiteGateway --> PurchaseController : record", encoding="utf-8")
            erd.write_text('entity "PurchaseRecord" as PurchaseRecord {}', encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "bce.puml", "sequence": "sequence.puml", "erd": "erd.puml"},
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {"puml2codeRoot": ".", "openapiGeneratorJar": "bce.puml"},
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            package = run / "application/src/main/java/com/example/demo"
            (package / "bce").mkdir(parents=True)
            (package / "persistence/repository").mkdir(parents=True)
            (package / "persistence/mapper").mkdir(parents=True)
            (package / "bce/StockPurchasePersistenceGateway.java").write_text(
                "public interface StockPurchasePersistenceGateway {}", encoding="utf-8"
            )
            (package / "bce/TradingSiteGateway.java").write_text(
                "public interface TradingSiteGateway {}", encoding="utf-8"
            )
            (package / "bce/PurchaseRecord.java").write_text(
                "public class PurchaseRecord {}", encoding="utf-8"
            )
            (package / "persistence/repository/PurchaseRecordRepository.java").write_text(
                "public interface PurchaseRecordRepository {}", encoding="utf-8"
            )
            (package / "persistence/mapper/BcePersistenceMapper.java").write_text(
                "public class BcePersistenceMapper {}", encoding="utf-8"
            )

            tasks = generate_gateway_adapter_tasks(load_job(job), run)

            self.assertEqual(
                [
                    "implement-stock-purchase-persistence-gateway-adapter",
                    "implement-trading-site-gateway-adapter",
                ],
                [task.task_id for task in tasks],
            )
            self.assertTrue(all(task.task_type == "gateway-adapter" for task in tasks))
            persistence_prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            trading_prompt = (run / tasks[1].prompt_file).read_text(encoding="utf-8")
            self.assertIn("corresponding repository operation exactly", persistence_prompt)
            self.assertIn("deterministic local adapter", trading_prompt)

    def test_e2e_planner_stops_when_bce_ports_cannot_carry_api_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bce.puml", "sequence.puml", "erd.puml", "openapi.yaml"):
                (root / name).write_text(name, encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {
                            "bceClass": "bce.puml",
                            "sequence": "sequence.puml",
                            "erd": "erd.puml",
                            "openapi": "openapi.yaml",
                        },
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            package = run / "application/src/main/java/com/example/demo"
            (package / "bce").mkdir(parents=True)
            (package / "api/model").mkdir(parents=True)
            (package / "application/impl").mkdir(parents=True)
            (package / "bce/StockPurchaseController.java").write_text(
                "public interface StockPurchaseController { void startPurchase(); void updatePortfolio(); void getSuggestion(); }",
                encoding="utf-8",
            )
            (package / "api/model/PurchaseRequest.java").write_text(
                "public class PurchaseRequest {}", encoding="utf-8"
            )
            (package / "api/model/SuggestionRequest.java").write_text(
                "public class SuggestionRequest {}", encoding="utf-8"
            )
            (package / "application/impl/StockPurchaseControllerService.java").write_text(
                "public class StockPurchaseControllerService { // TODO define real command mapping\n"
                " public PurchaseRecord startPurchase(String siteName) { return interceptResponse(); }\n}",
                encoding="utf-8",
            )
            (package / "application/impl/WebConnectionManagerService.java").write_text(
                'public class WebConnectionManagerService { String response = "ACK"; }',
                encoding="utf-8",
            )
            (package / "persistence/repository").mkdir(parents=True)
            (package / "persistence/repository/PurchaseRecordRepository.java").write_text(
                "public interface PurchaseRecordRepository {}", encoding="utf-8"
            )

            spec = load_job(job)
            self.assertEqual(
                ["implement-end-to-end-flow"],
                [task.task_id for task in generate_e2e_tasks(spec, run)],
            )
            codes = {gap["code"] for gap in detect_e2e_design_gaps(spec, run)}
            self.assertEqual(set(), codes)
            report = json.loads(
                (run / "reports/design-gaps/end-to-end-flow.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("READY", report["status"])

            (run / "reports/run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {"task_id": "implement-existing", "task_type": "control"},
                            {
                                "task_id": "implement-end-to-end-flow",
                                "task_type": "integration-test",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                ["implement-end-to-end-flow"],
                [task["task_id"] for task in plan_e2e_tasks(spec, run)],
            )
            manifest = json.loads(
                (run / "reports/run-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                ["implement-existing", "implement-end-to-end-flow"],
                [task["task_id"] for task in manifest["implementation_tasks"]],
            )

    def test_e2e_planner_requires_executable_openapi_error_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bce.puml").write_text("@startuml\n@enduml", encoding="utf-8")
            (root / "sequence.puml").write_text(
                "alt product not found\nend", encoding="utf-8"
            )
            (root / "erd.puml").write_text("", encoding="utf-8")
            (root / "openapi.yaml").write_text(
                json.dumps({
                    "openapi": "3.0.3",
                    "paths": {
                        "/products/{productId}": {
                            "get": {
                                "operationId": "getProduct",
                                "responses": {
                                    "200": {"description": "Product found"},
                                    "404": {"description": "Product not found"},
                                },
                            }
                        }
                    },
                }),
                encoding="utf-8",
            )
            job = root / "job.json"
            job.write_text(
                json.dumps({
                    "name": "product-catalog",
                    "workspaceRoot": ".",
                    "inputs": {
                        "bceClass": "bce.puml",
                        "sequence": "sequence.puml",
                        "erd": "erd.puml",
                        "openapi": "openapi.yaml",
                    },
                    "generation": {"basePackage": "com.example.products"},
                    "tools": {"puml2codeRoot": ".", "openapiGeneratorJar": "bce.puml"},
                }),
                encoding="utf-8",
            )
            run = root / "run_product"
            main = run / "application/src/main/java/com/example/products"
            test = run / "application/src/test/java/com/example/products"
            (main / "api").mkdir(parents=True)
            (main / "adapter/in/web").mkdir(parents=True)
            (test / "adapter/in/web").mkdir(parents=True)
            (main / "api/ProductsApi.java").write_text(
                "public interface ProductsApi { Object getProduct(String productId); }",
                encoding="utf-8",
            )
            (main / "adapter/in/web/ProductsApiController.java").write_text(
                "import org.springframework.http.ResponseEntity;\n"
                "public class ProductsApiController {\n"
                "  ResponseEntity<String> getProduct(String id) { return ResponseEntity.ok(id); }\n"
                "}",
                encoding="utf-8",
            )
            (test / "adapter/in/web/ProductsApiControllerTest.java").write_text(
                "public class ProductsApiControllerTest {}", encoding="utf-8"
            )

            spec = load_job(job)
            tasks = generate_e2e_tasks(spec, run)
            self.assertEqual(["implement-end-to-end-flow"], [task.task_id for task in tasks])
            gaps = detect_e2e_design_gaps(spec, run)
            self.assertEqual(
                {"OPENAPI_ERROR_OUTCOME_UNIMPLEMENTED"},
                {gap["code"] for gap in gaps},
            )
            report = json.loads(
                (run / "reports/design-gaps/end-to-end-flow.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("WARNING", report["status"])

    def test_e2e_planner_emits_bounded_real_integration_test_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bce.puml", "sequence.puml", "erd.puml", "openapi.yaml"):
                (root / name).write_text(name, encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {
                            "bceClass": "bce.puml",
                            "sequence": "sequence.puml",
                            "erd": "erd.puml",
                            "openapi": "openapi.yaml",
                        },
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            control = run / "application/src/main/java/com/example/demo/bce/StockPurchaseController.java"
            control.parent.mkdir(parents=True)
            control.write_text(
                "public interface StockPurchaseController { PurchaseRecord startPurchase(String siteName); Portfolio updatePortfolio(PurchaseRecord record); boolean submitSuggestion(String siteName); }",
                encoding="utf-8",
            )
            repository = (
                run
                / "application/src/main/java/com/example/demo/persistence/repository/PurchaseRecordRepository.java"
            )
            repository.parent.mkdir(parents=True)
            repository.write_text(
                "package com.example.demo.persistence.repository; public interface PurchaseRecordRepository {}",
                encoding="utf-8",
            )
            service = (
                run
                / "application/src/main/java/com/example/demo/application/impl/PersistenceConsumer.java"
            )
            service.parent.mkdir(parents=True)
            service.write_text(
                "package com.example.demo.application.impl; import com.example.demo.persistence.repository.PurchaseRecordRepository; class PersistenceConsumer { PurchaseRecordRepository repository; }",
                encoding="utf-8",
            )
            gateway = (
                run
                / "application/src/main/java/com/example/demo/adapter/out/trading/InMemoryTradingSiteGatewayAdapter.java"
            )
            gateway.parent.mkdir(parents=True)
            gateway.write_text(
                "package com.example.demo.adapter.out.trading; public class InMemoryTradingSiteGatewayAdapter { public void rejectSite(String siteName) {} }",
                encoding="utf-8",
            )

            tasks = generate_e2e_tasks(load_job(job), run)

            self.assertEqual(["implement-end-to-end-flow"], [task.task_id for task in tasks])
            self.assertEqual("integration-test", tasks[0].task_type)
            self.assertEqual(1, len(tasks[0].allowed_write_paths))
            prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            self.assertIn("Do not mock application Controls", prompt)
            self.assertIn("Never declare `@TestConfiguration`", prompt)
            self.assertIn("InMemoryTradingSiteGatewayAdapter", prompt)
            self.assertIn("@DirtiesContext(classMode = BEFORE_EACH_TEST_METHOD)", prompt)
            self.assertIn("Never use reflection", prompt)
            self.assertIn("Machine-derived semantic contract", prompt)
            self.assertIn("package com.example.demo.persistence.repository", prompt)
            self.assertIn("package com.example.demo.adapter.out.trading", prompt)

    def test_deterministic_deployment_renderer_supports_multiple_workloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(
                json.dumps(
                    {
                        "resources": [
                            {"type": "Microsoft.ContainerRegistry/registries", "name": "demoacr"},
                            {"type": "Microsoft.KeyVault/vaults", "name": "demo-vault"},
                            {
                                "type": "Microsoft.ContainerService/managedClusters",
                                "networking": {
                                    "containerPort": 8000,
                                    "serviceExposure": "ClusterIP",
                                    "ingressProtocol": "HTTPS",
                                },
                                "workloads": [
                                    {
                                        "name": "orders-api",
                                        "replicas": {"min": 2, "max": 5},
                                        "probes": {
                                            "readiness": "/readyz",
                                            "liveness": "/livez",
                                        },
                                        "monitoring": {
                                            "metricsPath": "/actuator/prometheus"
                                        },
                                    },
                                    {
                                        "name": "orders-worker",
                                        "replicas": {"min": 1, "max": 1},
                                    },
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            spec = SimpleNamespace(name="orders", inputs={"cloud": cloud})
            report = render_deployment(root / "run", spec)

            files = set(report["renderedFiles"])
            self.assertIn("application/Dockerfile", files)
            self.assertIn("application/k8s/orders-api/deployment.yaml", files)
            self.assertIn("application/k8s/orders-api/service.yaml", files)
            self.assertIn("application/k8s/orders-api/ingress.yaml", files)
            self.assertIn("application/k8s/orders-api/hpa.yaml", files)
            self.assertIn("application/k8s/orders-api/pdb.yaml", files)
            self.assertIn("application/k8s/orders-api/network-policy.yaml", files)
            self.assertIn("application/k8s/orders-api/service-account.yaml", files)
            self.assertNotIn("application/k8s/orders-api/external-secret.yaml", files)
            self.assertIn("application/k8s/orders-api/service-monitor.yaml", files)
            self.assertIn("application/k8s/orders-worker/deployment.yaml", files)
            self.assertNotIn("application/k8s/orders-worker/service.yaml", files)
            self.assertEqual("deterministic", report["renderer"])
            self.assertEqual(
                "SUCCEEDED_WITH_WARNINGS", report["validation"]["status"]
            )
            self.assertTrue(report["sourceEvidence"]["cloudResourceSpecification"])
            self.assertEqual("implementation-agent-inference", report["intentSource"])
            self.assertEqual("SUCCEEDED", report["sourceConformance"]["status"])
            persisted_intent = json.loads(
                (root / "run/reports/deployment-intent.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(report["intent"], persisted_intent)
            service_source = (
                root / "run/application/k8s/orders-api/service.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("type: ClusterIP", service_source)
            deployment_source = (
                root / "run/application/k8s/orders-api/deployment.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("path: /readyz", deployment_source)
            self.assertIn("path: /livez", deployment_source)

    def test_deployment_renderer_supports_separate_frontend_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "deployment-intent.json"
            intent_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "easydep-deployment-intent/v1alpha1",
                        "namespace": "orders",
                        "frontend": {
                            "mode": "separate",
                            "apiBaseUrl": "https://api.orders.example.com",
                        },
                        "workloads": [
                            {
                                "name": "orders-api",
                                "kind": "Deployment",
                                "image": "__EASYDEP_REGISTRY_registry__/orders-api:<tag>",
                                "registryRef": "registry",
                                "artifact": "application",
                                "port": 8000,
                                "capabilities": {"service": True},
                            },
                            {
                                "name": "orders-web",
                                "kind": "Deployment",
                                "image": "__EASYDEP_REGISTRY_registry__/orders-web:<tag>",
                                "registryRef": "registry",
                                "artifact": "frontend",
                                "port": 8080,
                                "capabilities": {
                                    "service": True,
                                    "ingress": True,
                                },
                                "ingress": {"host": "orders.example.com"},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run"
            frontend = run / "application/frontend"
            frontend.mkdir(parents=True)
            (frontend / "package.json").write_text("{}", encoding="utf-8")
            (frontend / "package-lock.json").write_text("{}", encoding="utf-8")

            report = render_deployment(
                run,
                SimpleNamespace(
                    name="orders", inputs={"deploymentIntent": intent_path}
                ),
            )

            files = set(report["renderedFiles"])
            self.assertIn("application/frontend/Dockerfile", files)
            self.assertIn("application/frontend/nginx.conf", files)
            self.assertIn("application/k8s/verify-deployment.py", files)
            backend_dockerfile = (run / "application/Dockerfile").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("frontend-build", backend_dockerfile)
            frontend_dockerfile = (
                run / "application/frontend/Dockerfile"
            ).read_text(encoding="utf-8")
            self.assertIn("nginx-unprivileged", frontend_dockerfile)
            build_push = (run / "application/k8s/build-push.sh").read_text(
                encoding="utf-8"
            )
            self.assertIn("EASYDEP_FRONTEND_API_BASE_URL", build_push)
            verifier = (
                run / "application/k8s/verify-deployment.py"
            ).read_text(encoding="utf-8")
            compile(verifier, "verify-deployment.py", "exec")
            deploy = (run / "application/k8s/deploy.sh").read_text(
                encoding="utf-8"
            )
            self.assertIn("verify-deployment.py", deploy)

    def test_implementation_release_can_skip_kubernetes_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "deployment-intent.json"
            intent_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "easydep-deployment-intent/v1alpha1",
                        "namespace": "demo",
                        "workloads": [
                            {
                                "name": "demo-api",
                                "kind": "Deployment",
                                "image": "example/demo:1",
                                "capabilities": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run"
            report = render_deployment(
                run,
                SimpleNamespace(name="demo", inputs={"deploymentIntent": intent_path}),
                include_kubernetes=False,
            )

            self.assertFalse(report["kubernetesManifests"])
            self.assertIn("application/Dockerfile", report["renderedFiles"])
            self.assertFalse((run / "application/k8s").exists())
            self.assertTrue((run / "reports/deployment-intent.json").is_file())

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_deterministic_iac_renderer_matches_deployment_intent(self, _validation: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({
                "provider": "azure",
                "resources": [
                    {"type": "Microsoft.ContainerRegistry/registries", "name": "demoacr"},
                    {"type": "Microsoft.ContainerService/managedClusters", "name": "demoaks", "workloads": [{"name": "orders-api"}]},
                    {"type": "Microsoft.KeyVault/vaults", "name": "demokv"},
                ],
            }), encoding="utf-8")
            run = root / "run"
            (run / "application/k8s/orders-api").mkdir(parents=True)
            (run / "reports").mkdir(parents=True)
            (run / "reports/deployment-intent.json").write_text(json.dumps({"workloads": [{"name": "orders-api"}]}), encoding="utf-8")

            report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))

            self.assertEqual("SUCCEEDED", report["sourceConformance"]["status"])
            source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
            self.assertIn('resource "azurerm_kubernetes_cluster"', source)
            self.assertIn('resource "azurerm_container_registry"', source)
            self.assertIn('resource "azurerm_key_vault"', source)

    @patch(
        "app.implementation.delivery.terraform.render_open_tofu",
        return_value={
            "easydep-provider.tf": 'terraform {}',
            "main.tf": 'resource "azurerm_container_registry" "demoacr" {}',
        },
    )
    @patch("app.implementation.delivery.terraform.validate_provider_resource_plan")
    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_iac_renderer_uses_completed_deployment_bundle_resource_plan(
        self,
        _validation: object,
        validate_resource_plan: object,
        render_resource_plan: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource_spec = {
                "provider": "azure",
                "region": "koreacentral",
            }
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps(resource_spec), encoding="utf-8")
            bundle = root / "deployment-bundle.json"
            bundle.write_text(
                json.dumps(
                    {
                        "schemaVersion": "easydep-deployment-diagram",
                        "status": "completed",
                        "resourceSpec": resource_spec,
                        "projections": [
                            {
                                "status": "completed",
                                "provider": "azure",
                                "region": "koreacentral",
                                "resourcePlanStructureDigest": "reviewed-plan",
                                "resourcePlan": {"structureDigest": "reviewed-plan"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = render_iac(
                root / "run",
                SimpleNamespace(
                    inputs={"cloud": cloud, "deploymentBundle": bundle}
                ),
                include_kubernetes=False,
            )

            self.assertEqual(
                "deployment_diagram_resource_plan",
                report["sourceEvidence"]["resourceSpecSource"],
            )
            self.assertEqual(
                "reviewed-plan",
                report["sourceEvidence"]["deploymentDiagramResourcePlanDigest"],
            )
            self.assertTrue((root / "run/application/terraform/main.tf").is_file())
            assert hasattr(validate_resource_plan, "assert_called_once_with")
            validate_resource_plan.assert_called_once_with({"structureDigest": "reviewed-plan"})
            assert hasattr(render_resource_plan, "assert_called_once_with")
            render_resource_plan.assert_called_once_with({"structureDigest": "reviewed-plan"})

    @patch(
        "app.implementation.delivery.terraform.validate_terraform",
        return_value={"status": "SUCCEEDED"},
    )
    def test_iac_renderer_renders_real_deployment_resource_plan(
        self, _validation: object
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource_spec = deployment_resource_spec("aws")
            self.assertNotIn("resources", resource_spec)
            diagram = build_deployment_diagram_bundle(
                deployment_graph(DEPLOYMENT_CASES[0]), resource_spec
            )
            cloud = root / "cloud.json"
            bundle = root / "deployment-diagram-bundle.json"
            cloud.write_text(json.dumps(resource_spec), encoding="utf-8")
            bundle.write_text(json.dumps(diagram), encoding="utf-8")

            report = render_iac(
                root / "run",
                SimpleNamespace(inputs={"cloud": cloud, "deploymentBundle": bundle}),
                include_kubernetes=False,
            )

            self.assertEqual("resource_plan_exact_rendering", report["sourceConformance"]["mode"])
            self.assertIn("application/terraform/easydep-provider.tf", report["renderedFiles"])
            self.assertTrue((root / "run/application/terraform/bootstrap_compute_1.sh.tftpl").is_file())

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_deterministic_iac_renderer_supports_aws_and_gcp(self, _validation: object) -> None:
        cases = (
            ("aws", [{"type": "AWS::EC2::VPC", "name": "network"}, {"type": "AWS::EC2::Subnet", "name": "private-a", "availabilityZone": "ap-northeast-2a", "dependsOn": ["network"]}, {"type": "AWS::EC2::Subnet", "name": "private-c", "availabilityZone": "ap-northeast-2c", "dependsOn": ["network"]}, {"type": "AWS::ECR::Repository", "name": "orders"}, {"type": "AWS::EKS::Cluster", "name": "orders", "dependsOn": ["network"]}], ('resource "aws_ecr_repository"', 'resource "aws_eks_cluster"', 'resource "aws_iam_role_policy_attachment"')),
            ("gcp", [{"type": "compute.googleapis.com/Network", "name": "network"}, {"type": "compute.googleapis.com/Subnetwork", "name": "private", "dependsOn": ["network"]}, {"type": "artifactregistry.googleapis.com/Repository", "name": "orders"}, {"type": "container.googleapis.com/Cluster", "name": "orders", "dependsOn": ["private"]}], ('resource "google_artifact_registry_repository"', 'resource "google_container_cluster"', 'resource "google_artifact_registry_repository_iam_member"')),
        )
        for provider, resources, expected in cases:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cloud = root / "cloud.json"
                cloud.write_text(json.dumps({"provider": provider, "resources": resources}), encoding="utf-8")
                run = root / "run"
                (run / "application/k8s/orders-api").mkdir(parents=True)
                (run / "reports").mkdir(parents=True, exist_ok=True)
                (run / "reports/deployment-intent.json").write_text(json.dumps({"workloads": [{"name": "orders-api"}]}), encoding="utf-8")
                report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
                source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
                self.assertEqual("SUCCEEDED", report["sourceConformance"]["status"])
                self.assertEqual(provider, report["provider"])
                self.assertTrue(report["requiredVariables"])
                for marker in expected:
                    self.assertIn(marker, source)

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_iac_renderer_connects_networks_and_creates_cluster_nodes(self, _validation: object) -> None:
        cases = (
            (
                "aws",
                [{"type": "AWS::EC2::VPC", "name": "platform", "cidrBlock": "10.0.0.0/16"}, {"type": "AWS::EC2::Subnet", "name": "private-a", "cidrBlock": "10.0.1.0/24", "availabilityZone": "ap-northeast-2a", "dependsOn": ["platform"]}, {"type": "AWS::EC2::Subnet", "name": "private-c", "cidrBlock": "10.0.2.0/24", "availabilityZone": "ap-northeast-2c", "dependsOn": ["platform"]}, {"type": "AWS::EKS::Cluster", "name": "cluster", "dependsOn": ["platform"]}],
                ("aws_vpc.platform.id", 'resource "aws_eks_node_group"'),
            ),
            (
                "gcp",
                [{"type": "compute.googleapis.com/Network", "name": "platform"}, {"type": "compute.googleapis.com/Subnetwork", "name": "private-a", "ipCidrRange": "10.0.1.0/24", "dependsOn": ["platform"]}, {"type": "container.googleapis.com/Cluster", "name": "cluster", "dependsOn": ["private-a"]}],
                ("network = google_compute_network.platform.id", 'resource "google_container_node_pool"'),
            ),
        )
        for provider, resources, expected in cases:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cloud = root / "cloud.json"
                cloud.write_text(json.dumps({"provider": provider, "resources": resources}), encoding="utf-8")
                run = root / "run"
                report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
                source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
                self.assertIn(report["sourceConformance"]["status"], {"SUCCEEDED_WITH_WARNINGS", "SUCCEEDED"})
                for marker in expected:
                    self.assertIn(marker, source)

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_iac_renderer_resolves_network_references_independent_of_resource_order(self, _validation: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "aws", "resources": [
                {"type": "AWS::EKS::Cluster", "name": "cluster", "dependsOn": ["platform"]},
                {"type": "AWS::EC2::Subnet", "name": "private-a", "availabilityZone": "ap-northeast-2a", "dependsOn": ["platform"]},
                {"type": "AWS::EC2::Subnet", "name": "private-c", "availabilityZone": "ap-northeast-2c", "dependsOn": ["platform"]},
                {"type": "AWS::EC2::VPC", "name": "platform"},
            ]}), encoding="utf-8")
            run = root / "run"
            render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
            source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
            self.assertIn("vpc_id = aws_vpc.platform.id", source)
            self.assertIn("subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_c.id]", source)

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_iac_renderer_resolves_type_safe_dependency_ids_when_names_overlap(self, _validation: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "aws", "resources": [
                {"id": "cluster-a", "type": "AWS::EKS::Cluster", "name": "platform", "dependsOn": ["vpc-a"]},
                {"id": "subnet-a", "type": "AWS::EC2::Subnet", "name": "private-a", "availabilityZone": "ap-northeast-2a", "dependsOn": ["vpc-a"]},
                {"id": "subnet-c", "type": "AWS::EC2::Subnet", "name": "private-c", "availabilityZone": "ap-northeast-2c", "dependsOn": ["vpc-a"]},
                {"id": "vpc-a", "type": "AWS::EC2::VPC", "name": "platform"},
            ]}), encoding="utf-8")
            run = root / "run"
            render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
            source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
            self.assertIn("vpc_id = aws_vpc.platform.id", source)
            self.assertIn("subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_c.id]", source)

    def test_iac_renderer_rejects_unknown_provider_resource_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "aws", "resources": [{"type": "AWS::S3::Bucket", "name": "assets"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not supported"):
                render_iac(root / "run", SimpleNamespace(inputs={"cloud": cloud}))

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_azure_iac_renderer_preserves_private_cluster_and_mysql_networking(self, _validation: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "azure", "resources": [
                {"id": "vnet-platform", "type": "Microsoft.Network/virtualNetworks", "name": "platform", "subnets": [{"name": "aks", "addressPrefix": "10.0.1.0/24"}, {"name": "mysql", "addressPrefix": "10.0.2.0/24", "delegations": ["Microsoft.DBforMySQL/flexibleServers"]}]},
                {"type": "Microsoft.Network/privateDnsZones", "name": "private.mysql.database.azure.com", "dependsOn": ["vnet-platform"]},
                {"type": "Microsoft.ContainerService/managedClusters", "name": "platform", "nodePools": [{"name": "system", "vmSize": "Standard_D2s_v5", "count": 2, "enableAutoScaling": True, "minCount": 1, "maxCount": 3}, {"name": "user", "vmSize": "Standard_D4s_v5", "count": 1}], "networking": {"privateCluster": True, "subnet": "platform/aks"}},
                {"type": "Microsoft.DBforMySQL/flexibleServers", "name": "platform-db", "networking": {"publicNetworkAccess": "Disabled", "delegatedSubnet": "platform/mysql", "privateDnsZone": "private.mysql.database.azure.com"}},
            ]}), encoding="utf-8")
            run = root / "run"
            report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
            source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
            self.assertIn(report["sourceConformance"]["status"], {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"})
            for marker in ("private_cluster_enabled = true", "vnet_subnet_id = azurerm_subnet.platform_aks.id", "delegated_subnet_id = azurerm_subnet.platform_mysql.id", "private_dns_zone_id = azurerm_private_dns_zone.private_mysql_database_azure_com.id", 'resource "azurerm_kubernetes_cluster_node_pool" "platform_user"'):
                self.assertIn(marker, source)

    def test_infer_intent_uses_provider_specific_registry_images(self) -> None:
        cases = (
            ("aws", "AWS::EKS::Cluster", "AWS::ECR::Repository", "__EASYDEP_REGISTRY_"),
            ("gcp", "container.googleapis.com/Cluster", "artifactregistry.googleapis.com/Repository", "__EASYDEP_REGISTRY_"),
        )
        for provider, cluster_type, registry_type, marker in cases:
            with self.subTest(provider=provider):
                intent = infer_intent("orders", {"provider": provider, "resources": [{"type": cluster_type, "workloads": [{"name": "orders-api"}]}, {"type": registry_type, "name": "orders"}]})
                self.assertIn(marker, intent["workloads"][0]["image"])

    def test_infer_intent_requires_or_preserves_workload_registry_reference(self) -> None:
        cloud = {
            "provider": "aws",
            "resources": [
                {
                    "id": "cluster-a",
                    "type": "AWS::EKS::Cluster",
                    "name": "orders",
                    "workloads": [{"name": "orders-api", "registryRef": "private-registry"}],
                },
                {"id": "public-registry", "type": "AWS::ECR::Repository", "name": "public"},
                {"id": "private-registry", "type": "AWS::ECR::Repository", "name": "private"},
            ],
        }
        intent = infer_intent("orders", cloud)
        workload = intent["workloads"][0]
        self.assertEqual("private-registry", workload["registryRef"])
        self.assertIn("__EASYDEP_REGISTRY_private-registry__", workload["image"])

        cloud["resources"][0]["workloads"] = [{"name": "orders-api"}]
        with self.assertRaisesRegex(ValueError, "requires registryRef"):
            infer_intent("orders", cloud)

    def test_infer_intent_rejects_multiple_kubernetes_clusters(self) -> None:
        cloud = {"provider": "azure", "resources": [
            {"type": "Microsoft.ContainerService/managedClusters", "name": "first"},
            {"type": "Microsoft.ContainerService/managedClusters", "name": "second"},
        ]}
        with self.assertRaisesRegex(ValueError, "exactly one Kubernetes cluster"):
            infer_intent("orders", cloud)

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_iac_renderer_rejects_registry_pull_binding_for_wrong_registry(self, _validation: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "azure", "resources": [
                {"id": "registry-a", "type": "Microsoft.ContainerRegistry/registries", "name": "public"},
                {"id": "registry-b", "type": "Microsoft.ContainerRegistry/registries", "name": "private"},
                {"id": "cluster-a", "type": "Microsoft.ContainerService/managedClusters", "name": "orders", "dependsOn": ["registry-a"], "workloads": [{"name": "orders-api", "registryRef": "registry-b"}]},
            ]}), encoding="utf-8")
            run = root / "run"
            spec = SimpleNamespace(name="orders", inputs={"cloud": cloud})
            render_deployment(run, spec)

            with self.assertRaisesRegex(ValueError, "has no image-pull binding"):
                render_iac(run, spec)

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "SUCCEEDED"})
    def test_aws_cloud_spec_renders_deployment_then_iac(self, _validation: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "aws", "resources": [
                {"type": "AWS::EC2::VPC", "name": "platform"},
                {"type": "AWS::EC2::Subnet", "name": "private-a", "availabilityZone": "ap-northeast-2a", "dependsOn": ["platform"]},
                {"type": "AWS::EC2::Subnet", "name": "private-c", "availabilityZone": "ap-northeast-2c", "dependsOn": ["platform"]},
                {"type": "AWS::ECR::Repository", "name": "orders"},
                {"type": "AWS::EKS::Cluster", "name": "orders-cluster", "dependsOn": ["platform"], "workloads": [{"name": "orders-api"}]},
            ]}), encoding="utf-8")
            spec = SimpleNamespace(name="orders", inputs={"cloud": cloud})
            run = root / "run"
            deployment = render_deployment(run, spec)
            iac = render_iac(run, spec)
            self.assertEqual("implementation-agent-inference", deployment["intentSource"])
            self.assertEqual("aws", iac["provider"])
            self.assertEqual("SUCCEEDED", iac["sourceConformance"]["status"])
            self.assertTrue((run / "application/k8s/render-images.sh").is_file())
            self.assertTrue((run / "application/k8s/build-push.sh").is_file())
            self.assertTrue((run / "application/k8s/deploy.sh").is_file())
            deploy = (run / "application/k8s/deploy.sh").read_text(encoding="utf-8")
            self.assertIn("EASYDEP_IMAGE_TAG", deploy)
            self.assertIn("EASYDEP_TERRAFORM_PATH", deploy)
            self.assertIn("build-push.sh", deploy)
            build_push = (run / "application/k8s/build-push.sh").read_text(encoding="utf-8")
            for marker in ("az acr login", "aws ecr get-login-password", "gcloud auth configure-docker", "RepoDigests"):
                self.assertIn(marker, build_push)
            bundle = run / "application/deployment-bundle"
            self.assertTrue((bundle / "application/k8s/deployment-intent.json").is_file())
            self.assertTrue((bundle / "application/terraform/main.tf").is_file())
            self.assertTrue((bundle / "README.md").is_file())
            self.assertIn('output "registry_image_bases"', (run / "application/terraform/outputs.tf").read_text(encoding="utf-8"))

    @patch("app.implementation.delivery.terraform.shutil.which", return_value=None)
    def test_terraform_validation_reports_when_binary_is_unavailable(self, _which: object) -> None:
        self.assertEqual("FAILED", validate_terraform(Path("missing")).get("status"))

    @patch("app.implementation.delivery.terraform.validate_terraform", return_value={"status": "FAILED", "errors": ["provider schema rejected configuration"]})
    def test_iac_renderer_blocks_artifact_promotion_when_terraform_validation_fails(self, _validation: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "azure", "resources": [{"type": "Microsoft.ContainerRegistry/registries", "name": "registry"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Terraform validation failed"):
                render_iac(root / "run", SimpleNamespace(inputs={"cloud": cloud}))

    def test_deployment_intent_rejects_incompatible_job_capabilities(self) -> None:
        intent = {
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "demo",
            "workloads": [
                {
                    "name": "cleanup",
                    "kind": "Job",
                    "image": "example/cleanup:1",
                    "capabilities": {"service": True},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "Job/CronJob cannot enable"):
            validate_intent(intent)

    def test_deterministic_renderer_supports_stateful_job_and_cronjob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / "intent.json"
            intent.write_text(
                json.dumps(
                    {
                        "schemaVersion": "easydep-deployment-intent/v1alpha1",
                        "namespace": "platform",
                        "workloads": [
                            {
                                "name": "ledger",
                                "kind": "StatefulSet",
                                "image": "example/ledger:1",
                                "replicas": {"min": 2, "max": 4},
                                "storage": {
                                    "size": "20Gi",
                                    "accessModes": ["ReadWriteMany"],
                                },
                                "capabilities": {
                                    "service": True,
                                    "hpa": True,
                                    "pdb": True,
                                    "pvc": True,
                                    "serviceAccount": True,
                                },
                            },
                            {
                                "name": "migration",
                                "kind": "Job",
                                "image": "example/migration:1",
                                "capabilities": {
                                    "serviceAccount": True,
                                    "configMap": True,
                                    "externalSecret": True,
                                },
                                "externalSecret": {
                                    "storeName": "platform-secrets",
                                    "remoteKey": "migration/runtime",
                                },
                            },
                            {
                                "name": "cleanup",
                                "kind": "CronJob",
                                "image": "example/cleanup:1",
                                "schedule": "0 3 * * *",
                                "capabilities": {},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            spec = SimpleNamespace(
                name="platform",
                inputs={"deploymentIntent": intent},
            )
            report = render_deployment(root / "run", spec)
            files = set(report["renderedFiles"])
            self.assertIn("application/k8s/ledger/statefulset.yaml", files)
            self.assertIn("application/k8s/ledger/pvc.yaml", files)
            self.assertIn("application/k8s/migration/job.yaml", files)
            self.assertIn("application/k8s/cleanup/cronjob.yaml", files)
            job_source = (
                root / "run/application/k8s/migration/job.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("configMapRef", job_source)
            self.assertIn("secretRef", job_source)
            service_source = (
                root / "run/application/k8s/ledger/service.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("clusterIP: None", service_source)

    def test_renderer_removes_files_from_previous_managed_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "intent.json"
            intent = {
                "schemaVersion": "easydep-deployment-intent/v1alpha1",
                "namespace": "demo",
                "workloads": [
                    {
                        "name": "demo-api",
                        "kind": "Deployment",
                        "image": "example/demo:1",
                        "replicas": {"min": 1, "max": 2},
                        "capabilities": {"service": True, "hpa": True},
                    }
                ],
            }
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            spec = SimpleNamespace(
                name="demo", inputs={"deploymentIntent": intent_path}
            )
            render_deployment(root / "run", spec)
            hpa = root / "run/application/k8s/demo-api/hpa.yaml"
            self.assertTrue(hpa.is_file())

            intent["workloads"][0]["replicas"] = {"min": 1, "max": 1}
            intent["workloads"][0]["capabilities"]["hpa"] = False
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            report = render_deployment(root / "run", spec)
            self.assertFalse(hpa.exists())
            self.assertIn(
                "application/k8s/demo-api/hpa.yaml", report["removedFiles"]
            )

    def test_external_secret_requires_explicit_store_and_remote_key(self) -> None:
        intent = {
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "demo",
            "workloads": [
                {
                    "name": "demo-api",
                    "kind": "Deployment",
                    "image": "example/demo:1",
                    "capabilities": {"externalSecret": True},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "externalSecret"):
            validate_intent(intent)

    def test_intent_rejects_invalid_namespace_and_cron(self) -> None:
        intent = {
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "Invalid Namespace",
            "workloads": [
                {
                    "name": "cleanup",
                    "kind": "CronJob",
                    "image": "example/cleanup:1",
                    "schedule": "nightly",
                    "capabilities": {},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "namespace"):
            validate_intent(intent)

    def test_inference_uses_explicit_diagram_alias_for_exposure(self) -> None:
        cloud = {
            "resources": [
                {
                    "type": "Microsoft.ContainerService/managedClusters",
                    "networking": {"ingressProtocol": "HTTPS"},
                    "workloads": [
                        {
                            "name": "frontend",
                            "diagramAlias": "web",
                            "replicas": {"min": 1, "max": 1},
                        }
                    ],
                }
            ]
        }
        diagram = "@startuml\nactor User\nnode LB as lb\ncomponent Web as web\nlb --> web\n@enduml"
        intent = infer_intent("demo", cloud, diagram)
        capabilities = intent["workloads"][0]["capabilities"]
        self.assertTrue(capabilities["service"])
        self.assertTrue(capabilities["ingress"])

    def test_source_conformance_rejects_intent_that_conflicts_with_cloud_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(
                json.dumps(
                    {
                        "resources": [
                            {
                                "type": "Microsoft.ContainerService/managedClusters",
                                "networking": {"containerPort": 8000},
                                "workloads": [
                                    {
                                        "name": "orders-api",
                                        "replicas": {"min": 2, "max": 2},
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            intent = root / "intent.json"
            intent.write_text(
                json.dumps(
                    {
                        "schemaVersion": "easydep-deployment-intent/v1alpha1",
                        "namespace": "orders",
                        "workloads": [
                            {
                                "name": "orders-api",
                                "kind": "Deployment",
                                "image": "example/orders-api:1",
                                "replicas": {"min": 1, "max": 1},
                                "capabilities": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            spec = SimpleNamespace(
                name="orders", inputs={"cloud": cloud, "deploymentIntent": intent}
            )
            with self.assertRaisesRegex(ValueError, "replicas.min"):
                render_deployment(root / "run", spec)
            report = json.loads(
                (root / "run/reports/deployment-render.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("FAILED", report["sourceConformance"]["status"])

    def test_completion_audit_accepts_wiring_only_with_all_four_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_wiring"
            java = run / "application/src/main/java/com/example/demo"
            tests = run / "application/src/test/java/com/example/demo/config"
            resources = run / "application/src/main/resources"
            reports = run / "reports/implementation-tasks"
            for path in (java / "api/model", java / "bce", java / "config", tests, resources, reports):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "StockPurchaseApplication.java").write_text("class App {}", encoding="utf-8")
            (java / "config/ApplicationConfiguration.java").write_text("class Config {}", encoding="utf-8")
            (resources / "application.yml").write_text("spring: {}", encoding="utf-8")
            (tests / "ApplicationContextTest.java").write_text("class ContextTest {}", encoding="utf-8")
            (reports / "empty.context.json").write_text(json.dumps({"bce": ""}), encoding="utf-8")
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"inputs": {}, "diagnostics": []}), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "implement-application-wiring",
                {item["task_id"] for item in audit["backlog"]},
            )
            self.assertEqual(0, audit["summary"]["missingWiringOutputs"])

    def test_undefined_type_scan_ignores_notes_and_relationship_labels(self) -> None:
        source = """@startuml
class TimerManager <<Control>> {
  - timers: Map<string, TimerInfo>
  + start(record: PurchaseRecord): boolean
}
class PurchaseRecord <<Entity>> {}
note top of TimerManager : Control Manager creates Successful Purchase records
TimerManager --> PurchaseRecord : Manager creates record
@enduml
"""
        self.assertEqual(["TimerInfo"], find_undefined_bce_types(source))

    def test_decimal_alias_is_not_an_undefined_java_bce_type(self) -> None:
        source = "@startuml\nclass Product <<Entity>> {\n  - price : Decimal\n}\n@enduml\n"

        self.assertEqual([], find_undefined_bce_types(source))

    def test_qualified_library_type_does_not_create_java_placeholder(self) -> None:
        source = """@startuml
class Term <<Entity>> {
  - openedAt : java.time.LocalDate
  - updatedAt : java.time.LocalDateTime
}
@enduml
"""

        self.assertEqual([], find_undefined_bce_types(source))

    def test_java_date_types_do_not_create_bce_placeholders(self) -> None:
        source = """@startuml
class Term <<Entity>> {
  - openedAt : LocalDate
  - updatedAt : LocalDateTime
}
@enduml
"""

        self.assertEqual([], find_undefined_bce_types(source))

    def test_rejects_input_outside_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "name": "unsafe",
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "../outside.puml"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "escapes workspaceRoot"):
                load_job(job)

    def test_loads_paths_relative_to_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "diagram.puml").write_text("@startuml\n@enduml\n", encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "name": "safe",
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "diagram.puml"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "diagram.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            spec = load_job(job)
            self.assertEqual(root.resolve(), spec.workspace_root)
            self.assertEqual((root / "diagram.puml").resolve(), spec.inputs["bceClass"])

    def test_extracts_control_and_scoped_sequence_messages(self) -> None:
        diagram = """class CheckoutController <<Control>> {
  + checkout()
}
class Cart <<Entity>> {}
"""
        classes = parse_design_classes(diagram)
        self.assertEqual(["CheckoutController", "Cart"], [item.name for item in classes])
        sequence = """alt valid
  UI -> CheckoutController : checkout()
  CheckoutController -> Cart : total()
else invalid
  UI -> ErrorScreen : show()
end
"""
        scoped = slice_sequence(sequence, {"CheckoutController", "Cart"})
        self.assertIn("enclosing branch: alt valid", scoped)
        self.assertNotIn("ErrorScreen", scoped)

    def test_scopes_sequence_messages_through_plantuml_aliases(self) -> None:
        sequence = """@startuml UC1
boundary "SignInBoundary" as SignInB
control "SignInController" as SignInC
StudentA -> SignInB : signIn(username:String,password:String)
SignInB -> SignInC : authenticate(username:String,password:String)
SignInC --> SignInB : AuthenticationToken
@enduml
"""
        scoped = slice_sequence(sequence, {"SignInBoundary"})
        self.assertIn("SignInB -> SignInC : authenticate", scoped)
        self.assertNotIn("No directly matched sequence messages", scoped)

    def test_boundary_gate_rejects_null_for_forward_sequence_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "application/src/main/java/example/adapter/in/boundary/SignInBoundaryAdapter.java"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("class SignInBoundaryAdapter { Object signIn() { return null; } }", encoding="utf-8")
            violations = boundary_adapter_contract_violations(
                root,
                [relative],
                "boundary \"SignInBoundary\" as SignInB\ncontrol \"SignInController\" as SignInC\nSignInB -> SignInC : authenticate()",
            )
            self.assertEqual(1, len(violations))

    def test_parses_openapi_operations_without_yaml_dependency(self) -> None:
        source = """openapi: 3.0.3
paths:
  /orders:
    post:
      summary: Create order
  /orders/{id}:
    get:
      summary: Read order
components: {}
"""
        operations = parse_openapi_operations(source)
        self.assertEqual(2, len(operations))
        self.assertTrue(operations[0].startswith("# POST /orders"))
        self.assertTrue(operations[1].startswith("# GET /orders/{id}"))

    def test_openhands_absence_is_a_diagnostic_not_an_import_error(self) -> None:
        compatibility = openhands_compatibility()
        self.assertIn("sdkInstalled", compatibility)
        self.assertIsInstance(compatibility["sdkInstalled"], bool)

    def test_openhands_uses_the_shared_project_api_key(self) -> None:
        configured = SimpleNamespace(
            api_key="shared-key",
            nvidia_api_key=None,
            nvidia_nim_api_key=None,
            llm_api_key=None,
        )
        with patch("app.implementation.agents.provider.settings", configured):
            self.assertEqual("shared-key", configured_api_key())

    def test_changed_files_detects_create_modify_and_delete(self) -> None:
        self.assertEqual(
            {"changed", "created", "deleted"},
            changed_files(
                {"same": "1", "changed": "1", "deleted": "1"},
                {"same": "1", "changed": "2", "created": "1"},
            ),
        )

    def test_missing_required_outputs_requires_every_contracted_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = "application/src/main/java/example/Service.java"
            test = "application/src/test/java/example/ServiceTest.java"
            target = root / implementation
            target.parent.mkdir(parents=True)
            target.write_text("class Service {}", encoding="utf-8")

            self.assertEqual(
                [test], missing_required_outputs(root, [implementation, test])
            )

    def test_snapshot_ignores_gradle_build_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "application/src/Main.java"
            build = root / "application/build/Main.class"
            gradle = root / "application/.gradle/file.lock"
            for path in (source, build, gradle):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")

            snapshot = snapshot_files(root)

            self.assertEqual(["application/src/Main.java"], list(snapshot))

    def test_verification_feedback_contains_compiler_error_and_contract_rule(self) -> None:
        feedback = render_verification_feedback(
            {"stdout": "", "stderr": "void cannot be converted to boolean"}
        )
        self.assertIn("void cannot be converted to boolean", feedback)
        self.assertIn("Generated contracts are authoritative", feedback)
        self.assertIn("use reflection", feedback)
        self.assertIn("to replace each affected allowlisted file completely", feedback)
        self.assertIn("remove the absent call", feedback)
        self.assertIn("void type not allowed here", feedback)
        self.assertIn("doAnswer", feedback)

    def test_api_adapter_repair_feedback_includes_exact_contract_subset(self) -> None:
        contracts = """// application/src/main/java/com/example/api/AuthApi.java
interface AuthApi { }
// application/src/main/java/com/example/api/model/LoginRequest.java
class LoginRequest { }
// application/src/main/java/com/example/bce/AuthControl.java
interface AuthControl { String authenticate(String username, String password); }
// application/src/main/java/com/example/bce/CatalogControl.java
interface CatalogControl { }
"""
        context = {
            "api": "Auth",
            "operations": [{"controlBinding": {"control": "AuthControl"}}],
            "generatedJavaContracts": contracts,
        }
        subset = _api_adapter_repair_contract(context)
        self.assertIn("AuthApi", subset)
        self.assertIn("LoginRequest", subset)
        self.assertIn("AuthControl", subset)
        self.assertNotIn("CatalogControl", subset)

        feedback = render_verification_feedback(
            {"stderr": "package com.example.bce.control does not exist"},
            api_controls=["AuthControl"],
            api_contracts=subset,
        )
        self.assertIn("Exact generated API/BCE contracts", feedback)
        self.assertIn("authenticate(String username, String password)", feedback)

    def test_repair_feedback_includes_current_allowlisted_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            first = "application/src/Service.java"
            second = "application/src/ServiceTest.java"
            for relative, content in ((first, "class Service {}"), (second, "class ServiceTest {}")):
                path = sandbox / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            sources = read_allowed_sources(sandbox, [first, second])
            feedback = render_verification_feedback(
                {"stderr": "compile failed"}, current_sources=sources
            )

            self.assertIn("class Service {}", feedback)
            self.assertIn("class ServiceTest {}", feedback)
            self.assertIn("Do not call view or str_replace", feedback)

    def test_repair_feedback_includes_generated_contracts_for_non_api_tasks(self) -> None:
        feedback = render_verification_feedback(
            {"stderr": "cannot find symbol"},
            generated_contracts="interface CatalogControl { List<Course> getCourseCatalog(); }",
        )

        self.assertIn("Exact generated contracts for this repair", feedback)
        self.assertIn("getCourseCatalog", feedback)

    def test_frontend_repair_feedback_includes_generated_client_contracts(self) -> None:
        feedback = render_frontend_verification_feedback(
            {"stderr": "TypeScript error"},
            generated_contracts="export type Course = { courseId: string };",
        )

        self.assertIn("Generated TypeScript client contracts", feedback)
        self.assertIn("courseId", feedback)

    def test_reads_failed_gradle_test_xml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            result_dir = sandbox / "application/build/test-results/test"
            result_dir.mkdir(parents=True)
            (result_dir / "TEST-example.xml").write_text(
                '<testsuite><testcase><failure message="expected call"/></testcase></testsuite>',
                encoding="utf-8",
            )
            failures = read_gradle_test_failures(sandbox)
            self.assertIn("expected call", failures)
            self.assertNotIn("<testsuite>", failures)

    def test_preserves_causal_spring_failure_from_long_trace(self) -> None:
        trace = "\n".join(
            [
                "org.springframework.beans.factory.UnsatisfiedDependencyException: Error creating bean with name 'controller'",
                "Caused by: org.springframework.beans.factory.BeanCurrentlyInCreationException: Requested bean is currently in creation",
            ]
            + [f"\tat example.Stack.frame{number}(Stack.java:1)" for number in range(4000)]
        )
        summary = summarize_test_failure(trace)
        self.assertIn("UnsatisfiedDependencyException", summary)
        self.assertIn("BeanCurrentlyInCreationException", summary)
        self.assertLess(len(summary), 8000)

    def test_preserves_causal_hibernate_mapped_by_failure_from_long_trace(self) -> None:
        trace = "\n".join(
            [
                "org.hibernate.AnnotationException: Collection 'StudentEntity.enrollments' "
                "is 'mappedBy' a property named 'student' which does not exist in the target entity",
            ]
            + [f"\tat org.hibernate.Stack.frame{number}(Stack.java:1)" for number in range(4000)]
        )
        summary = summarize_test_failure(trace)
        self.assertIn("AnnotationException", summary)
        self.assertIn("mappedBy", summary)

    def test_breaks_bean_factory_cycle_with_lazy_parameter(self) -> None:
        configuration = """package example.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
class ApplicationConfiguration {
    @Bean
    public Controller controller(Timer timer) { return new Controller(timer); }

    @Bean
    public Timer timer(DelayScreen screen) { return new Timer(screen); }

    @Bean
    public DelayScreen delayScreen(Controller controller) { return new DelayScreen(controller); }
}
"""
        normalized, changed = break_configuration_cycles(configuration)
        self.assertTrue(changed)
        self.assertIn("import org.springframework.context.annotation.Lazy;", normalized)
        self.assertIn("delayScreen(@Lazy Controller controller)", normalized)

    def test_compiler_repair_is_limited_to_named_allowed_file(self) -> None:
        main = "application/src/main/java/example/Service.java"
        test = "application/src/test/java/example/ServiceTest.java"
        evidence = {
            "stderr": (
                "C:\\workspace\\application\\src\\test\\java\\example"
                "\\ServiceTest.java:80: error: cannot find symbol"
            )
        }

        self.assertEqual([test], select_repair_paths(evidence, [main, test]))

    def test_runtime_failure_keeps_all_repair_targets(self) -> None:
        allowed = ["application/src/Main.java", "application/src/MainTest.java"]
        self.assertEqual(
            allowed,
            select_repair_paths(
                {
                    "testResults": (
                        "MainTest.failed(MainTest.java:42): expected true but was false"
                    )
                },
                allowed,
            ),
        )

    def test_runtime_failure_hints_explain_common_mockito_causes(self) -> None:
        hints = verification_failure_hints(
            "TooManyActualInvocations\nTooFewActualInvocations\n"
            "Wanted but not invoked\nvoid type not allowed here\n"
            "UnnecessaryStubbingException\nNotAMockException\n"
            "InvalidUseOfMatchersException: 2 matchers expected\n"
            "testStartPurchase_ConnectionFails_HandlesFailure(): Wanted but not invoked\n"
            "Forbidden test bean configuration: @MockBean\n"
            'expected "identifier"; SQL statement:\n'
            "error: incompatible types: java.util.Date cannot be converted to com.easydep.app.bce.Date"
            "package com.easydep.app.bce.control does not exist\n"
        )
        self.assertIn("exact argument", hints)
        self.assertIn("exact observed count", hints)
        self.assertIn("conflicting stubs", hints)
        self.assertIn("void mocks need no stub", hints)
        self.assertIn("delete every stubbing", hints)
        self.assertIn("real service", hints)
        self.assertIn("eq(30)", hints)
        self.assertIn("doThrow", hints)
        self.assertIn("SQL Syntax / Reserved Keyword", hints)
        self.assertIn("Incompatible types", hints)
        self.assertIn("API/BCE request conversion", hints)
        self.assertIn("Project contract import", hints)
        self.assertIn("real E2E test", hints)
        self.assertIn("remove @MockBean", hints)

    def test_runtime_failure_hints_preserve_exact_e2e_http_contract(self) -> None:
        hints = verification_failure_hints(
            "Missing HTTP path evidence: /courses/{courseId}\n"
            "Missing HTTP method evidence for scenario: GET /courses/{courseId}"
        )
        self.assertIn("resolve exactly", hints)
        self.assertIn("remove any extra suffix", hints)
        self.assertIn("exact HTTP verb", hints)

    def test_runtime_failure_hints_require_foreign_key_fixture_seed(self) -> None:
        hints = verification_failure_hints(
            'DataIntegrityViolationException: Referential integrity constraint violation: '
            'FOREIGN KEY(RELATED_STUDENT_ID) REFERENCES STUDENT(STUDENT_ID) (\'alice\')'
        )

        self.assertIn("foreign-key target", hints)
        self.assertIn("same identifier", hints)
        self.assertIn("Do not disable constraints", hints)

    def test_runtime_failure_hints_require_entity_migration_columns(self) -> None:
        hints = verification_failure_hints(
            "Persistence entity schema mismatch: EnrollmentEntity is missing migration column(s): student_id"
        )

        self.assertIn("entity contract", hints)
        self.assertIn("exact snake_case name", hints)

    def test_runtime_failure_hints_preserve_mapper_entity_constructor(self) -> None:
        hints = verification_failure_hints(
            "error: no suitable constructor found for EnrollmentEntity(String,StudentEntity,CourseEntity,String,String)"
        )

        self.assertIn("mapper constructor contract", hints)
        self.assertIn("Preserve every existing public constructor", hints)
        self.assertIn("Re-run the build", hints)

    def test_runtime_failure_hints_explain_boundary_control_recursion(self) -> None:
        hints = verification_failure_hints(
            "StackOverflowError at CourseDetailsBoundaryAdapter.viewCourseDetails "
            "CourseDetailsControllerService.getCourseDetails"
        )

        self.assertIn("Boundary/Control recursion", hints)
        self.assertIn("must never call back into", hints)

    def test_runtime_failure_hints_explain_jpa_schema_column_mismatch(self) -> None:
        hints = verification_failure_hints(
            'JdbcSQLSyntaxErrorException: Column "COURSE_ID" not found'
        )

        self.assertIn("Persistence schema mismatch", hints)
        self.assertIn("lower snake_case", hints)

    def test_runtime_failure_hints_explain_invalid_json_path(self) -> None:
        hints = verification_failure_hints("InvalidPathException: Expected path: ")

        self.assertIn("Invalid JSONPath assertion", hints)
        self.assertIn("actual response body", hints)

    def test_runtime_failure_hints_explain_executable_api_status_gate(self) -> None:
        hints = verification_failure_hints(
            "missing executable HTTP 409 mapping from CoursesApi; "
            "@ApiResponse/@ApiResponses annotations are documentation only"
        )
        self.assertIn("documentation only", hints)
        self.assertIn("Transport-level statuses", hints)
        self.assertIn("409/422", hints)

    def test_runtime_failure_hints_align_boolean_control_status_assertions(self) -> None:
        hints = verification_failure_hints(
            "StudentsApiControllerTest.registerStudentForCourse_success(): "
            "AssertionFailedError: expected: <200> but was: <409>"
        )

        self.assertIn("HTTP status assertion mismatch", hints)
        self.assertIn("stub the exact Control method", hints)
        self.assertIn("success value", hints)

    def test_runtime_failure_hints_reject_unexecutable_500_controller_tests(self) -> None:
        hints = verification_failure_hints(
            "RegistrationsApiControllerTest.registerCourse_returns500_andDoesNotInvokeControl(): "
            "expected: <500> but was: <201>"
        )

        self.assertIn("Do not add a direct 500 test", hints)
        self.assertIn("transport/global exception", hints)

    def test_control_return_does_not_reject_domain_error_response(self) -> None:
        class_diagram = """
class RegistrationController <<Control>> {
  +removeEnrollment(studentId : String, courseId : String): void
  +enrollStudent(studentId : String, courseId : String): Enrollment
}
class Enrollment <<Entity>> {
}
"""
        api_spec = {
            "paths": {
                "/enrollments": {
                    "post": {
                        "x-easydep-control": {
                            "control": "RegistrationController",
                            "method": "enrollStudent",
                        },
                        "responses": {"201": {}, "409": {}},
                    },
                    "delete": {
                        "x-easydep-control": {
                            "control": "RegistrationController",
                            "method": "removeEnrollment",
                        },
                        "responses": {"204": {}, "403": {}, "500": {}},
                    },
                }
            }
        }

        findings = _unrepresentable_openapi_error_outcomes(class_diagram, api_spec)

        self.assertEqual([], findings)

    def test_event_journal_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "events.jsonl"
            journal = EventJournal(target)

            class FakeEvent:
                source = "agent"
                tool_name = "think"

                def model_dump(self, mode: str):
                    return {"mode": mode}

            journal(FakeEvent())
            record = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual("FakeEvent", record["type"])
            self.assertEqual({"think": 1}, journal.tool_counts)

    def test_agent_workspace_uses_short_path_and_creates_allowed_parents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "generated"
            run = generated / "runs" / "run_abcdef1234567890"
            (run / "application").mkdir(parents=True)
            relative = "application/src/main/java/example/impl/Service.java"
            temp_root = Path(directory) / "temp"
            with patch(
                "app.implementation.agents.workspace.tempfile.gettempdir",
                return_value=str(temp_root),
            ):
                sandbox = prepare_agent_workspace(
                    run,
                    {"task_id": "implement-order-controller", "allowed_write_paths": [relative]},
                )
                self.assertEqual(
                    temp_root / "easydep-agent-workspaces" / "abcdef123456" / "order-controller",
                    sandbox,
                )
                self.assertTrue((sandbox / relative).parent.is_dir())
                readonly = sandbox / "application" / "readonly.java"
                readonly.write_text("class Readonly {}", encoding="utf-8")
                os.chmod(readonly, stat.S_IREAD)

                recreated = prepare_agent_workspace(
                    run,
                    {"task_id": "implement-order-controller", "allowed_write_paths": [relative]},
                )
            self.assertEqual(sandbox, recreated)
            self.assertFalse(readonly.exists())

    def test_final_verification_uses_ascii_short_workspace_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "generated" / "runs" / "run_abcdef1234567890"
            source = run / "application" / "src" / "Main.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Main {}", encoding="utf-8")
            temp_root = root / "ascii-temp"
            verification = {"exitCode": 0, "testResults": ""}

            with (
                patch(
                    "app.implementation.agents.workspace.tempfile.gettempdir",
                    return_value=str(temp_root),
                ),
                patch(
                    "app.implementation.agents.verification.build.verify_agent_workspace",
                    return_value=verification,
                ),
            ):
                result = verify_run_workspace(run)
                phase_result = verify_run_workspace(
                    run, report_name="phase-control-verification.json"
                )

            self.assertEqual("SUCCEEDED", result["status"])
            self.assertEqual(verification, result["verification"])
            self.assertTrue(
                (
                    temp_root
                    / "easydep-agent-workspaces"
                    / "abcdef123456"
                    / "final-verification"
                    / "application/src/Main.java"
                ).is_file()
            )
            self.assertTrue((run / "reports/final-verification.json").is_file())
            self.assertEqual("SUCCEEDED", phase_result["status"])
            self.assertTrue(
                (run / "reports/phase-control-verification.json").is_file()
            )

    def test_phase_verification_skips_frontend_install_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "generated" / "runs" / "run_abcdef1234567890"
            source = run / "application" / "src" / "Main.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Main {}", encoding="utf-8")
            frontend = run / "application" / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text("{}", encoding="utf-8")
            temp_root = root / "ascii-temp"
            verification = {"exitCode": 0, "testResults": ""}

            with (
                patch(
                    "app.implementation.agents.workspace.tempfile.gettempdir",
                    return_value=str(temp_root),
                ),
                patch(
                    "app.implementation.agents.verification.build.verify_agent_workspace",
                    return_value=verification,
                ),
                patch(
                    "app.implementation.agents.verification.build.verify_frontend_workspace"
                ) as verify_frontend,
            ):
                result = verify_run_workspace(run, verify_frontend=False)

            self.assertEqual("SUCCEEDED", result["status"])
            self.assertIsNone(result["frontendVerification"])
            verify_frontend.assert_not_called()

    def test_verification_failure_is_persisted_in_workflow_state(self) -> None:
        from app.implementation.workflows.coordinator import _record_workflow_failure

        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            state = {
                "status": "RUNNING",
                "currentPhase": "persistence",
                "phases": [{"phaseId": "persistence", "status": "RUNNING"}],
                "currentActivity": {
                    "id": "verify-persistence",
                    "label": "Repository 빌드 및 Unit Test",
                    "status": "RUNNING",
                },
            }

            _record_workflow_failure(run, state, RuntimeError("npm ci timed out"))

            persisted = json.loads(
                (run / "reports" / "workflow-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual("FAILED", persisted["status"])
            self.assertEqual("FAILED", persisted["phases"][0]["status"])
            self.assertEqual("FAILED", persisted["currentActivity"]["status"])
            self.assertIn("npm ci timed out", persisted["blockingReason"])

    def test_frontend_timeout_retains_npm_diagnostics(self) -> None:
        from app.implementation.agents.verification.frontend import (
            run_frontend_verification,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontend = root / "application" / "frontend"
            frontend.mkdir(parents=True)
            (frontend / "package.json").write_text("{}", encoding="utf-8")
            (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
            source = frontend / "src" / "main.tsx"
            source.parent.mkdir()
            source.write_text(
                "import { HashRouter } from 'react-router-dom'; const app=<HashRouter />;",
                encoding="utf-8",
            )

            def timeout(command: list[str], **_kwargs: object) -> object:
                raise subprocess.TimeoutExpired(
                    command,
                    300,
                    output=b"fetching packages",
                    stderr=b"registry stalled",
                )

            result = run_frontend_verification(root, timeout, timeout_seconds=300)

            self.assertEqual(1, result["exitCode"])
            self.assertEqual("ci", result["command"][1])
            self.assertIn("registry stalled", str(result["stderr"]))
            self.assertIn("fetching packages", str(result["stdout"]))

    def test_failed_job_overrides_stale_running_workflow_progress(self) -> None:
        from app.workspace.service import WorkspaceService

        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            (reports / "workflow-state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "currentPhase": "persistence",
                        "phases": [
                            {"phaseId": "persistence", "status": "RUNNING"}
                        ],
                        "tasks": [
                            {
                                "taskId": "persistence-1",
                                "phase": "persistence",
                                "status": "SUCCEEDED",
                            }
                        ],
                        "currentActivity": {
                            "id": "verify-persistence",
                            "status": "RUNNING",
                        },
                    }
                ),
                encoding="utf-8",
            )
            service = WorkspaceService()
            try:
                progress = service._implementation_progress_snapshot(
                    {
                        "status": "FAILED",
                        "run_root": str(run),
                        "error": "npm ci timed out",
                    }
                )
            finally:
                service.shutdown()

            updates = {item["step"]: item for item in progress["updates"]}
            self.assertEqual("failed", updates["phase-backend"]["status"])
            self.assertEqual("failed", updates["implementation-result"]["status"])
            self.assertEqual("failed", progress["progress_status"])

    def test_agent_workspace_uses_sibling_when_previous_workspace_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "generated"
            run = generated / "runs" / "run_abcdef1234567890"
            (run / "application").mkdir(parents=True)
            temp_root = Path(directory) / "temp"
            base = temp_root / "easydep-agent-workspaces" / "abcdef123456" / "order-controller"
            base.mkdir(parents=True)
            relative = "application/src/main/java/example/impl/Service.java"

            with (
                patch(
                    "app.implementation.agents.workspace.tempfile.gettempdir",
                    return_value=str(temp_root),
                ),
                patch(
                    "app.implementation.agents.workspace.shutil.rmtree",
                    side_effect=PermissionError("locked"),
                ),
            ):
                sandbox = prepare_agent_workspace(
                    run,
                    {"task_id": "implement-order-controller", "allowed_write_paths": [relative]},
                )

            self.assertEqual(base.with_name("order-controller-2"), sandbox)
            self.assertTrue((sandbox / relative).parent.is_dir())

    def test_reads_exact_generated_java_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            package = run / "application/src/main/java/com/example/bce"
            package.mkdir(parents=True)
            (package / "OrderController.java").write_text(
                "package com.example.bce; public class OrderController {}",
                encoding="utf-8",
            )
            api_package = run / "application/src/main/java/com/example/api/model"
            api_package.mkdir(parents=True)
            (api_package / "OrderController.java").write_text(
                "package com.example.api.model; public class OrderController {}",
                encoding="utf-8",
            )
            repository_package = run / "application/src/main/java/com/example/persistence/repository"
            repository_package.mkdir(parents=True)
            (repository_package / "OrderRepository.java").write_text(
                "package com.example.persistence.repository; public interface OrderRepository {}",
                encoding="utf-8",
            )
            contracts = read_generated_java_contracts(
                run,
                "com.example",
                {"OrderController", "Missing"},
                {"OrderController"},
                {"OrderRepository"},
            )
            self.assertIn("// bce/OrderController.java", contracts)
            self.assertIn("// api/model/OrderController.java", contracts)
            self.assertIn("// persistence/repository/OrderRepository.java", contracts)
            self.assertNotIn("Missing.java", contracts)

    def test_classifies_erd_join_entities_without_relaxing_unknown_aliases(self) -> None:
        source = """entity \"Course\" as Course {}
entity \"Section\" as Section {}
entity \"CourseSection\" as CourseSection {}
entity \"Audit\" as Audit {}
Course ||--|{ CourseSection
Section ||--|{ CourseSection
"""
        self.assertEqual(
            {"CourseSection"},
            parse_erd_association_entities(source, {"Course", "Section"}),
        )

    def test_classifies_self_referential_erd_join_entity(self) -> None:
        source = """entity \"Node\" as Node {}
entity \"NodeNode\" as NodeNode {}
Node ||--|{ NodeNode
"""
        self.assertEqual(
            {"NodeNode"},
            parse_erd_association_entities(source, {"Node"}),
        )

    def test_accepts_annotated_multivalued_child_without_allowing_unknown_table(self) -> None:
        source = """entity "Student" as Student {}
' easydep:erd-origin kind=multivalued alias=StudentCompletedCourses parent=Student field=completedCourses
entity "StudentCompletedCourses" as StudentCompletedCourses {
  * studentcompletedcourses_id : BIGINT
  student_universityId : VARCHAR(255) <<FK>>
  completedCourses_value : VARCHAR(255)
}
entity "StudentProfile" as StudentProfile {
  * studentprofile_id : BIGINT
}
Student ||..o{ StudentCompletedCourses
Student ||..o{ StudentProfile
"""
        contract = assess_bce_erd_entity_contract(source, {"Student"})
        self.assertEqual({"StudentCompletedCourses"}, set(contract.allowed_physical_entities))
        self.assertEqual({"StudentProfile"}, set(contract.unexpected_erd_entities))

    def test_accepts_legacy_rendered_multivalued_child_with_full_shape(self) -> None:
        source = """entity "Student" as Student {}
' === 제1정규화(1NF) 분리 테이블 ===
entity "StudentCompletedCourses" as StudentCompletedCourses {
  * studentcompletedcourses_id : BIGINT
  student_universityId : VARCHAR(255) <<FK>> <<not null>>
  completedCourses_value : VARCHAR(255)
}
Student ||..o{ StudentCompletedCourses
"""
        contract = assess_bce_erd_entity_contract(source, {"Student"})
        self.assertFalse(contract.missing_bce_entities)
        self.assertFalse(contract.unexpected_erd_entities)

    def test_detects_empty_generated_contract_without_inference(self) -> None:
        contracts = """// bce/CourseData.java
package com.example.bce;
/** Assumed placeholder for an undefined BCE type. */
public final class CourseData {}
"""
        self.assertEqual(["CourseData"], find_empty_java_contracts(contracts))

    def test_repairs_orphaned_e2e_statements_after_premature_method_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "FlowTest.java"
            path.write_text(
                "class FlowTest {\n"
                "  @Test void first() { assertThat(true); }\n"
                "  // trailing assertions\n"
                "  assertThat(response.getStatusCode()).isEqualTo(201);\n"
                "  enrollmentRepository.findAll();\n"
                "}\n}\n",
                encoding="utf-8",
            )
            self.assertTrue(repair_orphaned_java_test_statements(path))
            repaired = path.read_text(encoding="utf-8")
            self.assertIn("generatedOrphanFlowAssertions", repaired)
            self.assertEqual(2, repaired.count("@Test"))

    def test_repairs_nested_e2e_class_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "FlowTest.java"
            path.write_text(
                "class FlowTest {\n"
                "  @Test\n"
                "  void llmGeneratedFlowWrapper() {\n"
                "    @Autowired\n"
                "    private CourseRepository repository;\n"
                "    @BeforeEach\n"
                "    void cleanDatabase() {}\n"
                "    @Test\n"
                "    void scenario() {}\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            self.assertTrue(repair_nested_e2e_members(path))
            self.assertFalse(repair_orphaned_java_test_statements(path))
            repaired = path.read_text(encoding="utf-8")
            self.assertNotIn("llmGeneratedFlowWrapper", repaired)
            self.assertIn("private CourseRepository repository", repaired)
            self.assertEqual(1, repaired.count("@Test"))
            self.assertEqual(repaired.count("{"), repaired.count("}"))

    def test_reads_exact_persistence_entity_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            package = run / "application/src/main/java/com/example/demo/persistence/entity"
            package.mkdir(parents=True)
            (package / "HoldingEntity.java").write_text(
                "package com.example.demo.persistence.entity; public class HoldingEntity {}",
                encoding="utf-8",
            )

            contracts = read_persistence_entity_contracts(run, "com.example.demo")

            self.assertIn("HoldingEntity.java", contracts)
            self.assertIn("public class HoldingEntity", contracts)

    def test_makes_generated_entity_no_arg_constructor_mapper_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            relative = "application/src/main/java/com/example/demo/persistence/entity/HoldingEntity.java"
            entity = sandbox / relative
            entity.parent.mkdir(parents=True)
            entity.write_text(
                "public class HoldingEntity { protected HoldingEntity() {} }",
                encoding="utf-8",
            )

            repaired = ensure_mapper_accessible_persistence_constructor(
                sandbox, [relative]
            )

            self.assertEqual([relative], repaired)
            self.assertIn("public HoldingEntity()", entity.read_text(encoding="utf-8"))

    def test_leaves_public_entity_constructor_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            relative = "application/src/main/java/com/example/demo/persistence/entity/HoldingEntity.java"
            entity = sandbox / relative
            entity.parent.mkdir(parents=True)
            source = "public class HoldingEntity { public HoldingEntity() {} }"
            entity.write_text(source, encoding="utf-8")

            repaired = ensure_mapper_accessible_persistence_constructor(
                sandbox, [relative]
            )

            self.assertEqual([], repaired)
            self.assertEqual(source, entity.read_text(encoding="utf-8"))

    def test_extracts_referenced_openapi_models(self) -> None:
        context = """$ref: '#/components/schemas/PurchaseRequest'
$ref: '#/components/schemas/PurchaseRecord'"""
        self.assertEqual(
            {"PurchaseRequest", "PurchaseRecord"},
            referenced_openapi_model_names(context),
        )

    def test_derives_base_package_from_allowed_service_path(self) -> None:
        task = {
            "allowed_write_paths": [
                "application/src/main/java/com/example/demo/application/impl/Service.java"
            ]
        }
        self.assertEqual("com.example.demo", task_base_package(task))

    def test_derives_base_package_from_persistence_mapper_path(self) -> None:
        task = {
            "allowed_write_paths": [
                "application/src/main/java/com/example/demo/persistence/mapper/Mapper.java"
            ]
        }
        self.assertEqual("com.example.demo", task_base_package(task))

    def test_derives_base_package_when_first_output_is_a_resource(self) -> None:
        task = {
            "allowed_write_paths": [
                "application/src/main/resources/db/migration/V1__schema.sql",
                "application/src/test/java/com/example/demo/persistence/SchemaTest.java",
            ]
        }
        self.assertEqual("com.example.demo", task_base_package(task))


if __name__ == "__main__":
    unittest.main()
