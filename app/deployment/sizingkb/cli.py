"""sizingkb CLI: build / show / coverage.

    python -m sizingkb build --source tumblebug
    python -m sizingkb build --source presets
    python -m sizingkb show --subnet 24 --provider aws
    python -m sizingkb coverage
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sizingkb.agent_api import (
    container_presets,
    coverage_text,
    reference_points,
    requirements,
    subnet_capacity,
)
from sizingkb.dataset import is_built

DEFAULT_OUTPUTS = {
    "tumblebug": Path("output") / "tumblebug-sizing.json",
    "presets": Path("output") / "container-presets.json",
    "reviewed": Path("output") / "reviewed-sizing.json",
}

_MISSING = "사이징 데이터셋이 없습니다. `python -m sizingkb build --source tumblebug`."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sizingkb", description="요구사항 → 자원 규모 변환 규칙"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--source", choices=tuple(DEFAULT_OUTPUTS), required=True)
    build.add_argument("--output", type=Path)
    build.add_argument("--refresh", action="store_true")

    show = sub.add_parser("show")
    show.add_argument("--subnet", type=int, help="프리픽스 길이 (예: 24)")
    show.add_argument("--provider", default="aws")
    show.add_argument("--scope", help="요구사항을 볼 범위 (예: k8s-node, aws)")
    show.add_argument("--workload", help="참조점 키워드 (예: web, llm)")
    show.add_argument("--presets", action="store_true", help="컨테이너 프리셋")

    sub.add_parser("coverage")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    output = args.output or DEFAULT_OUTPUTS[args.source]
    if args.source == "tumblebug":
        from sizingkb.parsers import tumblebug

        tumblebug.build(output, refresh=args.refresh)
    elif args.source == "reviewed":
        from sizingkb.parsers import reviewed

        reviewed.build(output, refresh=args.refresh)
    else:
        from sizingkb.parsers import presets

        presets.build(output, refresh=args.refresh)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    if args.subnet is not None:
        print(subnet_capacity(args.subnet, args.provider))
    if args.scope:
        print(requirements(args.scope))
    if args.workload:
        print(reference_points(args.workload))
    if args.presets:
        print(container_presets())
    if args.subnet is None and not (args.scope or args.workload or args.presets):
        print(requirements())
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    print(coverage_text())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return {"build": _cmd_build, "show": _cmd_show, "coverage": _cmd_coverage}[
        args.command
    ](args)
