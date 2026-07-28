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

from app.deployment.kbcommon.console import use_utf8
from app.deployment.kbcommon.invariants import Violation

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


#: **재배포가 금지돼 절대 포장하면 안 되는 산출물.** 소스 표의 `denied` — 여기
#: 있는 이름은 pack이 거부하고, 커밋되면 tests/test_costkb_aws_managed.py가 잡는다.
PACK_FORBIDDEN = frozenset({"aws-managed-pricing.json"})


def pack_artifacts(names: list[str], output_dir: Path, data_dir: Path) -> int:
    """`output/<이름>` → `data/<이름>.gz`. 매번 일회성 스크립트로 하던 반복 수작업의
    명령화 — 이름을 안 주면 data/에 이미 있는 산출물 전부를 다시 포장한다."""
    import gzip

    wanted = names or sorted(
        p.name[:-3] for p in data_dir.glob("*.json.gz")
    )
    failed = 0
    for name in wanted:
        if name in PACK_FORBIDDEN:
            print(f"✗ {name}: 재배포가 금지된 소스라 포장을 거부합니다 (로컬 전용)",
                  file=sys.stderr)
            failed += 1
            continue
        src = output_dir / name
        if not src.exists():
            print(f"− {name}: output/에 없습니다 (빌드 안 됨) — 건너뜀")
            continue
        dest = data_dir / f"{name}.gz"
        with gzip.open(dest, "wb", compresslevel=9) as handle:
            handle.write(src.read_bytes())
        print(f"✓ {name} → {dest} ({dest.stat().st_size:,} bytes)")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kbcommon", description="KB 사이의 정합성 검사")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="산출물끼리 이어지는지 확인한다")
    verify.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="산출물 디렉터리 (기본: output)")

    pack = sub.add_parser(
        "pack", help="output/ 산출물을 data/*.gz로 포장 (이름 생략 = 기존 전부 갱신)"
    )
    pack.add_argument("names", nargs="*", help="산출물 파일명 (예: svcmap-graph.json)")
    pack.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    # build-* 명령들은 envkb로 이사했다(재편 계획 ⑤) — 리전·탄소·지연·수명주기·
    # 드라이버 커버리지·이미지는 자체 산출물을 소유하는 지식베이스지 공용 배관이
    # 아니다. `python -m envkb build-<축>`을 쓰세요.

    args = parser.parse_args(argv)

    if args.command == "pack":
        from app.deployment.kbcommon.artifact import BUNDLED_DIR

        return pack_artifacts(args.names, args.output, BUNDLED_DIR)

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
