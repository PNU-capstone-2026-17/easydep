"""Export the persisted evidence used by the feedback report addendum.

The raw JSON files are lossless JSON serializations of Workspace command rows or
local API responses.  Diagram sources are produced only by EasyDep's deterministic
renderers from those saved artifacts; they are never hand-authored here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.common.plantuml import _find_plantuml_jar
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.workspace.repository import get_command

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "final-report-feedback-evidence"
API_BASE = "http://127.0.0.1:8100"
PRIMARY_APP_ID = "2b09c0e6-a14a-4f97-9066-77947e2b5ae9"
TESTING_APP_ID = "6f9fd91d-0ad5-459c-b451-165aafe496fb"

COMMANDS = {
    "requirements-capacity-question": "7e0a97c5-399e-468e-9732-23e82c3b41ef",
    "usecase-review-before-uc10-feedback": "07a919a3-ab32-40d6-a59a-168987123fa6",
    "uc10-feedback-and-result": "24d71022-2599-4760-b0d7-936fba953a4b",
    "uc3-feedback-and-clarification": "b794ef0f-212f-4696-ab51-157539f1f19d",
    "uc3-authority-answer-and-confirmation": "87f0d170-cc2a-4ba0-9bdd-c04ec9678f02",
    "uc3-apply-result": "174612d5-216e-470d-89d3-f6ab2c7d44ce",
    "implementation-search-question": "026e1e8f-bff6-4616-b5c3-a04ac6702033",
    "implementation-search-answer": "8da14a2d-6380-4858-a159-d734eadbbad6",
    "uc1-apply-result": "3c8d5ccc-ebd3-4c2e-80af-feeae42ae159",
    "testing-initial-result": "55fb1796-964d-4cdc-b55a-56ed3bdb3310",
    "testing-first-repair-result": "d9924032-2e98-4d86-aa02-18388644ecea",
    "testing-final-repair-result": "9fe0cc3e-64d6-4a61-998e-0493505d8f94",
}

ARTIFACTS = {
    "uc10-before-usecase-spec-v1": ("usecase_spec", 1),
    "uc10-after-usecase-spec-v2": ("usecase_spec", 2),
    "uc1-before-usecase-spec-v3": ("usecase_spec", 3),
    "uc1-after-usecase-spec-v4": ("usecase_spec", 4),
    "usecase-diagram-after-uc10-v1": ("usecase_diagram", 1),
    "uc3-before-class-diagram-v1": ("class_diagram", 1),
    "uc3-after-class-diagram-v2": ("class_diagram", 2),
    "uc3-before-sequence-diagram-v1": ("sequence_diagram", 1),
    "uc3-after-sequence-diagram-v2": ("sequence_diagram", 2),
    "deployment-before-sizing-v1": ("deployment_diagram", 1),
    "deployment-after-sizing-v2": ("deployment_diagram", 2),
}


def api_json(path: str) -> Any:
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=30) as response:
        return json.load(response)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_json(path: Path, value: Any) -> None:
    write_bytes(path, json_bytes(value))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def exact_fields(source: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: source.get(name) for name in names}


def find_use_case(content: dict[str, Any], use_case_id: str) -> dict[str, Any]:
    return next(item for item in content["use_cases"] if item["id"] == use_case_id)


def find_use_case_spec(content: dict[str, Any], use_case_id: str) -> dict[str, Any]:
    return next(
        item for item in content["use_case_specs"] if item["use_case_id"] == use_case_id
    )


def find_class_operation(content: dict[str, Any], stable_id: str) -> dict[str, Any]:
    return next(
        operation
        for class_item in content["Classes"]
        for operation in class_item["operations"]
        if operation.get("stableId") == stable_id
    )


def find_sequence(content: dict[str, Any], use_case_id: str) -> dict[str, Any]:
    return next(
        diagram for diagram in content["Diagrams"] if diagram["use_case_id"] == use_case_id
    )


def find_call(diagram: dict[str, Any], call_id: str) -> dict[str, Any]:
    return next(message for message in diagram["Messages"] if message.get("call_id") == call_id)


def render_sources_with_local_plantuml(source_dir: Path, render_dir: Path) -> None:
    """Render saved sources with the local JAR and preserve duplicate internal names."""

    jar = _find_plantuml_jar()
    java = shutil.which("java")
    if jar is None or java is None:
        raise RuntimeError("EasyDep's local PlantUML JAR and Java are required for rendering.")
    source_paths = sorted(source_dir.glob("*.puml"))
    if not source_paths:
        raise RuntimeError("No PlantUML sources were generated.")
    render_dir.mkdir(parents=True, exist_ok=True)

    def run_plantuml(extension: str, destination: Path, inputs: list[Path]) -> None:
        command = [
            java,
            "-Xms32m",
            "-Xmx256m",
            "-Xss256k",
            "-Xshare:off",
            "-XX:+UseSerialGC",
            "-Dfile.encoding=UTF-8",
            "-DPLANTUML_LIMIT_SIZE=16384",
            "-jar",
            str(jar),
            "-charset",
            "UTF-8",
            f"-t{extension}",
            "-o",
            str(destination),
            *[str(path) for path in inputs],
        ]
        result = subprocess.run(command, capture_output=True, check=False, timeout=180)
        if result.returncode != 0:
            detail = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="replace")
            raise RuntimeError(f"PlantUML {extension} rendering failed:\n{detail.strip()}")

    expected = {
        (source, extension): render_dir / f"{source.stem}.{extension}"
        for source in source_paths
        for extension in ("svg", "png")
    }
    if not any(path.is_file() for path in expected.values()):
        for extension in ("svg", "png"):
            run_plantuml(extension, render_dir, source_paths)

    # PlantUML may use an internal @startuml name as the output filename. Two
    # saved UC3 versions therefore both become UC3.svg/UC3.png in a batch run.
    # Render any missing expected file alone and copy the single output bytes to
    # the evidence filename without changing the PlantUML source.
    for (source, extension), target in expected.items():
        if target.is_file():
            continue
        with tempfile.TemporaryDirectory(prefix="easydep-feedback-render-") as temp_name:
            temp_dir = Path(temp_name)
            run_plantuml(extension, temp_dir, [source])
            candidates = list(temp_dir.glob(f"*.{extension}"))
            if len(candidates) != 1:
                raise RuntimeError(
                    f"Expected one {extension} for {source.name}, found {len(candidates)}."
                )
            shutil.copyfile(candidates[0], target)
    missing = [
        target for target in expected.values() if not target.is_file()
    ]
    if missing:
        raise RuntimeError(f"PlantUML did not create expected files: {missing}")


def validate_originals(
    commands: dict[str, dict[str, Any]], artifacts: dict[str, dict[str, Any]]
) -> None:
    for name, command in commands.items():
        expected_app = TESTING_APP_ID if name.startswith("testing-") else PRIMARY_APP_ID
        assert command["app_id"] == expected_app, name

    capacity = commands["requirements-capacity-question"]["result"]
    assert [item["label"] for item in capacity["actions"]] == [
        "Send answer",
        "Skip suggestion and continue",
    ]

    uc10_before = artifacts["uc10-before-usecase-spec-v1"]["content"]
    uc10_after = artifacts["uc10-after-usecase-spec-v2"]["content"]
    assert len(uc10_before["use_cases"]) == 12
    assert len(uc10_after["use_cases"]) == 13
    assert find_use_case(uc10_before, "UC10")["name"] == (
        "Review assigned course offerings and rosters"
    )
    assert find_use_case(uc10_after, "UC10")["name"] == "Review assigned course offerings"
    assert find_use_case(uc10_after, "UC13")["name"] == "View student roster"

    stable_id = "op_5e29d38d425377d9ad70eb89"
    class_before = artifacts["uc3-before-class-diagram-v1"]["content"]
    class_after = artifacts["uc3-after-class-diagram-v2"]["content"]
    assert find_class_operation(class_before, stable_id)["name"] == "performSwap"
    assert find_class_operation(class_after, stable_id)["name"] == (
        "swapRegistrationAtomically"
    )
    seq_before = find_sequence(
        artifacts["uc3-before-sequence-diagram-v1"]["content"], "UC3"
    )
    seq_after = find_sequence(
        artifacts["uc3-after-sequence-diagram-v2"]["content"], "UC3"
    )
    assert find_call(seq_before, "UC3::call:4")["label"].startswith("performSwap(")
    assert find_call(seq_after, "UC3::call:4")["label"].startswith(
        "swapRegistrationAtomically("
    )

    uc1_before = artifacts["uc1-before-usecase-spec-v3"]["content"]
    uc1_after = artifacts["uc1-after-usecase-spec-v4"]["content"]
    assert find_use_case_spec(uc1_before, "UC1")["trigger"] == (
        "University user initiates a search for published course offerings."
    )
    assert find_use_case_spec(uc1_after, "UC1")["trigger"] == (
        "University user initiates a search for published course offerings specifying term "
        "and course code."
    )

    deployment_before = artifacts["deployment-before-sizing-v1"]["content"]
    deployment_after = artifacts["deployment-after-sizing-v2"]["content"]
    assert deployment_before.get("sizing") is None
    assert deployment_after["sizing"]["status"] == "completed"
    assert deployment_after["sizing"]["selected"] == [
        {
            "computeUnitId": "compute-1",
            "sku": "t3a.medium",
            "replicaCount": 1,
            "replicationConfirmed": False,
        }
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--resume-from-raw",
        action="store_true",
        help="Reuse and verify already-exported raw files; retry only projections/rendering.",
    )
    args = parser.parse_args()
    output = args.output.resolve()

    commands: dict[str, dict[str, Any]] = {}
    for name, command_id in COMMANDS.items():
        if args.resume_from_raw:
            command = read_json(output / "raw" / "commands" / f"{name}.json")
            if command.get("command_id") != command_id:
                raise RuntimeError(f"Checkpoint command ID mismatch: {name}")
        else:
            command = get_command(command_id)
            if command is None:
                raise RuntimeError(f"Missing persisted command: {command_id}")
        commands[name] = command

    artifacts: dict[str, dict[str, Any]] = {}
    for name, (stage, version) in ARTIFACTS.items():
        if args.resume_from_raw:
            response = read_json(output / "raw" / "artifacts" / f"{name}.json")
            if response.get("version_no") != version:
                raise RuntimeError(f"Checkpoint artifact version mismatch: {name}")
            artifacts[name] = response
        else:
            path = f"/api/apps/{PRIMARY_APP_ID}/stages/{stage}/versions/{version}"
            artifacts[name] = api_json(path)

    validate_originals(commands, artifacts)

    generated: dict[str, dict[str, Any]] = {}

    def record(path: Path, data: bytes, *, kind: str, source: str) -> None:
        write_bytes(path, data)
        relative = path.relative_to(output).as_posix()
        generated[relative] = {
            "kind": kind,
            "source": source,
            "bytes": len(data),
            "sha256": sha256(data),
        }

    def record_existing(path: Path, *, kind: str, source: str) -> None:
        if not path.is_file():
            raise RuntimeError(f"Missing checkpoint file: {path}")
        data = path.read_bytes()
        relative = path.relative_to(output).as_posix()
        generated[relative] = {
            "kind": kind,
            "source": source,
            "bytes": len(data),
            "sha256": sha256(data),
        }

    for name, command in commands.items():
        path = output / "raw" / "commands" / f"{name}.json"
        source = f"workspace_commands.command_id={command['command_id']}"
        if args.resume_from_raw:
            if path.read_bytes() != json_bytes(command):
                raise RuntimeError(f"Checkpoint command serialization mismatch: {name}")
            record_existing(path, kind="raw-command-snapshot", source=source)
        else:
            record(path, json_bytes(command), kind="raw-command-snapshot", source=source)

    choice_subsets = {
        "requirements-capacity-question-and-actions": exact_fields(
            commands["requirements-capacity-question"]["result"],
            "message",
            "resource_question",
            "resource_questions",
            "actions",
        ),
        "usecase-review-actions-before-uc10-feedback": exact_fields(
            commands["usecase-review-before-uc10-feedback"]["result"],
            "message",
            "actions",
        ),
        "uc3-feedback-clarification-and-actions": {
            "submitted_payload": exact_fields(
                commands["uc3-feedback-and-clarification"]["payload"], "text", "context"
            ),
            "returned_result": exact_fields(
                commands["uc3-feedback-and-clarification"]["result"],
                "message",
                "conversation",
                "actions",
            ),
        },
        "uc3-authority-answer-and-confirmation-actions": {
            "submitted_payload": exact_fields(
                commands["uc3-authority-answer-and-confirmation"]["payload"],
                "text",
                "context",
            ),
            "returned_result": exact_fields(
                commands["uc3-authority-answer-and-confirmation"]["result"],
                "message",
                "authority_targets",
                "downstream_targets",
                "actions",
            ),
        },
        "implementation-search-question-and-actions": exact_fields(
            commands["implementation-search-question"]["result"],
            "message",
            "feedback_question",
            "actions",
        ),
        "implementation-search-answer-and-confirmation-actions": {
            "submitted_payload": exact_fields(
                commands["implementation-search-answer"]["payload"],
                "text",
                "feedback_option_id",
                "action_id",
            ),
            "returned_result": exact_fields(
                commands["implementation-search-answer"]["result"],
                "message",
                "authority_targets",
                "downstream_targets",
                "actions",
            ),
        },
        "testing-initial-actions": exact_fields(
            commands["testing-initial-result"]["result"], "message", "actions"
        ),
        "testing-final-repair-actions": exact_fields(
            commands["testing-final-repair-result"]["result"], "message", "actions"
        ),
    }
    for name, subset in choice_subsets.items():
        source_name = {
            "requirements-capacity-question-and-actions": "requirements-capacity-question",
            "usecase-review-actions-before-uc10-feedback": (
                "usecase-review-before-uc10-feedback"
            ),
            "uc3-feedback-clarification-and-actions": "uc3-feedback-and-clarification",
            "uc3-authority-answer-and-confirmation-actions": (
                "uc3-authority-answer-and-confirmation"
            ),
            "implementation-search-question-and-actions": (
                "implementation-search-question"
            ),
            "implementation-search-answer-and-confirmation-actions": (
                "implementation-search-answer"
            ),
            "testing-initial-actions": "testing-initial-result",
            "testing-final-repair-actions": "testing-final-repair-result",
        }[name]
        path = output / "raw" / "choices" / f"{name}.json"
        source = f"raw/commands/{source_name}.json"
        if args.resume_from_raw:
            if path.read_bytes() != json_bytes(subset):
                raise RuntimeError(f"Checkpoint choice subset mismatch: {name}")
            record_existing(path, kind="exact-subset-of-raw-command", source=source)
        else:
            record(
                path,
                json_bytes(subset),
                kind="exact-subset-of-raw-command",
                source=source,
            )

    for name, response in artifacts.items():
        stage, version = ARTIFACTS[name]
        path = output / "raw" / "artifacts" / f"{name}.json"
        source = f"GET /api/apps/{PRIMARY_APP_ID}/stages/{stage}/versions/{version}"
        if args.resume_from_raw:
            if path.read_bytes() != json_bytes(response):
                raise RuntimeError(f"Checkpoint artifact serialization mismatch: {name}")
            record_existing(path, kind="raw-api-response", source=source)
        else:
            record(path, json_bytes(response), kind="raw-api-response", source=source)

    for stage in sorted({stage for stage, _version in ARTIFACTS.values()} | {"api_spec"}):
        api_path = f"/api/apps/{PRIMARY_APP_ID}/stages/{stage}/versions"
        path = output / "raw" / "version-indexes" / f"{stage}.json"
        if args.resume_from_raw:
            record_existing(path, kind="raw-api-response", source=f"GET {api_path}")
        else:
            response = api_json(api_path)
            record(
                path,
                json_bytes(response),
                kind="raw-api-response",
                source=f"GET {api_path}",
            )

    testing_path = f"/api/workspace/apps/{TESTING_APP_ID}/testing-result"
    testing_output = output / "raw" / "testing" / "current-testing-result.json"
    if args.resume_from_raw:
        record_existing(
            testing_output, kind="raw-api-response", source=f"GET {testing_path}"
        )
    else:
        testing_result = api_json(testing_path)
        record(
            testing_output,
            json_bytes(testing_result),
            kind="raw-api-response",
            source=f"GET {testing_path}",
        )

    baseline_source = (
        ROOT
        / "evaluation"
        / "baselines"
        / "course-registration-cases"
        / "e1-course-registration-aws.json"
    )
    baseline_data = baseline_source.read_bytes()
    baseline_output = output / "raw" / "input" / baseline_source.name
    if args.resume_from_raw:
        if baseline_output.read_bytes() != baseline_data:
            raise RuntimeError("Checkpoint input file differs from the baseline source.")
        record_existing(
            baseline_output,
            kind="verbatim-file-copy",
            source=baseline_source.relative_to(ROOT).as_posix(),
        )
    else:
        record(
            baseline_output,
            baseline_data,
            kind="verbatim-file-copy",
            source=baseline_source.relative_to(ROOT).as_posix(),
        )

    diagram_sources: dict[str, tuple[str, str]] = {}
    usecase_puml = artifacts["usecase-diagram-after-uc10-v1"]["content"]
    diagram_sources["usecase-after-uc10-v1"] = (
        usecase_puml,
        "raw/artifacts/usecase-diagram-after-uc10-v1.json#/content",
    )
    for state_name, rendered_name in (
        ("uc3-before-class-diagram-v1", "uc3-class-before-v1"),
        ("uc3-after-class-diagram-v2", "uc3-class-after-v2"),
    ):
        diagram_sources[rendered_name] = (
            generate_plantuml_from_bce_json(artifacts[state_name]["content"]),
            f"raw/artifacts/{state_name}.json#/content",
        )
    for state_name, rendered_name in (
        ("uc3-before-sequence-diagram-v1", "uc3-sequence-before-v1"),
        ("uc3-after-sequence-diagram-v2", "uc3-sequence-after-v2"),
    ):
        uc3 = find_sequence(artifacts[state_name]["content"], "UC3")
        diagram_sources[rendered_name] = (
            generate_sequence_from_model(uc3),
            f"raw/artifacts/{state_name}.json#/content/Diagrams[use_case_id=UC3]",
        )
    for state_name, suffix in (
        ("deployment-before-sizing-v1", "before-v1"),
        ("deployment-after-sizing-v2", "after-v2"),
    ):
        bundle = artifacts[state_name]["content"]
        source = f"raw/artifacts/{state_name}.json#/content"
        diagram_sources[f"deployment-runtime-{suffix}"] = (
            deployment_bundle_runtime_puml(bundle),
            source,
        )
        diagram_sources[f"deployment-provisioning-{suffix}"] = (
            deployment_bundle_provisioning_puml(bundle),
            source,
        )

    for name, (puml, source) in diagram_sources.items():
        if not puml.strip():
            raise RuntimeError(f"Empty deterministic PlantUML output: {name}")
        puml_data = puml.encode("utf-8")
        puml_path = output / "sources" / f"{name}.puml"
        if args.resume_from_raw and puml_path.is_file():
            if puml_path.read_bytes() != puml_data:
                raise RuntimeError(f"Checkpoint PlantUML projection mismatch: {name}")
            record_existing(puml_path, kind="deterministic-view-source", source=source)
        else:
            record(
                puml_path,
                puml_data,
                kind="deterministic-view-source",
                source=source,
            )

    render_sources_with_local_plantuml(output / "sources", output / "renders")
    for name in diagram_sources:
        for extension in ("svg", "png"):
            record_existing(
                output / "renders" / f"{name}.{extension}",
                kind="rendered-view",
                source=f"sources/{name}.puml",
            )

    unavailable = [
        {
            "requested_view": "UC10 feedback before use-case diagram",
            "reason": (
                "No pre-feedback use-case diagram version was persisted. The only stored "
                "usecase_diagram version was created after usecase_spec version 2."
            ),
        },
        {
            "requested_view": "UC1 implementation-feedback before/after use-case diagrams",
            "reason": (
                "The feedback created usecase_spec versions 3 and 4, but it did not create "
                "corresponding usecase_diagram versions."
            ),
        },
        {
            "requested_view": "Pre-input deployment-sizing API response",
            "reason": (
                "The transient GET response was not persisted. Deployment version 1, whose "
                "sizing field is absent, is the saved before artifact."
            ),
        },
        {
            "requested_view": "Testing repair candidate before/after source files",
            "reason": (
                "No repair candidate was accepted, and the final command records a discarded "
                "candidate rather than a persisted replacement artifact. Full command result "
                "snapshots are retained instead."
            ),
        },
    ]

    manifest = {
        "schemaVersion": "easydep-feedback-report-evidence/v1",
        "generatedAt": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "primaryAppId": PRIMARY_APP_ID,
        "testingAppId": TESTING_APP_ID,
        "serialization": (
            "JSON values are unchanged; API and database objects are pretty-printed as UTF-8 "
            "JSON with a trailing newline."
        ),
        "diagramPolicy": (
            "PlantUML sources are stored originals or deterministic EasyDep projections from "
            "saved structured artifacts. SVG and PNG files are renderer outputs, not manually "
            "redrawn diagrams."
        ),
        "files": dict(sorted(generated.items())),
        "unavailableOriginals": unavailable,
    }
    manifest_data = json_bytes(manifest)
    write_bytes(output / "manifest.json", manifest_data)
    print(
        json.dumps(
            {
                "output": str(output),
                "files": len(generated) + 1,
                "manifest_sha256": sha256(manifest_data),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
