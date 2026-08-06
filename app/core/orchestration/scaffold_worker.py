"""Isolated entry point for the member-owned scaffold generator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.implementation.config import ImplementationSettings
from app.implementation.prototype_client import PrototypeClient


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    if len(arguments) != 1:
        raise SystemExit("usage: scaffold_worker <job.json>")
    client = PrototypeClient(ImplementationSettings.from_env())
    run_root, workflow = client.generate_and_plan(Path(arguments[0]).resolve())
    print(
        json.dumps(
            {"run_root": str(run_root), "member_plan": workflow},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
