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


def reserved_ips(provider: str, output_dir: Path | str | None = None) -> Rule | None:
    """이 프로바이더의 서브넷 예약 IP 수. **모르면 None** — 0이 아니다."""
    from sizingkb.model import RESERVED_IPS

    found = rules_of(RESERVED_IPS, provider, output_dir)
    return found[0] if found else None


def scopes(kind: str, output_dir: Path | str | None = None) -> tuple[str, ...]:
    return tuple(sorted({r.scope for r in rules_of(kind, output_dir=output_dir)}))
