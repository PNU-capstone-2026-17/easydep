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
GATE_PACK_SCHEMA = "easydep-comparison-gate-pack/v1"
# 기본 게이트 팩은 사용자 suite 파일이 아니라 이 도구가 함께 배포한다.
GATE_PACK_DIRECTORY = Path(__file__).resolve().parent / "gate-packs"

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


def _tokens(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label}은 문자열 배열이어야 합니다.")
    return tuple(item.strip() for item in value if item.strip())


@dataclass(frozen=True)
class SuiteCase:
    id: str
    input_path: Path
    region_tokens: tuple[str, ...] = ()
    forbidden_tokens: tuple[str, ...] = ()
    provider: str = ""
    region: str = ""
    monthly_budget_usd: float | None = None
    gate_pack_path: Path | None = None

    def gate_pack(self) -> dict[str, Any]:
        """사례가 선언한 실행 게이트와 요구사항 연결을 읽는다. 없으면 빈 계약."""
        if self.gate_pack_path is None:
            return {"gates": [], "requirementGates": {}, "apiContract": ""}
        data = json.loads(self.gate_pack_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schemaVersion") != GATE_PACK_SCHEMA:
            raise ValueError(
                f"gate pack schemaVersion은 {GATE_PACK_SCHEMA!r}이어야 합니다: {self.gate_pack_path}"
            )
        gates = data.get("gates", [])
        if not isinstance(gates, list):
            raise ValueError(f"gate pack의 gates는 배열이어야 합니다: {self.gate_pack_path}")
        requirement_gates = data.get("requirementGates", {})
        if not isinstance(requirement_gates, dict):
            raise ValueError(
                f"gate pack의 requirementGates는 객체여야 합니다: {self.gate_pack_path}"
            )
        resolved: list[dict[str, Any]] = []
        for gate in gates:
            if not isinstance(gate, dict):
                raise ValueError(f"gate pack의 게이트는 객체여야 합니다: {self.gate_pack_path}")
            item = dict(gate)
            raw_oracle = item.get("oraclePath")
            if raw_oracle:
                # manifest는 outputRoot 아래에 생성되므로 팩 기준 상대 경로를 미리 절대화한다.
                oracle = Path(str(raw_oracle))
                if not oracle.is_absolute():
                    oracle = (self.gate_pack_path.parent / oracle).resolve()
                item["oraclePath"] = str(oracle)
            resolved.append(item)
        return {
            "gates": resolved,
            "requirementGates": {
                str(key): [str(value) for value in values]
                for key, values in requirement_gates.items()
            },
            "apiContract": str(data.get("apiContract", "")).strip(),
        }


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
        provider = str(item.get("provider", "")).strip()
        if provider and provider not in {"aws", "azure", "gcp"}:
            raise ValueError(f"cases[{index}].provider는 aws, azure, gcp 중 하나여야 합니다.")
        region = str(item.get("region", "")).strip()
        raw_budget = item.get("monthlyBudgetUSD")
        if raw_budget is not None and (
            not isinstance(raw_budget, (int, float)) or isinstance(raw_budget, bool) or raw_budget <= 0
        ):
            raise ValueError(f"cases[{index}].monthlyBudgetUSD는 0보다 큰 수여야 합니다.")
        region_tokens = _tokens(item.get("regionTokens", []), f"cases[{index}].regionTokens")
        forbidden_tokens = _tokens(
            item.get("forbiddenTokens", []), f"cases[{index}].forbiddenTokens"
        )
        raw_pack = item.get("gatePack")
        if raw_pack:
            pack_path = Path(str(raw_pack))
            if not pack_path.is_absolute():
                pack_path = (source.parent / pack_path).resolve()
            if not pack_path.is_file():
                raise ValueError(f"cases[{index}].gatePack 파일이 없습니다: {pack_path}")
        else:
            candidate = GATE_PACK_DIRECTORY / f"{case_id}.json"
            pack_path = candidate if candidate.is_file() else None
        cases.append(
            SuiteCase(
                id=case_id,
                input_path=input_path,
                region_tokens=region_tokens,
                forbidden_tokens=forbidden_tokens,
                gate_pack_path=pack_path,
                provider=provider,
                region=region,
                monthly_budget_usd=float(raw_budget) if raw_budget is not None else None,
            )
        )
    if len(cases) < 2:
        raise ValueError("단일 주제 편향을 줄이려면 suite에 두 개 이상의 사례가 필요합니다.")
    raw_output = Path(str(data.get("outputRoot", "artifacts/comparison")))
    output_root = raw_output if raw_output.is_absolute() else (Path.cwd() / raw_output).resolve()
    return ComparisonSuite(source, suite_id, repetitions, output_root, tuple(cases))


DESIGN_ARTIFACTS = ("classDiagram", "sequenceDiagram", "apiSpecification", "dataModel")
# 배포 산출물은 개별 요구사항 ID를 담지 않으므로 추적 근거 단계에서 제외한다.
EVIDENCE_STAGES = ("requirements", "design", "code", "test")


def _structural_gates(case: SuiteCase) -> tuple[list[dict[str, Any]], list[str]]:
    """모든 사례에 공통인 산출물·클라우드 제약 검사와 제약조건에 연결할 게이트 ID."""
    gates: list[dict[str, Any]] = [
        {"id": "design-artifacts", "kind": "artifactPresent", "artifacts": list(DESIGN_ARTIFACTS)},
        {"id": "code-artifact", "kind": "artifactPresent", "artifacts": ["sourceCode"]},
        {"id": "test-artifact", "kind": "artifactPresent", "artifacts": ["tests"]},
        {"id": "container-artifact", "kind": "artifactPresent", "artifacts": ["container"]},
        {"id": "iac-artifact", "kind": "artifactPresent", "artifacts": ["infrastructure"]},
    ]
    constraint_gates = ["iac-artifact"]
    if case.region_tokens:
        gates.append(
            {
                "id": "iac-region",
                "kind": "artifactContains",
                "artifact": "infrastructure",
                "anyOf": list(case.region_tokens),
            }
        )
        constraint_gates.append("iac-region")
    if case.forbidden_tokens:
        gates.append(
            {
                "id": "iac-forbidden",
                "kind": "artifactContains",
                "artifact": "infrastructure",
                "noneOf": list(case.forbidden_tokens),
            }
        )
        constraint_gates.append("iac-forbidden")
    return gates, constraint_gates


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
                    "evidenceStages": list(EVIDENCE_STAGES),
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
                    "evidenceStages": list(EVIDENCE_STAGES),
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


def _cloud_arguments(case: SuiteCase) -> list[str]:
    """사례가 선언한 배포 좌표를 EasyDep arm 명령 인자로 만든다."""
    arguments: list[str] = []
    if case.provider:
        arguments.extend(["--provider", case.provider])
    if case.region:
        arguments.extend(["--region", case.region])
    if case.monthly_budget_usd is not None:
        arguments.extend(["--monthly-budget", str(case.monthly_budget_usd)])
    return arguments


def _manifest_data(suite: ComparisonSuite, case: SuiteCase, repetitions: int) -> dict[str, Any]:
    requirements, constraints = _case_requirements(case)
    gates, constraint_gates = _structural_gates(case)
    pack = case.gate_pack()
    pack_gate_ids = {str(gate.get("id")) for gate in pack["gates"]}
    gates.extend(pack["gates"])
    requirement_gates = pack["requirementGates"]
    known = {item["id"] for item in requirements}
    unknown = sorted(set(requirement_gates) - known)
    if unknown:
        raise ValueError(
            f"{case.id} gate pack이 존재하지 않는 요구사항을 참조합니다: {', '.join(unknown)}"
        )
    for requirement in requirements:
        references = requirement_gates.get(requirement["id"], [])
        missing = sorted(
            {reference.split("#", 1)[0] for reference in references} - pack_gate_ids
        )
        if missing:
            raise ValueError(
                f"{case.id} gate pack의 {requirement['id']}가 정의되지 않은 게이트를 "
                f"참조합니다: {', '.join(missing)}"
            )
        requirement["verificationGates"] = references
    for constraint in constraints:
        constraint["verificationGates"] = list(constraint_gates)
    return {
        "schemaVersion": "easydep-comparison-manifest/v1",
        "experimentId": f"{suite.id}-{case.id}",
        "repetitions": repetitions,
        "outputRoot": str(suite.output_root),
        "promptProtocol": {
            "taskPreamble": "Develop a complete, executable cloud-native application for the following requirements.",
            "artifactContractPreamble": "Produce the same semantic deliverables for framework-neutral comparison. Native diagram notation is allowed.",
            "artifactContract": ARTIFACT_CONTRACT,
            "apiContract": pack["apiContract"],
        },
        "requirements": requirements,
        "constraints": constraints,
        "gates": gates,
        "arms": [
            {
                "id": "easydep",
                "framework": "EasyDep",
                "frameworkVersion": "current",
                "promptProfile": "requirementsOnly",
                "timeoutSeconds": 18000,
                # 세 실험군 모두 같은 클라우드 제약을 프롬프트로 받는다. EasyDep은 그 제약을
                # 앱 생성 화면의 구조화 입력으로도 받으므로, 좌표를 넘기지 않으면 요구사항
                # 분석이 리전을 되묻고 무인 실행이 멈춘다.
                "command": [
                    "{python}", "-X", "utf8", "-m", "evaluation.comparison.subjects.easydep",
                    "--repository", "{repository}", "--run-dir", "{run_dir}",
                    "--prompt-file", "{prompt_file}", "--base-url", "http://127.0.0.1:8100",
                    *_cloud_arguments(case),
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

