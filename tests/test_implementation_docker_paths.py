import json
from pathlib import Path

from app.implementation.domain.models import CommandEvidence, JobSpec
from app.implementation.generation.orchestrator import (
    GRADLE_GENERATOR_IMAGE,
    PrototypeOrchestrator,
)


def _orchestrator(tmp_path: Path) -> PrototypeOrchestrator:
    design = tmp_path / "design"
    design.mkdir()
    bce = design / "class-diagram.puml"
    bce.write_text("class Order <<Entity>>\n", encoding="utf-8")
    bce_model = design / "class-model.json"
    bce_model.write_text(json.dumps({
        "Classes": [{
            "className": "Order",
            "stereotype": "Entity",
            "use_case_ids": ["UC1"],
            "identifier": ["orderId"],
            "fields": ["orderId : UUID"],
            "operations": [],
        }],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }), encoding="utf-8")
    sequence_model = design / "sequence-model.json"
    sequence_model.write_text(json.dumps({"Diagrams": []}), encoding="utf-8")
    api_model = design / "api-model.json"
    api_model.write_text(json.dumps({"Endpoints": []}), encoding="utf-8")
    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="orders",
        workspace_root=tmp_path,
        inputs={
            "bceClass": bce,
            "bceModel": bce_model,
            "sequenceModel": sequence_model,
            "apiModel": api_model,
        },
        required_inputs=[],
        base_package="com.example.orders",
        allow_assumptions=True,
        verify_compile=True,
        output_root=tmp_path / ".easydep" / "implementation-runs" / "orders" / "generated" / "runs",
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


def test_bce_generator_writes_typed_java_without_external_command(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    commands = _recorded_commands(orchestrator)
    java_root = tmp_path / ".easydep" / "implementation-runs" / "orders" / "application" / "src" / "main" / "java"

    orchestrator._generate_bce(java_root)

    generated = java_root / "com/example/orders/bce/Order.java"
    assert commands == []
    assert generated.is_file()
    assert "public class Order" in generated.read_text(encoding="utf-8")
    assert orchestrator.manifest.tools["typed-java-scaffolder"]["input"] == "BCEModel"


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


def test_input_hash_tracks_typed_bce_changes(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    orchestrator._validate_inputs()
    first_hash = orchestrator._combined_input_hash()
    source = orchestrator.spec.inputs["bceModel"]
    model = json.loads(source.read_text(encoding="utf-8"))
    model["Classes"][0]["fields"].append("status : String")
    source.write_text(json.dumps(model), encoding="utf-8")
    orchestrator._validate_inputs()

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
    delays = {"openapi-generator": 0.15}

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
    assert "typed-java-scaffolder" in orchestrator.manifest.tools
    assert "easydep-frontend-generator" in names
