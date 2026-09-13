"""Export the report-case UC10 split as immutable before/after evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.workspace.repository import get_command  # noqa: E402

APP_ID = "b1aea221-82a6-4d59-8720-2b24044ce3c2"
COMMAND_IDS = {
    "restored-usecase-review": "a7a6091d-c6e9-4f87-a3c7-b10ab65ff99a",
    "uc10-split-feedback": "07f91681-16e8-40c3-a4a3-9c0ac31808fd",
    "generate-specifications": "c6efcd66-075e-4472-a293-b726faa8e71d",
    "generate-relationships-and-diagram": "d46cbf48-886c-481c-a113-df0d440aa0c7",
}
API_BASE = "http://127.0.0.1:8100"
SOURCE_STATE = (
    ROOT
    / "artifacts"
    / "checkpoint-e2e"
    / "current"
    / "e1-aws"
    / "chain"
    / "snapshots"
    / "use_cases"
    / "state.json"
)
SOURCE_DIAGRAM_DIR = (
    ROOT
    / "artifacts"
    / "checkpoint-e2e"
    / "current"
    / "e1-aws"
    / "chain"
    / "stages"
    / "04-specifications-to-usecase_diagram"
    / "output"
)
DEFAULT_OUTPUT = (
    ROOT / "docs" / "final-report-feedback-evidence" / "report-case-uc10-split"
)


def _api_json(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=60) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object from {path}.")
    return value


def _api_bytes(path: str) -> bytes:
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=60) as response:
        return response.read()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(
    source: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    after_specs: dict[str, Any],
    diagram: str,
) -> dict[str, Any]:
    source_use_cases = source.get("use_cases")
    before_content = before.get("content")
    after_content = after.get("content")
    after_specs_content = after_specs.get("content")
    if not isinstance(source_use_cases, list):
        raise TypeError("The source use_cases value is not a list.")
    for name, content in {
        "before": before_content,
        "after": after_content,
        "after_specs": after_specs_content,
    }.items():
        if not isinstance(content, dict) or not isinstance(content.get("use_cases"), list):
            raise TypeError(f"The {name} API response has no use_cases list.")
    assert isinstance(before_content, dict)
    assert isinstance(after_content, dict)
    assert isinstance(after_specs_content, dict)

    if before_content["use_cases"] != source_use_cases:
        raise ValueError("The persisted before use cases differ from the report checkpoint.")
    if len(before_content["use_cases"]) != 12 or len(after_content["use_cases"]) != 13:
        raise ValueError("The before/after use-case counts are not 12 and 13.")

    before_by_id = {item["id"]: item for item in before_content["use_cases"]}
    after_by_id = {item["id"]: item for item in after_content["use_cases"]}
    unchanged_ids = sorted(set(before_by_id) - {"UC10"})
    changed_siblings = [
        use_case_id
        for use_case_id in unchanged_ids
        if before_by_id[use_case_id] != after_by_id.get(use_case_id)
    ]
    if changed_siblings:
        raise ValueError(
            "The targeted revision changed non-target use cases: "
            + ", ".join(changed_siblings)
        )
    if after_specs_content["use_cases"] != after_content["use_cases"]:
        raise ValueError("The specification stage did not preserve the revised use cases.")
    if len(after_specs_content.get("use_case_specs") or []) != 13:
        raise ValueError("The after artifact does not contain 13 use-case specifications.")

    uc10 = after_by_id.get("UC10")
    uc13 = after_by_id.get("UC13")
    if not isinstance(uc10, dict) or not isinstance(uc13, dict):
        raise TypeError("The after artifact does not contain both UC10 and UC13.")
    if uc10.get("requirement_ids") != ["RR11", "RR14"] or uc13.get(
        "requirement_ids"
    ) != ["RR11", "RR14"]:
        raise ValueError("UC10 and UC13 did not retain RR11 and RR14.")
    if "Review Assigned Course Offerings" not in diagram or "View Student Roster" not in diagram:
        raise ValueError("The after diagram does not show both split use cases.")

    return {
        "source_before_match": True,
        "before_use_case_count": 12,
        "after_use_case_count": 13,
        "non_target_use_cases_unchanged": True,
        "unchanged_use_case_ids": unchanged_ids,
        "after_specification_count": 13,
        "uc10_requirement_ids": uc10["requirement_ids"],
        "uc13_requirement_ids": uc13["requirement_ids"],
    }


def export(output: Path) -> dict[str, Any]:
    raw = output / "raw"
    diagrams = output / "diagrams"
    raw.mkdir(parents=True, exist_ok=True)
    diagrams.mkdir(parents=True, exist_ok=True)

    source = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    before = _api_json(f"/api/apps/{APP_ID}/stages/usecase_spec/versions/1")
    after = _api_json(f"/api/apps/{APP_ID}/stages/usecase_spec/versions/2")
    after_specs = _api_json(f"/api/apps/{APP_ID}/stages/usecase_spec/versions/3")
    diagram_response = _api_json(
        f"/api/apps/{APP_ID}/stages/usecase_diagram/versions/1"
    )
    diagram = diagram_response.get("content")
    if not isinstance(diagram, str):
        raise TypeError("The after use-case diagram API response is not PlantUML text.")

    validation = _validate(source, before, after, after_specs, diagram)

    shutil.copyfile(SOURCE_STATE, raw / "report-checkpoint-use-cases-state.json")
    _write_json(raw / "before-usecase-spec-v1.json", before)
    _write_json(raw / "after-usecase-spec-v2.json", after)
    _write_json(raw / "after-usecase-specifications-v3.json", after_specs)
    _write_json(raw / "after-usecase-diagram-v1.json", diagram_response)
    _write_json(
        raw / "after-uc10-trace.json",
        _api_json(f"/api/apps/{APP_ID}/trace?ref=use_case%3AUC10"),
    )
    _write_json(
        raw / "after-uc13-trace.json",
        _api_json(f"/api/apps/{APP_ID}/trace?ref=use_case%3AUC13"),
    )
    for label, command_id in COMMAND_IDS.items():
        command = get_command(command_id)
        if command is None or command.get("app_id") != APP_ID:
            raise ValueError(f"Missing or mismatched Workspace command: {command_id}")
        _write_json(raw / f"command-{label}.json", command)

    for extension in ("puml", "svg", "png"):
        source = SOURCE_DIAGRAM_DIR / f"usecase.{extension}"
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copyfile(source, diagrams / f"before-usecase.{extension}")
    (diagrams / "after-usecase.puml").write_text(diagram, encoding="utf-8")
    (diagrams / "after-usecase.svg").write_bytes(
        _api_bytes(f"/api/apps/{APP_ID}/stages/usecase_diagram/image.svg")
    )
    (diagrams / "after-usecase.png").write_bytes(
        _api_bytes(f"/api/apps/{APP_ID}/stages/usecase_diagram/image.png")
    )

    evidence_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "manifest.json"
    )
    manifest = {
        "schemaVersion": "easydep-report-case-feedback-evidence/v1",
        "app_id": APP_ID,
        "source_checkpoint": SOURCE_STATE.relative_to(ROOT).as_posix(),
        "source_checkpoint_sha256": _sha256(SOURCE_STATE),
        "feedback_command_id": COMMAND_IDS["uc10-split-feedback"],
        "validation": validation,
        "files": [
            {
                "path": path.relative_to(output).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in evidence_files
        ],
    }
    _write_json(output / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = export(args.output.resolve())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
