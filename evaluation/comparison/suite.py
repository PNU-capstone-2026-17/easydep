"""여러 도메인 사례를 같은 비교 계약으로 실행한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import load_manifest
from .report import write_reports
from .runner import run_experiment

SUITE_SCHEMA = "easydep-comparison-suite/v1"

ARTIFACT_CONTRACT = [
    {"id": "requirements", "title": "Requirements specification", "description": "Traceable functional and non-functional requirements."},
    {"id": "classDiagram", "title": "Class diagram", "description": "Classes, responsibilities, attributes, operations, and relationships."},
    {"id": "sequenceDiagram", "title": "Sequence diagrams", "description": "Major use-case interactions with participants and messages."},
    {"id": "apiSpecification", "title": "API specification", "description": "Operations, requests, responses, and data schemas."},
    {"id": "dataModel", "title": "Data model", "description": "Persistent entities, keys, fields, and relationships."},
    {"id": "sourceCode", "title": "Source code", "description": "Executable application implementation."},
    {"id": "tests", "title": "Tests", "description": "Automated unit or integration tests."},
    {"id": "container", "title": "Container configuration", "description": "A reproducible container build definition."},
    {"id": "infrastructure", "title": "Infrastructure as code", "description": "Terraform deployment configuration satisfying the cloud constraints."},
]


@dataclass(frozen=True)
class SuiteCase:
    id: str
    input_path: Path


@dataclass(frozen=True)
class ComparisonSuite:
    path: Path
    id: str
    repetitions: int
    output_root: Path
    cases: tuple[SuiteCase, ...]


def load_suite(path: str | Path) -> ComparisonSuite:
    source = Path(path).resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schemaVersion") != SUITE_SCHEMA:
        raise ValueError(f"suite schemaVersion은 {SUITE_SCHEMA!r}이어야 합니다.")
    suite_id = str(data.get("suiteId", "")).strip()
    if not suite_id:
        raise ValueError("suiteId는 비어 있을 수 없습니다.")
    repetitions = data.get("repetitions", 1)
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise ValueError("repetitions는 1 이상의 정수여야 합니다.")
    cases: list[SuiteCase] = []
    for index, item in enumerate(data.get("cases", [])):
        if not isinstance(item, dict):
            raise ValueError(f"cases[{index}]는 객체여야 합니다.")
        case_id = str(item.get("id", "")).strip()
        raw_path = Path(str(item.get("input", "")))
        input_path = raw_path if raw_path.is_absolute() else (source.parent / raw_path).resolve()
        if not case_id or not input_path.is_file():
            raise ValueError(f"cases[{index}]의 id 또는 input 파일이 올바르지 않습니다.")
        cases.append(SuiteCase(case_id, input_path))
    if len(cases) < 2:
        raise ValueError("단일 주제 편향을 줄이려면 suite에 두 개 이상의 사례가 필요합니다.")
    raw_output = Path(str(data.get("outputRoot", "artifacts/comparison")))
    output_root = raw_output if raw_output.is_absolute() else (Path.cwd() / raw_output).resolve()
    return ComparisonSuite(source, suite_id, repetitions, output_root, tuple(cases))


def _case_requirements(case: SuiteCase) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = json.loads(case.input_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"사례 입력은 JSON 객체여야 합니다: {case.input_path}")
    requirements: list[dict[str, Any]] = []
    raw_requirements = data.get("requirements")
    if isinstance(raw_requirements, list):
        for index, text in enumerate(raw_requirements, start=1):
            requirements.append(
                {
                    "id": f"REQ-{index:02d}",
                    "text": str(text),
                    "verificationGates": [],
                    "evidenceStages": ["requirements", "design", "code", "test", "deployment"],
                }
            )
    raw_classified = data.get("classified")
    if isinstance(raw_classified, list):
        for index, item in enumerate(raw_classified, start=1):
            if not isinstance(item, dict):
                continue
            requirements.append(
                {
                    "id": str(item.get("id") or f"REQ-{index:02d}"),
                    "text": str(item.get("text") or ""),
                    "verificationGates": [],
                    "evidenceStages": ["requirements", "design", "code", "test", "deployment"],
                }
            )
    if not requirements:
        raise ValueError(f"지원되는 requirements 또는 classified 배열이 없습니다: {case.input_path}")
    constraint = data.get("cloudConstraints") or data.get("resource_constraints_text")
    constraints = []
    if constraint:
        constraints.append(
            {"id": "CLOUD-01", "text": str(constraint), "verificationGates": []}
        )
    return requirements, constraints


def _manifest_data(suite: ComparisonSuite, case: SuiteCase, repetitions: int) -> dict[str, Any]:
    requirements, constraints = _case_requirements(case)
    return {
        "schemaVersion": "easydep-comparison-manifest/v1",
        "experimentId": f"{suite.id}-{case.id}",
        "repetitions": repetitions,
        "outputRoot": str(suite.output_root),
        "promptProtocol": {
            "taskPreamble": "Develop a complete, executable cloud-native application for the following requirements.",
            "artifactContractPreamble": "Produce the same semantic deliverables for framework-neutral comparison. Native diagram notation is allowed.",
            "artifactContract": ARTIFACT_CONTRACT,
        },
        "requirements": requirements,
        "constraints": constraints,
        "gates": [],
        "arms": [
            {
                "id": "easydep",
                "framework": "EasyDep",
                "frameworkVersion": "current",
                "promptProfile": "requirementsOnly",
                "timeoutSeconds": 18000,
                "command": [
                    "{python}", "-X", "utf8", "-m", "evaluation.comparison.subjects.easydep",
                    "--repository", "{repository}", "--run-dir", "{run_dir}",
                    "--prompt-file", "{prompt_file}", "--base-url", "http://127.0.0.1:8100",
                ],
            },
            {
                "id": "metagpt",
                "framework": "MetaGPT",
                "frameworkVersion": "0.8.2",
                "promptProfile": "commonArtifacts",
                "timeoutSeconds": 10800,
                "command": [
                    "{python}", "-X", "utf8", "-m", "evaluation.comparison.subjects.metagpt",
                    "--run-dir", "{run_dir}", "--prompt-file", "{prompt_file}",
                ],
            },
            {
                "id": "chatdev",
                "framework": "ChatDev",
                "frameworkVersion": "1.1.6",
                "promptProfile": "commonArtifacts",
                "timeoutSeconds": 10800,
                "command": [
                    "{python}", "-X", "utf8", "-m", "evaluation.comparison.subjects.chatdev",
                    "--run-dir", "{run_dir}", "--prompt-file", "{prompt_file}",
                ],
            },
        ],
    }


def materialize_manifests(
    suite: ComparisonSuite,
    *,
    case_ids: Iterable[str] | None = None,
    repetitions: int | None = None,
) -> list[Path]:
    selected = set(case_ids or ())
    unknown = selected - {case.id for case in suite.cases}
    if unknown:
        raise ValueError(f"suite에 없는 사례입니다: {', '.join(sorted(unknown))}")
    count = repetitions if repetitions is not None else suite.repetitions
    if count < 1:
        raise ValueError("repetitions는 1 이상이어야 합니다.")
    directory = suite.output_root / suite.id / "manifests"
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for case in suite.cases:
        if selected and case.id not in selected:
            continue
        path = directory / f"{case.id}.json"
        path.write_text(
            json.dumps(_manifest_data(suite, case, count), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths.append(path)
    return paths


def run_suite(
    suite: ComparisonSuite,
    *,
    case_ids: Iterable[str] | None = None,
    repetitions: int | None = None,
) -> tuple[Path, Path]:
    combined_runs: list[dict[str, Any]] = []
    manifests = materialize_manifests(
        suite, case_ids=case_ids, repetitions=repetitions
    )
    for path in manifests:
        manifest = load_manifest(path)
        report = run_experiment(manifest, output_root=suite.output_root)
        case_id = path.stem
        for run in report["runs"]:
            run["caseId"] = case_id
        combined_runs.extend(report["runs"])
        write_reports(report, suite.output_root / manifest.experiment_id)
    combined = {
        "schemaVersion": "easydep-comparison-suite-report/v1",
        "experimentId": suite.id,
        "repetitions": repetitions or suite.repetitions,
        "cases": [path.stem for path in manifests],
        "promptProtocol": {"artifactContract": ARTIFACT_CONTRACT},
        "runs": combined_runs,
    }
    return write_reports(combined, suite.output_root / suite.id)

