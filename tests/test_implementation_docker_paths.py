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
        agent_temperature=0.0,
        agent_max_output_tokens=1000,
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
