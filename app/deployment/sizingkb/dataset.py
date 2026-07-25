"""사이징 규칙 로드·조회."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from kbcommon import artifact
from sizingkb.model import Rule

_SCHEMA_PATH = Path(__file__).with_name("schema.json")

DEFAULT_OUTPUT_DIR = Path("output")

SIZING_FILES = (
    "tumblebug-sizing.json",
    "container-presets.json",
    "reviewed-sizing.json",
)


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve(output_dir: Path | str | None) -> str:
    return str(DEFAULT_OUTPUT_DIR if output_dir is None else output_dir)


@lru_cache(maxsize=4)
def _load(output_dir: str) -> tuple[tuple[Rule, ...], tuple[str, ...]]:
    rules: list[Rule] = []
    warnings: list[str] = []
    for name in SIZING_FILES:
        found = artifact.resolve(output_dir, name)
        path = found if found is not None else Path(output_dir) / name
        if not path.exists():
            continue
        data, error = artifact.read_dataset(path, schema())
        if error:
            warnings.append(error)
            continue
        rules.extend(Rule.from_dict(r) for r in data.get("rules") or [])
    return tuple(rules), tuple(warnings)


def clear_caches() -> None:
    _load.cache_clear()
    schema.cache_clear()


def is_built(output_dir: Path | str | None = None) -> bool:
    return bool(_load(_resolve(output_dir))[0])


def load_warnings(output_dir: Path | str | None = None) -> tuple[str, ...]:
    return _load(_resolve(output_dir))[1]


def all_rules(output_dir: Path | str | None = None) -> tuple[Rule, ...]:
    return _load(_resolve(output_dir))[0]


def rules_of(
    kind: str | None = None,
    scope: str | None = None,
    output_dir: Path | str | None = None,
) -> tuple[Rule, ...]:
    low = scope.strip().lower() if scope else None
    return tuple(
        r
        for r in all_rules(output_dir)
        if (kind is None or r.kind == kind) and (low is None or r.scope.lower() == low)
    )


def search_rules(text: str, output_dir: Path | str | None = None) -> tuple[Rule, ...]:
    """규칙을 **내용으로** 찾는다 — id·지표·설명까지 본다.

    scope로만 찾으면 닿지 않는 것이 있다. 클러스터의 서브넷 개수 규칙은 scope가
    `'aws'`인데(프로바이더별로 값이 다르므로 옳은 분류다), 사람은 '쿠버네티스'나
    '클러스터'로 묻는다. 그때 "이 데이터셋에 없습니다"가 나가면 **있는 것을 없다고
    말하는 것**이다 — bundlekb의 접두어 결함과 같은 계보(2026-07-25).

    scope 정확 일치를 **대체하지 않고 보완한다.** `'aws'`는 여전히 프로바이더를
    뜻해야 한다.
    """
    low = text.strip().lower()
    if not low:
        return ()
    return tuple(
        r
        for r in all_rules(output_dir)
        if low in " ".join(
            str(x) for x in (r.id, r.metric, r.note or "", r.scope)
        ).lower()
    )


def reserved_ips(provider: str, output_dir: Path | str | None = None) -> Rule | None:
    """이 프로바이더의 서브넷 예약 IP 수. **모르면 None** — 0이 아니다."""
    from sizingkb.model import RESERVED_IPS

    found = rules_of(RESERVED_IPS, provider, output_dir)
    return found[0] if found else None


def scopes(kind: str, output_dir: Path | str | None = None) -> tuple[str, ...]:
    return tuple(sorted({r.scope for r in rules_of(kind, output_dir=output_dir)}))
