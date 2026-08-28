"""명령 한 번으로 제품 평가 profile 실행·재개·집계를 선택한다."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from evaluation.easydep.product.catalog import load_profile, load_profile_catalog
from evaluation.easydep.product.report import aggregate_manifests
from evaluation.easydep.product.runner import (
    ProductEvaluationRunner,
    RunEnvironment,
    find_manifests,
)
from evaluation.easydep.product_scenario import HttpProductScenarioTransport


class _CliRunEnvironment(RunEnvironment):
    """CLI에서 받은 값이 서버 확인값이 아님을 manifest에 명시한다."""

    def as_dict(self) -> dict[str, Any]:
        value = super().as_dict()
        value.update(
            {
                "metadataSource": "cli-user-provided-labels",
                "serverConfigurationVerified": False,
            }
        )
        return value


def _commit() -> str:
    """현재 저장소 commit을 읽되 Git을 사용할 수 없으면 이유가 드러나는 값을 남긴다."""
    completed = subprocess.run(  # noqa: S603 - 인자가 고정된 로컬 Git 조회다.
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip() or "git-commit-unavailable"


def _settings(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("settings JSON은 객체여야 합니다.")
    return value


def _environment(args: argparse.Namespace) -> RunEnvironment:
    return _CliRunEnvironment(
        commit=args.commit or _commit(),
        provider=args.provider,
        model=args.model,
        settings=_settings(args.settings_json),
    )


def _runner(args: argparse.Namespace) -> ProductEvaluationRunner:
    return ProductEvaluationRunner(
        lambda: HttpProductScenarioTransport(args.base_url),
        args.output,
        timeout_seconds=args.timeout_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    """초보자도 각 실행의 차이를 help에서 확인할 수 있는 parser를 만든다."""
    parser = argparse.ArgumentParser(description="EasyDep 공개 제품 경로 반복 평가")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_connection_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--base-url", default="http://127.0.0.1:8000")
        command.add_argument("--output", type=Path, default=Path("artifacts/product-evaluation"))
        command.add_argument(
            "--provider",
            required=True,
            help="서버에 적용하지 않는 사용자 제공 provider label",
        )
        command.add_argument(
            "--model",
            required=True,
            help="서버에 적용하지 않는 사용자 제공 model label",
        )
        command.add_argument(
            "--settings-json",
            type=Path,
            help="서버 설정을 변경하지 않고 비교 label과 digest로만 저장할 JSON",
        )
        command.add_argument("--commit")
        command.add_argument("--timeout-seconds", type=float, default=7200.0)

    run = subparsers.add_parser("run", help="quick, stability, full, holdout 중 하나 실행")
    add_connection_arguments(run)
    run.add_argument("--profile", choices=("quick", "stability", "full", "holdout"), required=True)
    run.add_argument(
        "--allow-holdout-after-settings-lock",
        action="store_true",
        help="설정 확정 뒤에만 holdout 입력을 여는 명시적 확인",
    )

    resume = subparsers.add_parser("resume", help="실패 manifest의 같은 앱에서 재개")
    add_connection_arguments(resume)
    resume.add_argument("--manifest", type=Path, required=True)
    resume.add_argument("--allow-holdout-after-settings-lock", action="store_true")

    report = subparsers.add_parser("report", help="저장된 manifest를 집계")
    report.add_argument("--input", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        report = aggregate_manifests(find_manifests(args.input))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0

    if args.command == "run":
        profile = load_profile(
            args.profile,
            allow_holdout_after_settings_lock=args.allow_holdout_after_settings_lock,
        )
        # profile 선택과 holdout 확인을 먼저 끝낸 뒤 필요한 원문만 연다.
        # quick/full 실행이 holdout 원문을 읽는 일은 없다.
        catalog = load_profile_catalog(
            profile,
            allow_holdout_after_settings_lock=args.allow_holdout_after_settings_lock,
        )
        environment = _environment(args)
        runner = _runner(args)
        runner.run_profile(profile, environment, catalog=catalog)
        return 0

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    dataset_id = str((manifest.get("dataset") or {}).get("id") or "")
    profile_name = str((manifest.get("profile") or {}).get("name") or "")
    profile = load_profile(
        profile_name,
        allow_holdout_after_settings_lock=args.allow_holdout_after_settings_lock,
    )
    if dataset_id not in profile.dataset_ids:
        raise ValueError("manifest의 dataset이 저장된 profile에 속하지 않습니다.")
    catalog = load_profile_catalog(
        profile,
        allow_holdout_after_settings_lock=args.allow_holdout_after_settings_lock,
    )
    case = catalog[dataset_id]
    repetition = int((manifest.get("profile") or {}).get("repetition") or 1)
    environment = _environment(args)
    runner = _runner(args)
    runner.run_case(
        case,
        profile,
        repetition,
        environment,
        manifest_path=args.manifest,
    )
    return 0
