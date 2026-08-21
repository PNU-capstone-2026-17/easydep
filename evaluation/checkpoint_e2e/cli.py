from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .catalog import CASES, CHECKPOINTS, RUN_ROOT
from .graph import (
    generate_candidate,
    promote_candidate,
    run_all,
    run_one,
    seed_candidate_prefix,
    validate_candidate,
)


def _print(value) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one EasyDep checkpoint from a gold snapshot.")
    sub = parser.add_subparsers(dest="command", required=True)

    one = sub.add_parser("run")
    one.add_argument("--case", choices=sorted(CASES), default="e1-aws")
    one.add_argument("--from", dest="source", choices=CHECKPOINTS[:-1], required=True)
    one.add_argument("--output-root", type=Path, default=RUN_ROOT)

    all_parser = sub.add_parser("run-all")
    all_parser.add_argument("--case", choices=sorted(CASES), default="e1-aws")
    all_parser.add_argument("--output-root", type=Path, default=RUN_ROOT)
    all_parser.add_argument("--run-id")
    all_parser.add_argument("--resume", action="store_true")

    candidate = sub.add_parser("gold-candidate")
    candidate.add_argument("--case", choices=sorted(CASES), default="e1-aws")
    candidate.add_argument("--output", type=Path)
    candidate.add_argument("--resume", action="store_true")

    seed = sub.add_parser("gold-seed")
    seed.add_argument("source", type=Path)
    seed.add_argument("--case", choices=sorted(CASES), default="e1-aws")
    seed.add_argument("--output", type=Path, required=True)
    seed.add_argument("--through", choices=CHECKPOINTS[:-1], default="erd")

    validate = sub.add_parser("gold-validate")
    validate.add_argument("path", type=Path)

    promote = sub.add_parser("gold-promote")
    promote.add_argument("path", type=Path)
    promote.add_argument("--case", choices=sorted(CASES), default="e1-aws")

    args = parser.parse_args()
    if args.command == "run":
        _print(run_one(args.case, args.source, output_root=args.output_root))
    elif args.command == "run-all":
        _print(
            run_all(
                args.case,
                output_root=args.output_root,
                run_id=args.run_id,
                resume=args.resume,
            )
        )
    elif args.command == "gold-candidate":
        destination = args.output or (
            RUN_ROOT
            / "gold-candidates"
            / f"{args.case}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        _print(
            {
                "path": str(destination),
                **generate_candidate(args.case, destination, resume=args.resume),
            }
        )
    elif args.command == "gold-seed":
        _print(
            {
                "path": str(args.output),
                **seed_candidate_prefix(
                    args.case, args.source, args.output, through=args.through
                ),
            }
        )
    elif args.command == "gold-validate":
        _print(validate_candidate(args.path))
    else:
        _print({"path": str(promote_candidate(args.path, args.case))})


if __name__ == "__main__":
    main()
