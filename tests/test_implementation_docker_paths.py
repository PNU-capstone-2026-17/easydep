import json
from pathlib import Path

from app.implementation.domain.models import CommandEvidence, JobSpec
from app.implementation.generation.orchestrator import (
    GRADLE_GENERATOR_IMAGE,
    PUML2CODE_IMAGE,
    PrototypeOrchestrator,
)


def _orchestrator(tmp_path: Path) -> PrototypeOrchestrator:
    puml2code = tmp_path / "app" / "implementation" / "tools" / "puml2code-bce"
    (puml2code / "bin").mkdir(parents=True)
    (puml2code / "bin" / "puml2code").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (puml2code / "Dockerfile").write_text("FROM node:20\n", encoding="utf-8")
    bce = tmp_path / "design" / "class-diagram.puml"
    bce.parent.mkdir()
    bce.write_text("class Order <<Entity>>\n", encoding="utf-8")
    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="orders",
        workspace_root=tmp_path,
        inputs={"bceClass": bce},
        required_inputs=[],
        base_package="com.example.orders",
        allow_assumptions=True,
        verify_compile=True,
        output_root=tmp_path / ".easydep" / "implementation-runs" / "orders" / "generated" / "runs",
        puml2code_root=puml2code,
        agent_mode="plan-only",
        agent_model="model",
        agent_base_url="http://localhost",
        agent_temperature=0.0,
        agent_top_p=1.0,
        agent_max_output_tokens=1000,
        agent_reasoning_budget=0,
    )
    return PrototypeOrchestrator(spec)


def _recorded_commands(orchestrator: PrototypeOrchestrator) -> list[list[str]]:
    commands: list[list[str]] = []

    def record(
        name: str,
        command: list[str],
        cwd: Path,
        timeout_seconds: int = 300,
    ) -> CommandEvidence:
        commands.append(command)
        return CommandEvidence(name, command, str(cwd), 0, 0, "", "")

    orchestrator._run_command = record  # type: ignore[method-assign]
    return commands


def test_bce_generator_uses_posix_container_paths_on_windows_and_linux(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    commands = _recorded_commands(orchestrator)
    java_root = tmp_path / ".easydep" / "implementation-runs" / "orders" / "application" / "src" / "main" / "java"

    orchestrator._generate_bce(java_root)

    build, command = commands
    assert build[:4] == ["docker", "build", "--tag", PUML2CODE_IMAGE]
    assert command[command.index("-v") + 1] == f"{tmp_path.resolve()}:/workspace"
    assert command[command.index("-w") + 1] == "/workspace"
    assert PUML2CODE_IMAGE in command
    assert command[command.index("-i") + 1] == "/workspace/design/class-diagram.puml"
    assert command[command.index("-o") + 1] == "/workspace/.easydep/implementation-runs/orders/application/src/main/java"


def test_gradle_compile_uses_posix_workdir_and_workspace_volume(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    commands = _recorded_commands(orchestrator)
    application = tmp_path / ".easydep" / "implementation-runs" / "orders" / "application"
    jar = application / "build" / "libs" / "orders.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"boot jar")
    local_gradle = application / ".gradle"
    local_gradle.mkdir()

    orchestrator._compile(application)

    command = commands[0]
    volume_indices = [index for index, value in enumerate(command) if value == "-v"]
    assert command[volume_indices[0] + 1] == f"{tmp_path.resolve()}:/workspace"
    assert command[command.index("-e") + 1] == "GRADLE_USER_HOME=/tmp/easydep-gradle-home"
    assert command[command.index("-w") + 1] == "/workspace/.easydep/implementation-runs/orders/application"
    # `bootJar` is deliberately absent: this pre-approval gate only proves the
    # generated scaffold compiles, and packaging happens after approval.
    assert command[command.index(GRADLE_GENERATOR_IMAGE) + 1 :] == [
        "gradle",
        "compileJava",
        "--no-daemon",
        "-Dorg.gradle.vfs.watch=false",
        "--build-cache",
    ]
    assert jar.read_bytes() == b"boot jar"
    assert not local_gradle.exists()


def test_container_path_rejects_a_path_outside_the_workspace(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    outside = tmp_path.parent / "outside.txt"

    try:
        orchestrator._container_path(outside)
    except ValueError as error:
        assert "workspaceRoot" in str(error)
    else:
        raise AssertionError("Expected an outside workspace path to be rejected")


def test_input_hash_tracks_bce_generator_source_changes(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    source = orchestrator.spec.puml2code_root / "src" / "parser" / "plantuml.pegjs"
    source.parent.mkdir(parents=True)
    source.write_text("first generator grammar", encoding="utf-8")
    orchestrator._validate_inputs()

    first_hash = orchestrator._combined_input_hash()
    source.write_text("updated generator grammar", encoding="utf-8")

    assert orchestrator._combined_input_hash() != first_hash


def test_input_validation_requires_executable_sequence_and_operation_ids(
    tmp_path: Path,
) -> None:
    orchestrator = _orchestrator(tmp_path)
    sequence = tmp_path / "design/sequence.puml"
    openapi = tmp_path / "design/openapi.json"
    sequence.write_text("@startuml\nparticipant A\n@enduml", encoding="utf-8")
    openapi.write_text(
        json.dumps(
            {
                "paths": {
                    "/orders": {
                        "post": {"responses": {"201": {"description": "ok"}}}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    orchestrator.spec.inputs.update({"sequence": sequence, "openapi": openapi})
    orchestrator.spec.required_inputs = ["bceClass", "sequence", "openapi"]

    orchestrator._validate_inputs()

    codes = {item.code for item in orchestrator.manifest.diagnostics}
    assert "SEQUENCE_HAS_NO_CALLS" in codes
    assert "OPENAPI_MISSING_OPERATION_ID" in codes


def test_input_validation_returns_needs_input_for_openapi_without_operations(
    tmp_path: Path,
) -> None:
    orchestrator = _orchestrator(tmp_path)
    sequence = tmp_path / "design/sequence.puml"
    openapi = tmp_path / "design/openapi.json"
    sequence.write_text("A -> B : createOrder()\n", encoding="utf-8")
    openapi.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "Orders", "version": "1.0.0"},
                "paths": {},
            }
        ),
        encoding="utf-8",
    )
    orchestrator.spec.inputs.update({"sequence": sequence, "openapi": openapi})
    orchestrator.spec.required_inputs = ["bceClass", "sequence", "openapi"]

    orchestrator._validate_inputs()

    codes = {item.code for item in orchestrator.manifest.diagnostics}
    assert "OPENAPI_NO_OPERATIONS" in codes


def test_input_validation_rejects_bce_erd_entity_mismatch(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    bce = orchestrator.spec.inputs["bceClass"]
    erd = tmp_path / "design/erd.puml"
    sequence = tmp_path / "design/sequence.puml"
    openapi = tmp_path / "design/openapi.json"
    bce.write_text("class Order <<Entity>> {}\n", encoding="utf-8")
    erd.write_text('entity "Customer" as Customer {}\n', encoding="utf-8")
    sequence.write_text("A -> B : createOrder()\n", encoding="utf-8")
    openapi.write_text(
        json.dumps(
            {
                "paths": {
                    "/orders": {
                        "post": {
                            "operationId": "createOrder",
                            "responses": {"201": {"description": "ok"}},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    orchestrator.spec.inputs.update(
        {"erd": erd, "sequence": sequence, "openapi": openapi}
    )
    orchestrator.spec.required_inputs = ["bceClass", "sequence", "openapi"]

    orchestrator._validate_inputs()

    codes = {item.code for item in orchestrator.manifest.diagnostics}
    assert "BCE_ERD_ENTITY_MISMATCH" in codes


def test_parallel_generators_record_evidence_in_declared_order(tmp_path: Path) -> None:
    """A run directory is an immutable checkpoint, so evidence order must not
    depend on which generator finishes first."""
    import time

    orchestrator = _orchestrator(tmp_path)
    application = tmp_path / "application"
    java_root = application / "src" / "main" / "java"
    java_root.mkdir(parents=True)

    # Finish in the exact reverse of the declared order.
    delays = {"puml2code-bce": 0.25, "openapi-generator": 0.15}

    def record(
        name: str,
        command: list[str],
        cwd: Path,
        timeout_seconds: int = 300,
    ) -> CommandEvidence:
        time.sleep(delays.get(name, 0.0))
        evidence = CommandEvidence(name, command, str(cwd), 0, 0, "", "")
        orchestrator._sink().commands.append(evidence)
        return evidence

    orchestrator._run_command = record  # type: ignore[method-assign]
    orchestrator._generate_openapi = lambda app: record(  # type: ignore[method-assign]
        "openapi-generator", ["openapi"], app
    )
    orchestrator._generate_frontend = lambda app: record(  # type: ignore[method-assign]
        "easydep-frontend-generator", ["frontend"], app
    )

    orchestrator._generate_sources(application, java_root)

    names = [item.name for item in orchestrator.manifest.commands]
    assert names == [
        "puml2code-bce-image",
        "puml2code-bce",
        "openapi-generator",
        "easydep-frontend-generator",
    ]


def test_parallel_generator_failure_propagates(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    application = tmp_path / "application"
    java_root = application / "src" / "main" / "java"
    java_root.mkdir(parents=True)

    def boom(_application: Path) -> None:
        raise RuntimeError("openapi generation failed")

    def record(
        name: str,
        command: list[str],
        cwd: Path,
        timeout_seconds: int = 300,
    ) -> CommandEvidence:
        evidence = CommandEvidence(name, command, str(cwd), 0, 0, "", "")
        orchestrator._sink().commands.append(evidence)
        return evidence

    orchestrator._run_command = record  # type: ignore[method-assign]
    orchestrator._generate_openapi = boom  # type: ignore[method-assign]
    orchestrator._generate_frontend = lambda app: record(  # type: ignore[method-assign]
        "easydep-frontend-generator", ["frontend"], app
    )

    try:
        orchestrator._generate_sources(application, java_root)
    except RuntimeError as error:
        assert "openapi generation failed" in str(error)
    else:  # pragma: no cover - the failure must not be swallowed
        raise AssertionError("a failing generator must surface to the caller")

    # A failed run still has to report what the other generators did.
    names = [item.name for item in orchestrator.manifest.commands]
    assert "puml2code-bce" in names
    assert "easydep-frontend-generator" in names
