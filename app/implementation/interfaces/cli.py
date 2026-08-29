"""제품 worker가 사용하는 구현 생성·계획·실행 명령을 제공한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..generation.orchestrator import PrototypeOrchestrator, load_job
from ..workflows.coordinator import plan_workflow, run_workflow


def main() -> int:
    """명령행 인자를 읽어 현재 제품 경로에 필요한 작업 하나를 실행한다."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) > 1 and sys.argv[1] in {"plan-workflow", "run-workflow"}:
        parser = argparse.ArgumentParser(
            description="Plan or run the resumable implementation workflow"
        )
        parser.add_argument("command", choices=("plan-workflow", "run-workflow"))
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        parser.add_argument("--approval", type=Path)
        parser.add_argument("--retry-failed", action="store_true")
        args = parser.parse_args()
        spec = load_job(args.job.resolve())
        if args.command == "plan-workflow":
            result = plan_workflow(args.run.resolve(), spec)
        else:
            result = run_workflow(
                args.run.resolve(),
                spec,
                args.approval.resolve() if args.approval else None,
                retry_failed=args.retry_failed,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0

    parser = argparse.ArgumentParser(description="EasyDep implementation generator")
    parser.add_argument("job", type=Path, help="Path to a prototype job JSON file")
    args = parser.parse_args()
    output = PrototypeOrchestrator(load_job(args.job)).run()
    manifest = json.loads(
        (output / "reports" / "run-manifest.json").read_text(encoding="utf-8")
    )
    print(
        json.dumps(
            {"status": manifest["status"], "output": str(output)},
            ensure_ascii=False,
        )
    )
    # 입력 보완이 필요한 상태도 생성기가 정상적으로 진단을 남긴 결과다.
    return 0 if manifest["status"] in {"SUCCEEDED", "NEEDS_INPUT"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
