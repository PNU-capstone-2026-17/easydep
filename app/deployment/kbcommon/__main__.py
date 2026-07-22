"""KB **사이**의 정합성 검사. `python -m kbcommon verify`

## 왜 빌드가 아니라 여기인가

지식베이스는 서로 import하지 않는다(단방향 규약: 에이전트 → KB). 그래서
"capacitykb의 타입이 graphkb에 있는가" 같은 검사를 어느 한쪽 빌드 안에 넣을 수 없다 —
넣는 순간 규약이 깨진다.

대신 여기서는 산출물 JSON을 **데이터로 읽는다.** 패키지를 부르지 않으므로 규약은
그대로고, 두 축을 나란히 놓고 볼 수 있다. KB 하나만 다시 빌드해도 조인이 깨질 수
있으니, 빌드와 분리된 검사가 오히려 맞다.

각 KB 내부의 레코드 간 검사는 빌드 시점에 돈다(`kbcommon/invariants.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kbcommon.invariants import Violation
from kbcommon.console import use_utf8

DEFAULT_OUTPUT = Path("output")


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _graph_node_ids(out: Path) -> set[str]:
    ids: set[str] = set()
    for path in sorted(out.glob("*-graph.json")):
        data = _load(path)
        if not data:
            continue
        nodes = data.get("nodes")
        ids |= set(nodes) if isinstance(nodes, dict) else {
            n["id"] for n in nodes or [] if isinstance(n, dict) and "id" in n
        }
    return ids


def check_capacity_joins_graph(out: Path) -> tuple[str, list[Violation], int]:
    """capacitykb의 모든 `type_id`가 graphkb 노드로 이어지는가.

    `capacitykb/model/records.py`가 "두 지식베이스는 코드가 분리돼 있지만 이 규약
    덕분에 질의 시점에 조인할 수 있다"고 명시한 규약이다. 깨지면 용량 질문에
    "정보 없음"이 나오는데, 데이터가 없어서가 아니라 **id 철자가 달라서**다.
    """
    node_ids = _graph_node_ids(out)
    violations: list[Violation] = []
    total = 0
    for path in sorted(out.glob("*-capacity.json")):
        data = _load(path)
        if not data:
            continue
        type_ids = {c.get("type_id") for c in data.get("constraints") or []}
        total += len(type_ids)
        for missing in sorted(type_ids - node_ids):
            violations.append(
                Violation(where=path.name, detail=f"graphkb에 없는 타입: {missing}")
            )
    return "capacity-joins-graph", violations, total


def check_bundle_matches_mirror(out: Path) -> tuple[str, list[Violation], int]:
    """번들 스펙의 메모리가 빌드 산출물과 같은가.

    번들 36건은 산출물이 없을 때 쓰이는 폴백이라, 둘이 어긋나면 **빌드 유무에 따라
    답이 달라진다.**

    예전에는 미러의 `memGiBActual`(보정값)과 대조했다 — 산출물이 상류 버그를 그대로
    담고 보정값을 따로 두던 시절이다. 지금은 **빌드가 고쳐서 담으므로** `memGiB`끼리
    비교하면 된다(`costkb/parsers/tumblebug.py`의 `_corrections`).
    """
    bundle_path = Path(__file__).resolve().parent.parent / "costkb" / "specs.json"
    bundle = _load(bundle_path)
    mirror = _load(out / "tumblebug-cost.json")
    if not bundle or not mirror:
        return "bundle-matches-mirror", [], 0

    by_key: dict[tuple, dict] = {}
    for spec in mirror.get("specs") or []:
        by_key.setdefault((spec.get("provider"), spec.get("specName")), spec)

    violations: list[Violation] = []
    specs = bundle.get("specs") or []
    for spec in specs:
        key = (spec.get("provider"), spec.get("specName"))
        found = by_key.get(key)
        where = f"{key[0]} {key[1]}"
        if found is None:
            violations.append(Violation(where=where, detail="미러에 같은 스펙이 없습니다"))
            continue
        built = found.get("memGiB")
        if built is None:
            continue
        if abs(float(spec.get("memGiB", 0)) - float(built)) > 1e-6:
            violations.append(
                Violation(
                    where=where,
                    detail=f"번들 memGiB={spec.get('memGiB')} vs 산출물={built}",
                )
            )
    return "bundle-matches-mirror", violations, len(specs)


CHECKS = (check_capacity_joins_graph, check_bundle_matches_mirror)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kbcommon", description="KB 사이의 정합성 검사")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="산출물끼리 이어지는지 확인한다")
    verify.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="산출물 디렉터리 (기본: output)")

    # 리전 이름은 네 KB가 다 쓰는 공용 축이라 여기서 빌드한다(어느 KB에도 안 속한다).
    regions = sub.add_parser(
        "build-regions", help="프로바이더별 리전 이름·위치 (cb-tumblebug cloudinfo)"
    )
    regions.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
    regions.add_argument("--output", type=Path, help="출력 경로 (기본: output/cloud-regions.json)")

    carbon_cmd = sub.add_parser(
        "build-carbon", help="리전별 탄소 (GCP 발표 + Cloud Carbon Footprint 추정)"
    )
    carbon_cmd.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
    carbon_cmd.add_argument("--output", type=Path, help="출력 경로 (기본: output/region-carbon.json)")

    life = sub.add_parser(
        "build-lifecycle", help="관리형 서비스 버전별 지원 종료일 (endoflife.date)"
    )
    life.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
    life.add_argument("--output", type=Path, help="출력 경로 (기본: output/service-lifecycle.json)")

    spider = sub.add_parser(
        "build-cbspider", help="CSP별로 무엇을 만들 수 있는가 (cb-spider 드라이버)"
    )
    spider.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
    spider.add_argument("--output", type=Path, help="출력 경로 (기본: output/cbspider-support.json)")

    args = parser.parse_args(argv)

    if args.command == "build-cbspider":
        from kbcommon import cbspider

        cbspider.build(
            args.output or (DEFAULT_OUTPUT / "cbspider-support.json"),
            refresh=args.refresh,
        )
        return 0

    if args.command == "build-lifecycle":
        from kbcommon import lifecycle

        lifecycle.build(
            args.output or (DEFAULT_OUTPUT / "service-lifecycle.json"),
            refresh=args.refresh,
        )
        return 0

    if args.command == "build-regions":
        from kbcommon import cloudinfo

        cloudinfo.build(
            args.output or (DEFAULT_OUTPUT / "cloud-regions.json"), refresh=args.refresh
        )
        return 0

    if args.command == "build-carbon":
        from kbcommon import carbon

        carbon.build(
            args.output or (DEFAULT_OUTPUT / "region-carbon.json"), refresh=args.refresh
        )
        return 0

    if not args.output.exists():
        print(f"산출물 디렉터리가 없습니다: {args.output} — 먼저 빌드하세요.", file=sys.stderr)
        return 2

    failed = 0
    for check in CHECKS:
        name, violations, total = check(args.output)
        if total == 0:
            print(f"− {name}: 검사할 산출물이 없습니다 (빌드 안 됨)")
            continue
        if not violations:
            print(f"✓ {name}: {total:,}건 전부 통과")
            continue
        failed += 1
        print(f"✗ {name}: {total:,}건 중 {len(violations)}건 실패")
        for v in violations[:10]:
            print(f"    {v.where}: {v.detail}")
        if len(violations) > 10:
            print(f"    … 외 {len(violations) - 10}건")
    return 1 if failed else 0


if __name__ == "__main__":
    use_utf8()
    raise SystemExit(main())
