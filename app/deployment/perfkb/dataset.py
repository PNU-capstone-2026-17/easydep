"""성능 데이터셋 로드 및 조회.

**costkb와 달리 번들 폴백이 없다.** costkb는 손 큐레이션 36건이 있어 빌드 없이도 동작하지만,
성능 데이터는 손으로 옮겨 적을 만한 게 아니다(스펙당 10여 개 필드 × 3만 건). 산출물이 없으면
도구가 빌드를 안내한다 — graphkb/capacitykb와 같은 방식이다.

산출물이 없어도 **추천은 그대로 동작한다**(fail-open) — 다만 그 사실을 숨기지는 않는다.
경고를 못 붙였으면 못 붙였다고 밝힌다. 자세한 건 `nim_agent/cost_tools.py`의 조인 부분 참고.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

from kbcommon.artifact import read_dataset

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
def _load_result(output_dir: str) -> tuple[dict | None, str | None]:
    """(데이터, 오류). 깨진 산출물에 **예외를 던지지 않는다**.

    perfkb는 번들 폴백이 없어서 예외가 그대로 올라갔고, 그러면 성능 도구가 아니라
    이 데이터를 조인하는 **비용 추천 전체가 죽었다**(결함 (마)). 성능 경고는 부가
    정보이므로, 없으면 없는 대로 추천은 살아야 한다.
    """
    return read_dataset(Path(output_dir) / BUILT_FILENAME, _schema())


def _load_cached(output_dir: str) -> dict | None:
    return _load_result(output_dir)[0]


def schema() -> dict:
    """번들된 JSON Schema — 빌드가 쓰기 전 검증에 쓴다."""
    return _schema()


def clear_caches() -> None:
    """로드·인덱스 캐시를 전부 비운다 (테스트가 output_dir을 갈아끼울 때).

    캐시가 셋이라 호출부에서 하나씩 비우면 반드시 빠뜨린다 — 실제로 인덱스를
    추가했을 때 기존 픽스처들이 조용히 낡은 데이터를 봤다. 추가는 여기만 고친다.
    """
    _load_result.cache_clear()
    _by_id.cache_clear()
    _by_provider_name.cache_clear()
    _schema.cache_clear()


def load_warning(output_dir: Path | str | None = None) -> str | None:
    """산출물이 깨져 읽지 못했다면 그 설명. 정상이거나 미빌드면 None."""
    return _load_result(_resolve(output_dir))[1]


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


@lru_cache(maxsize=4)
def _by_provider_name(output_dir: str) -> dict[tuple[str, str], dict]:
    """(provider, specName소문자) → 레코드. 리전마다 레코드가 있어 first-wins.

    경고에 쓰는 신호(`sustainedCpu`, `currentGeneration`)는 리전 불변이라 어느 리전을
    골라도 같다. 반면 `ebsBaselineIops` 등 일부 수치는 리전마다 다를 수 있으므로
    (`aws c8gn.48xlarge`가 me-central-1만 60000) 이 인덱스를 수치 조회에 쓰면 안 된다.
    """
    data = _load_cached(output_dir)
    if data is None:
        return {}
    index: dict[tuple[str, str], dict] = {}
    for rec in data["specs"]:
        index.setdefault((rec["provider"], rec["specName"].lower()), rec)
    return index


def get_by_spec_name(
    provider: str, spec_name: str, output_dir: Path | str | None = None
) -> dict | None:
    """(provider, specName)으로 성능 레코드를 찾는다 — **id 조인의 폴백**.

    costkb 번들 36건에는 `id`가 없고, 미러 레코드도 그 리전이 perfkb에 없을 수 있다.
    id로만 조인하면 두 경우 모두 조용히 경고가 사라진다(결함 C3).
    """
    if not provider or not spec_name:
        return None
    return _by_provider_name(_resolve(output_dir)).get(
        (provider.lower(), spec_name.lower())
    )


def tracked_providers(output_dir: Path | str | None = None) -> frozenset[str]:
    """성능 신호를 **실제로 수록한** 프로바이더 집합.

    상수로 박지 않고 산출물에서 유도한다 — `parsers/build.py`의 KNOWN_PROVIDERS를
    복제해두면 커버리지가 넓어질 때 조용히 드리프트한다. "레코드가 없다"와
    "이 프로바이더는 애초에 추적 대상이 아니다"를 가르는 데 쓴다(결함 C4).

    별도 캐시를 두지 않는다 — `_by_provider_name`이 이미 캐시돼 있어 키만 훑으면 된다.
    """
    return frozenset(provider for provider, _ in _by_provider_name(_resolve(output_dir)))


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
