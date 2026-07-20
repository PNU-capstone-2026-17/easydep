"""스펙/가격 데이터셋 로드 및 필터 유틸.

**두 가지 소스**:
1. `output/tumblebug-cost.json` — `costkb build`가 cb-tumblebug 덤프에서 만든 미러(73k행).
   있으면 이쪽을 쓴다.
2. `specs.json` — 번들된 손 큐레이션 36건. 빌드 안 해도 서버·크레덴셜 없이 즉시 동작한다.

**왜 미러인가**: 우리 에이전트의 런타임 경로는 cb-tumblebug MCP의 `recommend_vm_spec`이고,
그 도구는 `spec_infos` 테이블을 읽어 컬럼을 그대로 투영한다. 같은 테이블에서 빌드하면
오프라인 기준선과 라이브 경로가 **같은 세계**를 본다. AWS/Azure 공개 API에서 직접 빌드하면
오히려 불일치가 생긴다 (자세한 건 `parsers/tumblebug.py` 참고).

`specs.json`은 손으로 편집하는 파일이라 오타(예: `memGib`)가 들어가기 쉽다. 예전에는
검증이 없어서 그런 오타가 로드 시점이 아니라 `filter_specs` 안에서 `KeyError`로 터졌다.
그래서 로드할 때 번들 스키마로 1회 검증한다 — `@lru_cache` 덕에 비용은 프로세스당 한 번이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import jsonschema

from kbcommon.artifact import REBUILD_HINT, read_dataset

_SPECS_PATH = Path(__file__).with_name("specs.json")
_SCHEMA_PATH = Path(__file__).with_name("schema.json")

DEFAULT_OUTPUT_DIR = Path("output")
BUILT_FILENAME = "tumblebug-cost.json"

# MCP의 recommend_vm_spec은 호출자가 architecture를 안 주면 x86_64를 끼워넣는다
# (tb-mcp.py). 이걸 미러하지 않으면 MCP가 감추는 arm64 스펙(덤프 기준 7,790건)이
# 우리에게만 보여 결과가 갈린다.
DEFAULT_ARCHITECTURE = "x86_64"

_SORT_KEYS = {
    # 가격 미상(None)은 맨 뒤로 — Tumblebug의 `CASE WHEN cost_per_hour > 0 ... ELSE 999999`와 같은 취지.
    "cost": lambda s: (s["hourlyUSD"] is None, s["hourlyUSD"] or 0),
    "vcpu": lambda s: -s["vCPU"],
    "memory": lambda s: -s["memGiB"],
}


@lru_cache(maxsize=1)
def _schema() -> dict:
    """번들된 JSON Schema를 로드한다."""
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_validated(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(data, _schema())
    return data


@dataclass(frozen=True)
class _Loaded:
    data: dict
    warning: str | None = None


def _resolve(output_dir: Path | str | None) -> str:
    """None이면 그때그때 DEFAULT_OUTPUT_DIR을 읽는다.

    기본 인자로 박아두면 def 시점에 바인딩돼 테스트에서 갈아끼울 수 없다.
    """
    return str(DEFAULT_OUTPUT_DIR if output_dir is None else output_dir)


@lru_cache(maxsize=4)
def _load_cached(output_dir: str) -> _Loaded:
    """빌드 산출물이 있으면 그것을, 없거나 **깨졌으면** 번들을 로드한다.

    깨진 산출물에 예외를 던지면 번들 폴백 경로가 통째로 사라진다 — 빌드 한 번
    실패하면 `output/`를 손으로 지우기 전까지 KB가 영구 정지했다(결함 C2).
    폴백하되 조용히 하지는 않는다: 사용자는 자기가 지금 36건짜리 번들을 보고
    있다는 걸 알아야 한다.
    """
    data, error = read_dataset(Path(output_dir) / BUILT_FILENAME, _schema())
    if data is not None:
        return _Loaded(data)
    bundle = _load_validated(_SPECS_PATH)  # 번들이 깨졌으면 그건 진짜 버그다
    if error is None:
        return _Loaded(bundle)
    return _Loaded(
        bundle,
        f"빌드 산출물을 쓸 수 없어 번들 {len(bundle['specs'])}건으로 답합니다 "
        f"({error}). {REBUILD_HINT}: python -m costkb build",
    )


def schema() -> dict:
    """번들된 JSON Schema — 빌드가 쓰기 전 검증에 쓴다."""
    return _schema()


def clear_caches() -> None:
    """로드·스키마 캐시를 비운다 (테스트가 output_dir을 갈아끼울 때)."""
    _load_cached.cache_clear()
    _schema.cache_clear()


def load_dataset(output_dir: Path | str | None = None) -> dict:
    """데이터셋 전체를 로드하고 스키마로 검증한다(캐시됨).

    산출물이 깨져 있으면 번들로 폴백한다 — 그 사실은 `load_warning()`으로 알 수 있다.
    """
    return _load_cached(_resolve(output_dir)).data


def load_warning(output_dir: Path | str | None = None) -> str | None:
    """산출물이 깨져 번들로 폴백했다면 그 설명. 정상이면 None.

    응답에 붙이는 용도 — 조용한 폴백은 "커버리지가 왜 갑자기 좁아졌지?"를 미궁으로 만든다.
    """
    return _load_cached(_resolve(output_dir)).warning


def load_specs(output_dir: Path | str | None = None) -> list[dict]:
    """검증된 스펙 목록을 반환한다."""
    return load_dataset(output_dir)["specs"]


def dataset_note(output_dir: Path | str | None = None) -> str:
    """데이터셋의 출처·한계 고지 (레코드별 evidence를 대신한다)."""
    return load_dataset(output_dir)["_note"]


def is_built(output_dir: Path | str | None = None) -> bool:
    """빌드 산출물을 **실제로 쓰고 있는지** (아니면 번들 36건 폴백).

    파일 존재만 보면 안 된다 — 깨진 파일이 있을 때 "빌드됨"이라고 답하면
    커버리지 안내가 73k건 기준으로 나가는데 정작 답은 번들 36건에서 나온다.
    """
    return _load_cached(_resolve(output_dir)).warning is None and (
        Path(_resolve(output_dir)) / BUILT_FILENAME
    ).exists()


def filter_specs(
    vcpu_min: int = 0,
    mem_min_gib: float = 0,
    provider: str | None = None,
    region: str | None = None,
    sort_by: str = "cost",
    limit: int = 5,
    *,
    architecture: str | None = DEFAULT_ARCHITECTURE,
    priced_only: bool = True,
    output_dir: Path | str | None = None,
) ->list[dict]:
    """요구사항 조건으로 스펙을 필터링·정렬해 상위 결과를 반환한다.

    `memGiB`(미러값)로 필터링한다 — 표시용 `memGiBActual`이 아니라. 라이브 MCP가
    같은 컬럼으로 필터링하므로, 여기서 보정값을 쓰면 두 경로의 답이 갈린다.

    Args:
        vcpu_min: 최소 vCPU. 0이면 바운드 없음(Tumblebug도 0값 범위는 무시한다).
        mem_min_gib: 최소 메모리(GiB, 미러 기준).
        provider: 'aws' | 'gcp' | 'azure' 등 (대소문자 무시). None이면 전체.
        region: 리전 필터(부분 일치, 대소문자 무시). None이면 전체.
        sort_by: 'cost'(저렴한 순) | 'vcpu'(큰 순) | 'memory'(큰 순).
            알 수 없는 값이면 'cost'로 폴백한다.
        limit: 반환 개수(최소 1).
        architecture: 기본 'x86_64' — MCP가 주입하는 것과 동일. None이면 전체.
        priced_only: True(기본)면 가격이 있는 후보만. 비용 정렬·판정이 성립해야 하므로.
        output_dir: 빌드 산출물 위치.
    """
    prov = provider.lower() if provider else None
    reg = region.lower() if region else None
    arch = architecture.lower() if architecture else None

    matched = [
        s
        for s in load_specs(output_dir)
        if s["vCPU"] >= vcpu_min
        and s["memGiB"] >= mem_min_gib
        and (prov is None or s["provider"] == prov)
        and (reg is None or reg in s["region"].lower())
        # architecture를 모르는 레코드(번들 36건)는 거르지 않는다 — 정보 부재가
        # 배제 사유가 되면 빌드 전 폴백이 통째로 사라진다.
        and (arch is None or not s.get("architecture") or s["architecture"].lower() == arch)
        and (not priced_only or s["hourlyUSD"] is not None)
    ]

    key = _SORT_KEYS.get(sort_by, _SORT_KEYS["cost"])
    matched.sort(key=key)
    return matched[: max(1, limit)]


def count_unpriced(
    vcpu_min: int = 0,
    mem_min_gib: float = 0,
    provider: str | None = None,
    region: str | None = None,
    *,
    architecture: str | None = DEFAULT_ARCHITECTURE,
    output_dir: Path | str | None = None,
) ->int:
    """조건에 맞지만 가격이 없는 후보 수 — "라이브 가격은 MCP로" 안내에 쓴다."""
    return len(
        filter_specs(
            vcpu_min,
            mem_min_gib,
            provider,
            region,
            limit=10**9,
            architecture=architecture,
            priced_only=False,
            output_dir=output_dir,
        )
    ) - len(
        filter_specs(
            vcpu_min,
            mem_min_gib,
            provider,
            region,
            limit=10**9,
            architecture=architecture,
            priced_only=True,
            output_dir=output_dir,
        )
    )


def coverage(output_dir: Path | str | None = None) -> list[dict]:
    """데이터셋 커버리지 요약 — (provider, region)별 개수와 vCPU·메모리 범위.

    "조건을 만족하는 스펙이 없습니다"가 나올 때 경계를 확인하는 용도.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for spec in load_specs(output_dir):
        groups.setdefault((spec["provider"], spec["region"]), []).append(spec)
    return [
        {
            "provider": provider,
            "region": region,
            "count": len(rows),
            "priced": sum(1 for r in rows if r["hourlyUSD"] is not None),
            "vcpu_min": min(r["vCPU"] for r in rows),
            "vcpu_max": max(r["vCPU"] for r in rows),
            "mem_min_gib": min(r["memGiB"] for r in rows),
            "mem_max_gib": max(r["memGiB"] for r in rows),
        }
        for (provider, region), rows in sorted(groups.items())
    ]


def provider_summary(output_dir: Path | str | None = None) -> list[dict]:
    """프로바이더별 요약 — 리전이 수십 개로 늘어난 뒤 coverage()가 너무 길어져서."""
    groups: dict[str, list[dict]] = {}
    for spec in load_specs(output_dir):
        groups.setdefault(spec["provider"], []).append(spec)
    return [
        {
            "provider": provider,
            "count": len(rows),
            "priced": sum(1 for r in rows if r["hourlyUSD"] is not None),
            "regions": len({r["region"] for r in rows}),
            "vcpu_max": max(r["vCPU"] for r in rows),
            "mem_max_gib": max(r["memGiB"] for r in rows),
        }
        for provider, rows in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    ]
