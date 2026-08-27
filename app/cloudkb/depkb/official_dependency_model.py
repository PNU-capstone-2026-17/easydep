"""공식 공급자 근거에서 생성한 런타임 의존관계 뷰를 제공한다."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("official-dependencies.json")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=1)
def load_official_dependencies() -> dict[str, Any]:
    document = json.loads(PATH.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != "easydep-official-dependencies/v1":
        raise ValueError("unsupported official dependency model")
    unsigned = {key: value for key, value in document.items() if key != "freeze"}
    if (document.get("freeze") or {}).get("sha256") != _digest(unsigned):
        raise ValueError("official dependency model freeze digest mismatch")
    return document


def dependencies_for(provider: str, anchors: list[str]) -> tuple[dict[str, Any], ...]:
    rows = load_official_dependencies()["providers"].get(provider)
    if rows is None:
        raise ValueError(f"unsupported official dependency provider: {provider}")
    selected = {"vm"}
    if "loadBalancer" in anchors:
        selected.update({"load-balancer", "backend-group", "backend-service"})
    if "disk" in anchors:
        selected.add("disk")
    result = []
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row["from"] in selected and row not in result:
                result.append(row)
                if row["to"] not in selected:
                    selected.add(row["to"])
                    changed = True
    return tuple(result)
