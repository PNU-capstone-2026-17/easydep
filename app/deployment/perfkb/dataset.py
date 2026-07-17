"""성능 데이터셋 로드 및 조회.

**costkb와 달리 번들 폴백이 없다.** costkb는 손 큐레이션 36건이 있어 빌드 없이도 동작하지만,
성능 데이터는 손으로 옮겨 적을 만한 게 아니다(스펙당 10여 개 필드 × 3만 건). 산출물이 없으면
도구가 빌드를 안내한다 — graphkb/capacitykb와 같은 방식이다.

산출물이 없어도 **경고가 없을 뿐 추천은 그대로 동작한다**(fail-open). 자세한 건
`nim_agent/cost_tools.py`의 조인 부분 참고.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

_SCHEMA_PATH = Path(__file__).with_name("schema.json")

DEFAULT_OUTPUT_DIR = Path("output")
BUILT_FILENAME = "tumblebug-perf.json"


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve(output_dir: Path | str | None) -> str:
    """None이면 그때그때 DEFAULT_OUTPUT_DIR을 읽는다(기본 인자로 박으면 테스트에서 못 바꾼다)."""
    return str(DEFAULT_OUTPUT_DIR if output_dir is None else output_dir)


@lru_cache(maxsize=4)
def _load_cached(output_dir: str) -> dict | None:
    built = Path(output_dir) / BUILT_FILENAME
    if not built.exists():
        return None
    data = json.loads(built.read_text(encoding="utf-8"))
    jsonschema.validate(data, _schema())
    return data


def load_perf(output_dir: Path | str | None = None) -> list[dict] | None:
    """성능 레코드 목록. 산출물이 없으면 None."""
    data = _load_cached(_resolve(output_dir))
    return None if data is None else data["specs"]


def dataset_note(output_dir: Path | str | None = None) -> str | None:
    data = _load_cached(_resolve(output_dir))
    return None if data is None else data["_note"]


def is_built(output_dir: Path | str | None = None) -> bool:
    return _load_cached(_resolve(output_dir)) is not None


@lru_cache(maxsize=4)
def _by_id(output_dir: str) -> dict[str, dict]:
    data = _load_cached(output_dir)
    if data is None:
        return {}
    return {rec["id"]: rec for rec in data["specs"]}


def get_by_id(spec_id: str, output_dir: Path | str | None = None) -> dict | None:
    """costkb 레코드의 `id`로 성능 레코드를 찾는다. 조인의 진입점."""
    return _by_id(_resolve(output_dir)).get(spec_id)


def find(
    provider: str | None = None,
    spec_name: str | None = None,
    output_dir: Path | str | None = None,
) -> list[dict]:
    """프로바이더/스펙명으로 찾는다(리전마다 레코드가 따로 있어 여러 건일 수 있다)."""
    records = load_perf(output_dir) or []
    prov = provider.lower() if provider else None
    name = spec_name.lower() if spec_name else None
    return [
        r
        for r in records
        if (prov is None or r["provider"] == prov)
        and (name is None or r["specName"].lower() == name)
    ]


def coverage(output_dir: Path | str | None = None) -> list[dict]:
    """프로바이더별 레코드 수와 주요 신호 채움율 — '모른다'를 드러내는 용도."""
    records = load_perf(output_dir) or []
    by_provider: dict[str, dict] = {}
    for r in records:
        row = by_provider.setdefault(
            r["provider"],
            {"provider": r["provider"], "count": 0, "sustainedCpu": 0, "acu": 0,
             "currentGeneration": 0, "not_sustained": 0},
        )
        row["count"] += 1
        if "sustainedCpu" in r:
            row["sustainedCpu"] += 1
            if r["sustainedCpu"]["value"] is False:
                row["not_sustained"] += 1
        if "acu" in r:
            row["acu"] += 1
        if "currentGeneration" in r:
            row["currentGeneration"] += 1
    return sorted(by_provider.values(), key=lambda x: -x["count"])
