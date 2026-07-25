"""리전 간 네트워크 지연 — cb-tumblebug 벤치마크 실측.

`carbon.py`와 같은 자리다: **리전 속성은 kbcommon이 빌드하고 도구 계층이 서빙한다.**
탄소가 리전 하나의 속성이라면 지연은 **리전 쌍**의 속성이다.

## 무엇을 잰 값인가 — 이게 제일 중요하다

`assets/cloudlatencymap.csv`는 cb-tumblebug의 `benchmark.go`가 **직접 쓴다**. 즉
벤더가 발표한 SLA가 아니라 **실제로 VM을 띄워 잰 왕복 시간(RTT)**이다.

    RunLatencyBenchmark → mrttArray → cloudlatencymap.csv

**그래서 '보장'이 아니라 '어느 시점의 관측'이다.** 언제 쟀는지는 데이터에 없다 —
DB의 `measured_at`은 적재 시각이지 측정 시각이 아니다(같은 초에 10,890행이 다 찍혀
있다). 이 한계를 답변에 밝힌다.

## 검증한 것 넷

    대칭성      양방향 쌍 4,851 중 값이 다른 것 4,845 (99.9%)
    전치 채움   benchmark.go에 `Fill empty with transpose`가 있으나 **행 98개 중
                전부 대칭인 행은 0개** — 이 스냅샷에서는 거의 안 걸렸다
    거리 상관   대권거리 대비 r = 0.817, 거리대별 중앙값이 단조 증가
    물리 하한   왕복 광속(광섬유 200,000 km/s)을 어기는 쌍 16/10,791 (0.15%)

전치 채움을 **의심하고 세어 본 뒤** 기우였다고 결론 냈다. 세지 않았으면 "대칭이니
양방향 측정"이라고 잘못 말할 뻔했다.

## 물리 하한 위반은 지우지 않고 표시한다

16쌍은 좌표가 틀렸거나 값이 틀렸다. 어느 쪽인지 모르므로 **지우지 않고 불변식이
보고하게** 둔다 — 조용히 버리면 다음 사람이 같은 것을 다시 발견한다.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path

from kbcommon import artifact
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.invariants import Invariant, Violation
from kbcommon.sources import SOURCES

EVIDENCE = "tumblebug-latency"

#: 광섬유에서 빛은 대략 200,000 km/s. 왕복이므로 거리 × 2.
#: 여유 0.5 ms는 측정 자체의 잡음을 감안한 것이다.
_FIBER_KM_PER_MS = 200.0
_SLACK_MS = 0.5


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """두 좌표 사이 대권거리(km)."""
    radius = 6371.0
    lat1, lon1 = a
    lat2, lon2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def faster_than_light(distance_km: float, latency_ms: float) -> bool:
    """왕복 광속을 앞지르는가. 그러면 좌표나 값 중 하나가 틀렸다."""
    return latency_ms < distance_km * 2 / _FIBER_KM_PER_MS - _SLACK_MS


def _split(name: str) -> tuple[str, str]:
    """`aws-ap-northeast-2` → `(aws, ap-northeast-2)`."""
    provider, _, region = name.partition("-")
    return provider, region


def parse_matrix(text: str) -> tuple[list[dict], dict]:
    """CSV 행렬 → 레코드 목록.

    자기 자신(A→A)도 담는다 — 리전 **안**의 지연이라 뜻이 있다(중앙값 0.45 ms).
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], {"rows": 0, "cols": 0}
    header = rows[0][1:]
    records: list[dict] = []
    for row in rows[1:]:
        source = (row[0] or "").strip()
        if not source:
            continue
        for column, cell in zip(header, row[1:]):
            column = (column or "").strip()
            cell = (cell or "").strip()
            if not column or not cell:
                continue
            try:
                value = float(cell)
            except ValueError:
                continue
            if value <= 0:
                continue  # 0·음수는 측정값이 아니다
            src_provider, src_region = _split(source)
            dst_provider, dst_region = _split(column)
            if not src_region or not dst_region:
                continue
            records.append(
                {
                    "sourceProvider": src_provider,
                    "sourceRegion": src_region,
                    "targetProvider": dst_provider,
                    "targetRegion": dst_region,
                    "latencyMs": round(value, 3),
                }
            )
    stats = {
        "rows": len(rows) - 1,
        "cols": len([c for c in header if c.strip()]),
        "records": len(records),
    }
    return records, stats


def _coords(regions_path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    data = artifact.load_json(regions_path)
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for provider, info in (data.get("providers") or {}).items():
        for code, region in (info.get("regions") or {}).items():
            if region.get("latitude") is not None and region.get("longitude") is not None:
                out[(provider, code.lower())] = (region["latitude"], region["longitude"])
    return out


def annotate_distance(
    records: list[dict], coords: dict[tuple[str, str], tuple[float, float]]
) -> dict:
    """좌표를 아는 쌍에 거리를 붙이고 물리 하한 위반을 센다."""
    joined = violations = 0
    for record in records:
        src = (record["sourceProvider"], record["sourceRegion"].lower())
        dst = (record["targetProvider"], record["targetRegion"].lower())
        if src not in coords or dst not in coords:
            continue
        distance = haversine_km(coords[src], coords[dst])
        record["distanceKm"] = round(distance, 1)
        joined += 1
        if faster_than_light(distance, record["latencyMs"]):
            record["suspect"] = (
                "faster than round-trip light speed — either the coordinates or "
                "the value is wrong"
            )
            violations += 1
    return {"withDistance": joined, "fasterThanLight": violations}


def _schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["_note", "pairs"],
        "properties": {
            "_note": {"type": "string"},
            "_source": {"type": "array"},
            "_coverage": {"type": "array"},
            "pairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "sourceProvider",
                        "sourceRegion",
                        "targetProvider",
                        "targetRegion",
                        "latencyMs",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "sourceProvider": {"type": "string"},
                        "sourceRegion": {"type": "string"},
                        "targetProvider": {"type": "string"},
                        "targetRegion": {"type": "string"},
                        "latencyMs": {"type": "number", "exclusiveMinimum": 0},
                        "distanceKm": {"type": "number"},
                        "suspect": {"type": "string"},
                    },
                },
            },
        },
    }


def _invariants() -> list[Invariant]:
    def physics(dataset: dict) -> list[Violation]:
        return [
            Violation(
                where=(
                    f"{p['sourceProvider']}-{p['sourceRegion']}"
                    f"→{p['targetProvider']}-{p['targetRegion']}"
                ),
                detail=f"{p['latencyMs']} ms / {p.get('distanceKm')} km",
            )
            for p in dataset["pairs"]
            if p.get("suspect")
        ]

    return [
        Invariant(
            name="latency-beats-light",
            question=(
                "Is any pair's latency shorter than round-trip light speed? "
                "(a sign that either the coordinates or the value is wrong)"
            ),
            # **지우지 않고 보고한다** — 조용히 버리면 다음 사람이 같은 것을 다시 발견한다.
            severity="report",
            check=physics,
        )
    ]


NOTE = (
    "Network latency between regions (round-trip, ms). **A value cb-tumblebug "
    "measured by actually launching VMs**, not a vendor-guaranteed SLA — an "
    "observation at some point in time. **The source carries no measurement "
    "time** (the DB's measured_at is the load time). Checks: 99.9% of the 4,851 "
    "bidirectional pairs hold different values, so this is not a transpose copy, "
    "and it correlates with great-circle distance at 0.817, which is physically "
    "consistent. A few pairs do beat round-trip light speed and are marked "
    "`suspect` — for those, either the coordinates or the value is wrong."
)


def build(output: Path, *, refresh: bool = False, regions: Path | None = None) -> dict:
    source = SOURCES["tumblebug-latency"]
    path = fetch_cached(source.url, f"tumblebug-latency-{source.pin}.csv", refresh=refresh)
    records, stats = parse_matrix(path.read_text(encoding="utf-8"))

    regions_path = regions or (Path("output") / "cloud-regions.json")
    distance_stats = {"withDistance": 0, "fasterThanLight": 0}
    if artifact.resolve(str(regions_path.parent), regions_path.name) or regions_path.exists():
        found = artifact.resolve(str(regions_path.parent), regions_path.name)
        distance_stats = annotate_distance(records, _coords(found or regions_path))

    providers = sorted({r["sourceProvider"] for r in records})
    dataset = {
        "_note": NOTE,
        "pairs": records,
        "_coverage": [
            {
                "providers": providers,
                "pairs": len(records),
                "regions": len(
                    {(r["sourceProvider"], r["sourceRegion"]) for r in records}
                ),
                "note": (
                    f"{len(providers)} providers · {len(records):,} region pairs. "
                    f"{distance_stats['withDistance']:,} pairs carry a distance, "
                    f"and the {distance_stats['fasterThanLight']} of those that "
                    "break the physical lower bound are marked `suspect` (not "
                    "deleted). **Cross-provider pairs exist** — AWS↔Azure in the "
                    "same city, for instance."
                ),
            }
        ],
        "_source": [describe_source_set([path], source.key)],
    }
    result = artifact.write_dataset(output, dataset, _schema(), _invariants())
    print(
        f"region-latency: {len(records):,} pairs "
        f"({len(providers)} providers) → {output}"
    )
    summary = result.summary()
    if summary:
        print(summary)
    return dataset


# ---------------------------------------------------------------- 조회

_DEFAULT_ARTIFACT = "region-latency.json"


def _load(output_dir: str | Path = "output") -> dict | None:
    found = artifact.resolve(str(output_dir), _DEFAULT_ARTIFACT)
    path = found if found is not None else Path(output_dir) / _DEFAULT_ARTIFACT
    if not path.exists():
        return None
    try:
        return artifact.load_json(path)
    except Exception:
        return None


def describe(
    source: str, target: str | None = None, *, limit: int = 10, output_dir: str | Path = "output"
) -> str:
    """`aws-ap-northeast-2` 기준 지연. 대상을 주면 그 쌍만.

    **프로바이더를 넘나드는 쌍이 있다** — 같은 도시의 AWS↔Azure가 3.3 ms다.
    그게 이 데이터의 값어치이므로 필터로 막지 않는다.
    """
    data = _load(output_dir)
    if data is None:
        return (
            "No region latency data. Build it with "
            "`python -m kbcommon build-latency`."
        )
    wanted = source.strip().lower()
    rows = [
        p
        for p in data["pairs"]
        if f"{p['sourceProvider']}-{p['sourceRegion']}".lower() == wanted
    ]
    if not rows:
        known = sorted({f"{p['sourceProvider']}-{p['sourceRegion']}" for p in data["pairs"]})
        return (
            f"No measurement with '{source}' as the source. **That does not mean "
            f"none exists — this benchmark did not measure it.** A few of the "
            f"{len(known)} regions it did measure: "
            + ", ".join(known[:6])
        )
    if target:
        low = target.strip().lower()
        rows = [
            p for p in rows if f"{p['targetProvider']}-{p['targetRegion']}".lower() == low
        ]
        if not rows:
            return f"No measurement for '{source}' → '{target}'."

    rows.sort(key=lambda p: p["latencyMs"])
    lines = [
        f"Round-trip latency from {source} "
        f"(nearest first, {min(limit, len(rows))} of {len(rows)}):"
    ]
    for pair in rows[: max(1, limit)]:
        name = f"{pair['targetProvider']}-{pair['targetRegion']}"
        distance = f" · {pair['distanceKm']:,.0f} km" if pair.get("distanceKm") else ""
        suspect = "  ⚠ coordinates or value suspect" if pair.get("suspect") else ""
        lines.append(f"  {pair['latencyMs']:8.1f} ms  {name}{distance}{suspect}")
    lines.append(
        "\n※ **A value cb-tumblebug measured by actually launching VMs**, not a "
        "vendor-guaranteed SLA. The source carries no measurement time — it may "
        "differ from the value today."
    )
    return "\n".join(lines)
