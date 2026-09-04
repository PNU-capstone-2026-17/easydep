"""비교 자동화 CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from .models import Manifest, load_manifest
from .report import write_reports
from .runner import run_experiment
from .suite import load_suite, materialize_manifests, run_suite


def _gate_summary(manifest: Manifest) -> str:
    linked = sum(1 for item in manifest.requirements if item.verification_gates)
    required = sum(1 for gate in manifest.gates if gate.required)
    return (
        f"요구사항 {len(manifest.requirements)}개 중 {linked}개가 게이트에 연결됨, "
        f"필수 게이트 {required}개"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EasyDep/MetaGPT/ChatDev 비교 테스트 자동화")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="실행 없이 manifest 계약을 검사합니다.")
    validate.add_argument("manifest", type=Path)
    run = subparsers.add_parser("run", help="모든 대상과 반복을 실행하고 JSON/Markdown 보고서를 만듭니다.")
    run.add_argument("manifest", type=Path)
    run.add_argument("--output-root", type=Path, help="manifest의 outputRoot를 덮어씁니다.")
    validate_suite = subparsers.add_parser(
        "validate-suite", help="다중 사례 suite와 생성될 manifest를 검사합니다."
    )
    validate_suite.add_argument("suite", type=Path)
    validate_suite.add_argument("--case", action="append", dest="cases")
    validate_suite.add_argument("--repetitions", type=int)
    run_suite_parser = subparsers.add_parser(
        "run-suite", help="여러 도메인 사례를 실행하고 통합 보고서를 만듭니다."
    )
    run_suite_parser.add_argument("suite", type=Path)
    run_suite_parser.add_argument("--case", action="append", dest="cases")
    run_suite_parser.add_argument("--repetitions", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"validate-suite", "run-suite"}:
        suite = load_suite(args.suite)
        if args.command == "validate-suite":
            manifests = materialize_manifests(
                suite, case_ids=args.cases, repetitions=args.repetitions
            )
            print(
                f"유효한 suite: {suite.id} ({len(manifests)}개 사례, "
                f"{args.repetitions or suite.repetitions}회 반복, 3개 대상)"
            )
            for path in manifests:
                print(f"  - {path.stem}: {_gate_summary(load_manifest(path))}")
            return 0
        json_path, markdown_path = run_suite(
            suite, case_ids=args.cases, repetitions=args.repetitions
        )
        print(f"통합 JSON 결과: {json_path}")
        print(f"통합 Markdown 결과: {markdown_path}")
        return 0
    manifest = load_manifest(args.manifest)
    if args.command == "validate":
        profiles = ", ".join(
            f"{arm.id}={arm.prompt_profile}" for arm in manifest.arms
        )
        artifact_count = (
            len(manifest.prompt_protocol.artifact_contract)
            if manifest.prompt_protocol is not None
            else 0
        )
        print(
            f"유효한 manifest: {manifest.experiment_id} "
            f"({len(manifest.arms)}개 대상, {manifest.repetitions}회 반복, "
            f"공통 산출물 {artifact_count}개; {profiles})"
        )
        print(_gate_summary(manifest))
        return 0
    output_root = args.output_root
    report = run_experiment(manifest, output_root=output_root)
    root = output_root or Path(manifest.output_root)
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    json_path, markdown_path = write_reports(report, root / manifest.experiment_id)
    print(f"JSON 결과: {json_path}")
    print(f"Markdown 결과: {markdown_path}")
    return 0
