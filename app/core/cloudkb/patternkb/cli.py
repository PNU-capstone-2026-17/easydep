"""patternkb CLI: build / search / coverage.

    python -m patternkb build
    python -m patternkb search "circuit breaker"
    python -m patternkb coverage
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.core.cloudkb.patternkb.agent_api import _MISSING, coverage_text, search_patterns
from app.core.cloudkb.patternkb.dataset import CORPUS_FILE, is_built

DEFAULT_OUTPUT = Path("output") / CORPUS_FILE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patternkb", description="설계 패턴·원칙 산문 검색 (advisory 전용)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument("--refresh", action="store_true")

    aws_waf = sub.add_parser(
        "build-aws-waf",
        help="AWS WAF 화이트페이퍼 PDF → 코퍼스 (공정이용 수록 — NOTICE 참조)",
    )
    aws_waf.add_argument("--output", type=Path, default=None)
    aws_waf.add_argument("--refresh", action="store_true")

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--top", type=int, default=3)

    sub.add_parser("coverage")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    from app.core.cloudkb.patternkb.parsers import corpus

    corpus.build(args.output, refresh=args.refresh)
    return 0


def _cmd_build_aws_waf(args: argparse.Namespace) -> int:
    from app.core.cloudkb.patternkb.dataset import AWS_CORPUS_FILE
    from app.core.cloudkb.patternkb.parsers import aws_waf

    output = args.output or (Path("output") / AWS_CORPUS_FILE)
    aws_waf.build(output, refresh=args.refresh)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    print(search_patterns(args.query, top=args.top))
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    print(coverage_text())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return {
        "build": _cmd_build,
        "build-aws-waf": _cmd_build_aws_waf,
        "search": _cmd_search,
        "coverage": _cmd_coverage,
    }[args.command](args)
