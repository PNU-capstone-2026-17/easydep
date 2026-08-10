"""사전등록 범위의 Native v2 독립 검토를 검증하고 동결 모델을 만든다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.cloudkb.depkb.native_v2 import freeze


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def freeze_files(
    inventory_path: Path, scope_path: Path, first_path: Path, second_path: Path,
    adjudications_path: Path, output_path: Path,
) -> dict[str, Any]:
    inventory = _read(inventory_path)
    scope = _read(scope_path)
    model = freeze(
        inventory, _read(first_path), _read(second_path), _read(adjudications_path),
        expected_native_ids=scope["selectedNativeIds"],
    )
    output_path.write_text(
        json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("scope", type=Path)
    parser.add_argument("review_a", type=Path)
    parser.add_argument("review_b", type=Path)
    parser.add_argument("adjudications", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    model = freeze_files(
        args.inventory, args.scope, args.review_a, args.review_b,
        args.adjudications, args.output,
    )
    print(json.dumps({
        "output": str(args.output), "reliability": model["reliability"],
        "freezeSha256": model["freeze"]["sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
