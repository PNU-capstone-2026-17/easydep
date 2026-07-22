"""bundlekb CLI: build / show / coverage.

    python -m bundlekb build --source avm
    python -m bundlekb show --type "Microsoft.Compute/virtualMachines"
    python -m bundlekb coverage
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from bundlekb.dataset import (
    MIN_SAMPLES,
    all_bundles,
    all_companions,
    bundles_for,
    companions_of,
    is_built,
)

DEFAULT_OUTPUTS = {
    "avm": Path("output") / "avm-bundles.json",
    "tumblebug": Path("output") / "tumblebug-bundles.json",
    "aqt": Path("output") / "aqt-cooccurrence.json",
    "aws-patterns": Path("output") / "aws-pattern-bundles.json",
    "awscfn": Path("output") / "awscfn-cooccurrence.json",
}

_MISSING = "번들 데이터셋이 없습니다. `python -m bundlekb build --source avm` 으로 빌드하세요."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bundlekb", description="클라우드 리소스 군(번들) 지식베이스"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="소스에서 번들을 추출한다")
    build.add_argument("--source", choices=tuple(DEFAULT_OUTPUTS), required=True)
    build.add_argument("--output", type=Path, help="출력 JSON 경로 (기본: output/)")
    build.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받는다")

    show = sub.add_parser("show", help="한 타입의 번들·동반 리소스를 본다")
    show.add_argument("--type", required=True)

    sub.add_parser("coverage", help="무엇을 알고 무엇을 모르는가")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    output = args.output or DEFAULT_OUTPUTS[args.source]
    if args.source == "avm":
        from bundlekb.parsers import avm

        avm.build(output, refresh=args.refresh)
    elif args.source == "tumblebug":
        from bundlekb.parsers import tumblebug

        tumblebug.build(output, refresh=args.refresh)
    elif args.source == "aqt":
        from bundlekb.parsers import aqt

        aqt.build(output, refresh=args.refresh)
    elif args.source == "aws-patterns":
        from bundlekb.parsers import aws_patterns

        aws_patterns.build(output, refresh=args.refresh)
    elif args.source == "awscfn":
        from bundlekb.parsers import awscfn

        awscfn.build(output, refresh=args.refresh)
    else:  # pragma: no cover - argparse가 막는다
        raise SystemExit(f"알 수 없는 소스: {args.source}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    found = bundles_for(args.type)
    print(f"{args.type} 가 중심인 번들 {len(found)}개")
    for bundle in found:
        print(f"\n  [{bundle.name}]  근거 {bundle.evidence}")
        if bundle.description:
            print(f"    {bundle.description[:100]}")
        if bundle.caveat:
            print(f"    ⚠ {bundle.caveat[:120]}")
        for tier in ("always", "required", "optional"):
            members = bundle.by_tier(tier)
            if not members:
                continue
            # 한 줄에 60종을 쏟으면 사람이 못 읽는다. 도구 계층도 같은 이유로 자른다.
            shown = [m.type_id.split("::", 1)[-1] for m in members[:10]]
            more = f" … 외 {len(members) - 10}종" if len(members) > 10 else ""
            print(f"    {tier} {len(members)}종: {', '.join(shown)}{more}")

    companions = companions_of(args.type)
    if companions:
        print(f"\n  실제 템플릿에서 함께 나온 비율 (표본 {companions[0].samples}개)")
        for c in companions[:12]:
            print(f"    {c.ratio:6.1%}  ({c.hits:4})  {c.type_id}")
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    bundles = all_bundles()
    companions = all_companions()
    print("번들 커버리지:")
    by_evidence: dict[str, int] = {}
    for b in bundles:
        by_evidence[b.evidence] = by_evidence.get(b.evidence, 0) + 1
    for evidence, n in sorted(by_evidence.items(), key=lambda kv: -kv[1]):
        print(f"  {evidence:26} 번들 {n:5,}개")
    anchors = {c.anchor for c in companions}
    usable = {c.anchor for c in companions if c.samples >= MIN_SAMPLES}
    print(f"\n동시 출현: 앵커 {len(anchors):,}종 · 쌍 {len(companions):,}건")
    print(f"  표본 {MIN_SAMPLES}개 이상이라 비율을 낼 수 있는 앵커: {len(usable):,}종")
    print(
        "\n※ '무조건/필수'는 소스가 명시한 것이고, 동시 출현 비율은 **코퍼스에서 센 값**"
        "입니다 — 클라우드가 강제한다는 뜻이 아닙니다."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return {"build": _cmd_build, "show": _cmd_show, "coverage": _cmd_coverage}[
        args.command
    ](args)
