"""perfkb CLI: build / show / coverage.

    python -m perfkb build
    python -m perfkb build --tag v0.12.25 --refresh
    python -m perfkb coverage
    python -m perfkb show --provider aws --spec t3.medium

## 덤프 리더를 costkb에서 빌려 쓴다 (의도된 결합)

`costkb/parsers/dump.py`는 이름과 달리 cost 지식이 0이다 — 전부 "tumblebug 덤프에서
spec_infos 행을 읽는 법"이라 사실상 인프라다. perfkb는 **같은 테이블의 다른 컬럼**을
보므로 그 리더가 그대로 필요하다.

규약상으로는 `kbcommon`에 올리는 게 맞다(kbcommon의 존재 이유가 "같은 공개 스키마 소스를
공유"다). 다만 소비자가 둘뿐이라 지금은 승격하지 않고 빌려 쓴다. **셋째 소비자가 생기거나
costkb가 dump.py의 시그니처를 바꿔야 할 때 kbcommon으로 올린다.**

빌려 쓰는 건 **행 리더뿐이고 데이터 모델이 아니다** — perfkb는 costkb의 레코드나 스키마를
전혀 모른다. 조인은 도구 계층에서 `id`로 한다.
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
)

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
    build.add_argument("--output", type=Path, help=f"출력 경로 (기본: output/{BUILT_FILENAME})")

    show = sub.add_parser("show", help="특정 스펙의 성능 프로파일")
    show.add_argument("--provider", required=True, help="aws | azure | gcp")
    show.add_argument("--spec", required=True, help="CSP 스펙명 (t3.medium 등)")

    sub.add_parser("coverage", help="무엇을 알고 무엇을 모르는지 요약")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    # costkb의 덤프 리더를 빌려 쓴다 — 이유는 모듈 docstring 참고.
    from costkb.parsers import dump as dump_reader

    from perfkb.parsers.build import build_dataset, format_audit

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
    print(f"\n출처: {source}")
    print(format_audit(stats))
    print(f"\nperfkb: 레코드 {len(specs):,}개 → {output} ({output.stat().st_size:,} B)")
    return 0


def _describe(rec: dict) -> str:
    lines = [f"{rec['provider']} {rec['specName']}  ({rec['id']})"]
    sustained = rec.get("sustainedCpu")
    if sustained:
        mark = "예" if sustained["value"] else "아니오"
        lines.append(
            f"  상시 CPU 성능 보장: {mark} "
            f"(근거 {sustained['evidence']}, 신뢰도 {sustained['confidence']})"
        )
        if sustained.get("note"):
            lines.append(f"    ⚠ {sustained['note']}")
    for key, label in (
        ("currentGeneration", "최신 세대"),
        ("clockGHz", "클럭(GHz)"),
        ("networkPerformance", "네트워크"),
        ("ebsBaselineMbps", "EBS baseline(Mbps)"),
        ("ebsMaxMbps", "EBS 최대(Mbps)"),
        ("acu", "ACU (Azure 내부 비교용)"),
        ("diskIops", "디스크 IOPS"),
        ("vendorDescription", "벤더 설명"),
    ):
        if key in rec:
            lines.append(f"  {label}: {rec[key]}")
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
