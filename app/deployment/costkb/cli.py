"""costkb CLI: build / query / coverage.

`build`는 cb-tumblebug의 `assets.dump.gz`(spec_infos 73k행)를 미러해
`output/tumblebug-cost.json`을 만든다. 빌드하지 않아도 번들 36건으로 동작한다.

사용 예:
    python -m costkb build
    python -m costkb build --tag v0.12.25 --refresh
    python -m costkb coverage
    python -m costkb query --vcpu-min 4 --provider aws --region us-east-1
    python -m costkb query --vcpu-min 64 --mem-min 256 --architecture arm64
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from costkb.agent_api import HOURS_PER_MONTH
from costkb.dataset import (
    BUILT_FILENAME,
    DEFAULT_OUTPUT_DIR,
    coverage,
    dataset_note,
    filter_specs,
    is_built,
    provider_summary,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="costkb",
        description="클라우드 인스턴스 스펙·가격 지식베이스 (cb-tumblebug spec_infos 미러)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="cb-tumblebug 덤프에서 미러를 빌드")
    build.add_argument("--tag", help="cb-tumblebug 태그 (기본: 고정 태그)")
    build.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
    build.add_argument(
        "--rows-file",
        type=Path,
        help="pg_restore로 미리 뽑아둔 COPY 텍스트 (pgdumplib 우회용)",
    )
    build.add_argument("--output", type=Path, help=f"출력 경로 (기본: output/{BUILT_FILENAME})")

    query = sub.add_parser("query", help="요구사항으로 스펙 후보 조회")
    query.add_argument("--vcpu-min", type=int, default=0, help="최소 vCPU")
    query.add_argument("--mem-min", type=float, default=0, help="최소 메모리(GiB, 미러 기준)")
    query.add_argument("--provider", help="aws | azure | gcp | tencent | …")
    query.add_argument("--region", help="리전 부분 문자열")
    query.add_argument(
        "--architecture",
        default="x86_64",
        help="기본 x86_64 (MCP와 동일). 'any'면 전체",
    )
    query.add_argument(
        "--sort-by", choices=("cost", "vcpu", "memory"), default="cost", help="정렬 기준"
    )
    query.add_argument("--limit", type=int, default=5, help="반환 개수 (기본 5)")
    query.add_argument(
        "--include-unpriced", action="store_true", help="가격 미상 후보도 포함"
    )

    sub.add_parser("coverage", help="데이터셋이 어디까지 커버하는지 요약")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    from costkb.parsers import dump as dump_reader
    from costkb.parsers.tumblebug import build_dataset, format_audit

    if args.rows_file:
        rows = dump_reader.iter_rows_from_copy_file(args.rows_file)
        source = str(args.rows_file)
    else:
        tag = args.tag or dump_reader.DEFAULT_TAG
        print(f"cb-tumblebug {tag}의 assets.dump.gz를 받는 중…", file=sys.stderr)
        path = dump_reader.fetch_dump(tag=tag, refresh=args.refresh)
        rows = dump_reader.iter_spec_rows(path)
        source = dump_reader.dump_url(tag)

    dataset, stats = build_dataset(rows)
    output = args.output or (DEFAULT_OUTPUT_DIR / BUILT_FILENAME)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    specs = dataset["specs"]
    priced = sum(1 for s in specs if s["hourlyUSD"] is not None)
    print(f"\n출처: {source}")
    print(format_audit(stats))
    print(
        f"\ncostkb: 레코드 {len(specs):,}개 (가격 있음 {priced:,} / "
        f"{priced / len(specs):.1%}) → {output} ({output.stat().st_size:,} B)"
    )
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    architecture = None if args.architecture.lower() == "any" else args.architecture
    found = filter_specs(
        args.vcpu_min,
        args.mem_min,
        args.provider,
        args.region,
        args.sort_by,
        args.limit,
        architecture=architecture,
        priced_only=not args.include_unpriced,
    )
    if not found:
        print("조건을 만족하는 스펙이 없습니다. 커버리지를 확인하세요:", file=sys.stderr)
        return _cmd_coverage(args) or 1

    print(f"후보 {len(found)}건 (정가·상시가동 {HOURS_PER_MONTH}h/월 기준):")
    for spec in found:
        hourly = spec["hourlyUSD"]
        price = (
            f"${hourly:.4f}/h ≈ ${hourly * HOURS_PER_MONTH:,.2f}/월"
            if hourly is not None
            else "가격 미상"
        )
        # 표시는 보정값, 필터는 미러값 — 다르면 둘 다 보여준다
        mem = spec["memGiB"]
        actual = spec.get("memGiBActual", mem)
        mem_text = f"{actual:g} GiB" + (f" (기준 {mem:g})" if actual != mem else "")
        print(
            f"  {spec['provider']:9} {spec['specName']:24} {spec['region']:16} "
            f"{spec['vCPU']:3} vCPU / {mem_text:22} {price}"
        )
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    rows = provider_summary()
    total = sum(r["count"] for r in rows)
    origin = "cb-tumblebug 미러" if is_built() else "번들 36건 (빌드 전)"
    print(f"데이터셋 커버리지 — {origin}, 총 {total:,}건:")
    for row in rows:
        print(
            f"  {row['provider']:10} {row['count']:6,}건  리전 {row['regions']:3}개  "
            f"가격있음 {row['priced'] / row['count']:5.1%}  "
            f"vCPU~{row['vcpu_max']:<4} 메모리~{row['mem_max_gib']:g} GiB"
        )
    if not is_built():
        for row in coverage():
            print(
                f"    · {row['provider']} {row['region']}: {row['count']}건, "
                f"vCPU {row['vcpu_min']}~{row['vcpu_max']}, "
                f"메모리 {row['mem_min_gib']}~{row['mem_max_gib']} GiB"
            )
        print("\n`python -m costkb build`로 cb-tumblebug 미러(73k건)를 만들 수 있습니다.")
    print(f"\n{dataset_note()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {"build": _cmd_build, "query": _cmd_query, "coverage": _cmd_coverage}
    try:
        return handlers[args.command](args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI 사용자에게 traceback 대신 메시지
        print(f"예기치 못한 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
