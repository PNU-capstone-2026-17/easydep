"""perfkb CLI: build / show / coverage.

    python -m perfkb build
    python -m perfkb build --tag v0.12.25 --refresh
    python -m perfkb coverage
    python -m perfkb show --provider aws --spec t3.medium

## 덤프 리더는 kbcommon에서 온다

`kbcommon/tumblebug_dump.py`가 spec_infos 행을 읽는다. costkb(가격 컬럼)와 perfkb
(details 컬럼)가 **같은 테이블의 다른 컬럼**을 보므로 둘 다 그 리더를 공유한다 —
`fetch_cached`가 kbcommon에 있는 것과 같은 이유다. perfkb는 costkb를 import하지 않는다
(KB 간 단방향 규약). 조인은 도구 계층에서 `id`로 한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from perfkb.dataset import (
    BUILT_FILENAME,
    DEFAULT_OUTPUT_DIR,
    coverage,
    find,
    is_built,
    schema,
)
from perfkb.fields import FIELDS

_MISSING = (
    "성능 데이터셋이 없습니다. `python -m perfkb build`로 먼저 빌드하세요 "
    "(pgdumplib 필요: uv sync --extra perfkb)."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perfkb",
        description="클라우드 인스턴스 성능 특성 지식베이스 (cb-tumblebug spec_infos.details 미러)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="cb-tumblebug 덤프에서 성능 미러를 빌드")
    build.add_argument("--tag", help="cb-tumblebug 태그 (기본: 고정 태그)")
    build.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
    build.add_argument(
        "--rows-file", type=Path, help="pg_restore로 미리 뽑아둔 COPY 텍스트 (pgdumplib 우회용)"
    )
    build.add_argument(
        "--no-hardware", action="store_true",
        help="AWS 하드웨어 사실(CPU·GPU 모델) 덧붙이기를 건너뛴다",
    )
    build.add_argument("--output", type=Path, help=f"출력 경로 (기본: output/{BUILT_FILENAME})")

    show = sub.add_parser("show", help="특정 스펙의 성능 프로파일")
    show.add_argument("--provider", required=True, help="aws | azure | gcp")
    show.add_argument("--spec", required=True, help="CSP 스펙명 (t3.medium 등)")

    sub.add_parser("coverage", help="무엇을 알고 무엇을 모르는지 요약")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    from kbcommon import tumblebug_dump as dump_reader
    from perfkb.invariants import INVARIANTS
    from kbcommon.artifact import ArtifactInvalid, write_dataset
    from kbcommon.basis import describe
    from kbcommon.invariants import announce
    from kbcommon.fetch import describe_source

    from perfkb.parsers.build import build_dataset, format_audit

    if args.rows_file:
        rows = dump_reader.iter_rows_from_copy_file(args.rows_file)
        source = str(args.rows_file)
        source_path = Path(args.rows_file)
    else:
        tag = args.tag or dump_reader.DEFAULT_TAG
        print(f"cb-tumblebug {tag}의 assets.dump.gz를 받는 중…", file=sys.stderr)
        path = dump_reader.fetch_dump(tag=tag, refresh=args.refresh)
        rows = dump_reader.iter_spec_rows(path)
        source = dump_reader.dump_url(tag)
        source_path = path

    dataset, stats = build_dataset(rows)
    sources = [describe_source(source_path, "tumblebug-dump")]

    # **하드웨어 사실을 같은 빌드 안에서 덧붙인다.** 별도 명령으로 두면 perfkb를
    # 다시 빌드할 때 조용히 사라진다 — 사라져도 아무 표시가 없는 종류의 실패다.
    if not args.no_hardware:
        from perfkb.parsers import hardware

        try:
            table, hw_path = hardware.fetch(refresh=args.refresh)
        except Exception as exc:  # noqa: BLE001 — 부가 정보가 본체를 막지 않는다
            print(f"\n⚠ 하드웨어 사실을 받지 못해 건너뜁니다: {exc}", file=sys.stderr)
        else:
            report = hardware.enrich(dataset["specs"], table)
            print(f"\n{hardware.format_report(report)}")
            sources.append(describe_source(hw_path, "ec2-hardware"))

    dataset["_source"] = sources
    output = args.output or (DEFAULT_OUTPUT_DIR / BUILT_FILENAME)

    print(f"\n출처: {source}")
    print(format_audit(stats))
    # 검증 후 원자적 교체 — 쓰다 만 파일이 남으면 비용 추천까지 죽었다(결함 (마)).
    try:
        announce(write_dataset(output, dataset, schema(), INVARIANTS), "perfkb")
    except ArtifactInvalid as exc:
        print(f"\n✗ 산출물이 스키마를 위반해 쓰지 않았습니다 — {exc}", file=sys.stderr)
        print(
            "  상위 덤프의 details 구조가 바뀌었을 수 있습니다. perfkb/schema.json과 "
            f"parsers/를 확인하세요.\n  기존 산출물이 있다면 그대로 유지됩니다: {output}",
            file=sys.stderr,
        )
        return 1

    specs = dataset["specs"]
    print(f"\nperfkb: 레코드 {len(specs):,}개 → {output} ({output.stat().st_size:,} B)")
    return 0


def _describe(rec: dict) -> str:
    lines = [f"{rec['provider']} {rec['specName']}  ({rec['id']})"]
    sustained = rec.get("sustainedCpu")
    if sustained:
        mark = "예" if sustained["value"] else "아니오"
        lines.append(
            f"  상시 CPU 성능 보장: {mark} "
            f"(근거 {sustained['evidence']}, {describe(sustained.get('basis', ''))})"
        )
        if sustained.get("note"):
            lines.append(f"    ⚠ {sustained['note']}")
    # 목록은 `perfkb.fields` 하나뿐이다 — 예전엔 여기와 agent_api가 각자 목록을 갖고
    # 있어서 사람이 보는 것과 에이전트가 보는 것이 달랐다(CLI만 벤더 설명을 출력했다).
    for field in FIELDS:
        if rec.get(field.key) is not None:
            lines.append(f"  {field.label}: {field.render(rec[field.key])}")
    if rec.get("networkIsBurst"):
        lines.append("    ⚠ 네트워크 대역폭이 버스트입니다('Up to') — 지속 값이 아닙니다.")
    return "\n".join(lines)


def _cmd_show(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    found = find(provider=args.provider, spec_name=args.spec)
    if not found:
        print(f"{args.provider} {args.spec}: 성능 데이터가 없습니다.", file=sys.stderr)
        return 1
    # 리전마다 레코드가 있지만 성능은 리전 불변이라 하나만 보여준다.
    print(_describe(found[0]))
    if len(found) > 1:
        print(f"\n(같은 스펙이 {len(found)}개 리전에 있으며 성능 값은 동일합니다)")
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    if not is_built():
        print(_MISSING, file=sys.stderr)
        return 1
    print("성능 데이터 커버리지 — 무엇을 알고 무엇을 모르는가:")
    for row in coverage():
        n = row["count"]
        print(
            f"  {row['provider']:6} {n:6,}건  "
            f"상시CPU판정 {row['sustainedCpu'] / n:5.1%}  "
            f"세대 {row['currentGeneration'] / n:5.1%}  "
            f"ACU {row['acu'] / n:5.1%}  "
            f"→ 상시성능 미보장 {row['not_sustained']:,}건"
        )
    print(
        "\n※ 프로바이더 간 성능 비교는 불가능합니다 — ACU는 Azure에만, 클럭은 AWS에만 "
        "있습니다. 값이 없는 건 '느리다'가 아니라 '모른다'입니다."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return {"build": _cmd_build, "show": _cmd_show, "coverage": _cmd_coverage}[
        args.command
    ](args)
