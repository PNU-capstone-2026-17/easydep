"""비교 자동화 CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from .models import load_manifest
from .report import write_reports
from .runner import run_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EasyDep/MetaGPT/ChatDev 비교 테스트 자동화")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="실행 없이 manifest 계약을 검사합니다.")
    validate.add_argument("manifest", type=Path)
    run = subparsers.add_parser("run", help="모든 대상과 반복을 실행하고 JSON/Markdown 보고서를 만듭니다.")
    run.add_argument("manifest", type=Path)
    run.add_argument("--output-root", type=Path, help="manifest의 outputRoot를 덮어씁니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
