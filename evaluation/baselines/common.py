"""Shared input, output, and reproducibility helpers for all baselines."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.orchestration.run_identity import identity_manifest, make_run_id

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = ROOT / "artifacts" / "runs"

BUILD_COMPLETENESS_CONTRACT = """
Repository completeness rules:
- Every path referenced by Dockerfile COPY/ADD or a build script must exist in the returned repository.
- Include settings.gradle or settings.gradle.kts and every declared build/configuration file.
- Do not use the Gradle wrapper because its JAR is a binary that cannot be represented safely in the
  text artifact format. Use an official Gradle JDK 21 builder image in the Dockerfile instead.
- Do not return binary files such as JARs or keystores. Generate disposable build-time material from
  text instructions when needed.
- Pin mutually compatible Gradle, Spring Boot, Java 21, and optional Kotlin plugin versions. If using
  Kotlin, configure a Java 21-capable Kotlin version and compiler target explicitly.
- Before finishing, mentally execute Docker build and verify that every COPY source and build command
  is satisfiable by the returned files.
- Every generated source, test, build, configuration, Docker, and Terraform file must contain only
  its native file syntax. Never place Markdown headings, path labels, commentary, or fenced code
  markers inside a generated file.
""".strip()

# EasyDep Settings와 같은 루트 .env를 읽는다. 셸에서 명시한 값은 유지되며 모든
# 비교군은 같은 프로세스 환경을 전달받는다.
load_dotenv(ROOT / ".env", override=False)


def canonical_json_sha256(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def model() -> str:
    return os.getenv("MODEL", "openai/gpt-oss-120b")


def base_url() -> str:
    return os.getenv("BASE_URL", "https://integrate.api.nvidia.com/v1")


def temperature() -> float:
    return float(os.getenv("TEMPERATURE", "0"))


def seed() -> int:
    return int(os.getenv("SEED", "42"))


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    requirements: list[str]
    cloud_constraints: str
    scope: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> ExperimentCase:
        raw = json.loads(path.read_text(encoding="utf-8"))
        required = {"caseId", "requirements", "cloudConstraints", "scope"}
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"case fields missing: {', '.join(missing)}")
        if not isinstance(raw["requirements"], list) or not raw["requirements"]:
            raise ValueError("requirements must be a non-empty list")
        return cls(
            case_id=str(raw["caseId"]),
            requirements=[str(item) for item in raw["requirements"]],
            cloud_constraints=str(raw["cloudConstraints"]),
            scope=dict(raw["scope"]),
        )

    def prompt(self) -> str:
        requirements = "\n".join(f"R{i}: {text}" for i, text in enumerate(self.requirements, 1))
        return (
            f"[CASE]\n{self.case_id}\n\n[SOFTWARE REQUIREMENTS]\n{requirements}\n\n"
            f"[CLOUD CONSTRAINTS]\n{self.cloud_constraints}\n\n"
            f"[FIXED SCOPE]\n{json.dumps(self.scope, ensure_ascii=False)}"
        )


@dataclass(frozen=True)
class ExperimentSuite:
    development: tuple[Path, ...]
    holdout: tuple[Path, ...]
    repetitions: int
    arms: tuple[str, ...]
    oracle: Path
    study_design: str

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_arms: set[str] | None = None,
    ) -> ExperimentSuite:
        raw = json.loads(path.read_text(encoding="utf-8"))
        development = tuple(path.parent / str(name) for name in raw.get("development", []))
        holdout = tuple(path.parent / str(name) for name in raw.get("holdout", []))
        all_paths = development + holdout
        study_design = str(raw.get("studyDesign", "end-to-end"))
        if not development:
            raise ValueError("suite requires a non-empty development split")
        if study_design == "end-to-end" and not holdout:
            raise ValueError("end-to-end suite requires a non-empty holdout split")
        if set(development) & set(holdout):
            raise ValueError("development and holdout splits must be disjoint")
        hashes = raw.get("frozenHashes", {})
        if set(hashes) != {item.name for item in all_paths}:
            raise ValueError("frozenHashes must cover every suite case exactly once")
        cases: list[ExperimentCase] = []
        for case_path in all_paths:
            if not case_path.is_file():
                raise FileNotFoundError(case_path)
            digest = canonical_json_sha256(case_path)
            if digest != hashes[case_path.name]:
                raise ValueError(f"frozen hash mismatch: {case_path.name}")
            case = ExperimentCase.load(case_path)
            providers = case.scope.get("providers")
            if not isinstance(providers, list) or len(providers) != 1:
                raise ValueError(f"case must select exactly one provider: {case.case_id}")
            if case.scope.get("workload") != "docker-on-vm":
                raise ValueError(f"case is outside Docker-on-VM scope: {case.case_id}")
            cases.append(case)
        oracle_path = path.parent / str(raw.get("oracle", ""))
        if not oracle_path.is_file():
            raise FileNotFoundError("suite oracle is missing")
        oracle_digest = canonical_json_sha256(oracle_path)
        if oracle_digest != raw.get("oracleHash"):
            raise ValueError("frozen oracle hash mismatch")
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("caseId values must be unique")
        if study_design == "end-to-end":
            development_coverage = {
                (case.case_id.split("-", 1)[0], case.scope["providers"][0])
                for case in cases[: len(development)]
            }
            expected = {(profile, provider) for profile in ("P1", "P2", "P3")
                        for provider in ("aws", "azure", "gcp")}
            if development_coverage != expected:
                raise ValueError("development must cross P1-P3 with all three providers")
            holdout_cases = cases[len(development) :]
            if {case.case_id for case in holdout_cases} != {"H1-azure", "H2-gcp", "H3-aws"}:
                raise ValueError("holdout must contain the three frozen domain cases")
        elif study_design == "paired-components":
            declared_pairs = raw.get("pairs")
            if not isinstance(declared_pairs, list) or not declared_pairs:
                raise ValueError("paired-components suite requires declared pairs")
            actual = {
                (
                    str(case.scope.get("pairId", "")),
                    str(case.scope.get("condition", "")),
                    str(case.scope["providers"][0]),
                )
                for case in cases
            }
            expected_pairs = {
                (str(pair["id"]), condition, provider)
                for pair in declared_pairs
                for condition in ("control", "treatment")
                for provider in ("aws", "azure", "gcp")
            }
            if actual != expected_pairs or len(cases) != len(expected_pairs):
                raise ValueError(
                    "paired-components must contain control/treatment for every pair and provider"
                )
            if holdout:
                raise ValueError("paired-components suite must not reuse the domain holdout split")
        else:
            raise ValueError(f"unsupported studyDesign: {study_design}")
        repetitions = int(raw.get("repetitions", 0))
        if repetitions < 1:
            raise ValueError("repetitions must be positive")
        arms = tuple(str(item) for item in raw.get("arms", []))
        required_arms = expected_arms or {
            "easydep-full", "cot-standard", "metagpt-standard", "chatdev-standard"
        }
        if len(arms) != len(required_arms) or set(arms) != required_arms:
            raise ValueError(
                "suite arms do not match the required study arms: "
                + ", ".join(sorted(required_arms))
            )
        return cls(development, holdout, repetitions, arms, oracle_path, study_design)

    def cases(self, split: str) -> tuple[Path, ...]:
        if split == "development":
            return self.development
        if split == "holdout":
            return self.holdout
        raise ValueError("split must be development or holdout")

def begin_run(method: str, case: ExperimentCase, output_root: Path | None = None) -> Path:
    run_name = make_run_id(method, "standard", case.case_id)
    path = (output_root or DEFAULT_ARTIFACT_ROOT) / run_name
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def run_manifest(
    method: str, case: ExperimentCase, command: list[str], run_id: str
) -> dict[str, Any]:
    manifest = identity_manifest(
        run_id,
        system=method,
        variant="standard",
        case_id=case.case_id,
        purpose="evaluation",
    )
    manifest.update({
        "method": method,
        "startedAt": datetime.now(UTC).isoformat(),
        "gitRevision": git_revision(),
        "model": model(),
        "baseUrl": base_url(),
        "temperature": temperature(),
        "seed": seed(),
        "configurationSource": ".env",
        "python": platform.python_version(),
        "command": command,
        "webSearch": False,
    })
    return manifest


def require_api_key() -> None:
    if not os.getenv("API_KEY"):
        raise RuntimeError("API_KEY is required; load .env or set it in the environment")
