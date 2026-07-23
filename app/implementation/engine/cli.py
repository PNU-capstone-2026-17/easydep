from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .orchestrator import (
    PrototypeOrchestrator,
    load_job,
    plan_api_adapter_tasks,
    plan_boundary_adapter_tasks,
    plan_e2e_tasks,
    plan_gateway_adapter_tasks,
    plan_persistence_tasks,
    plan_wiring_tasks,
)
from .agent_runtime import (
    execute_openhands_task,
    validate_openhands_adapter,
    verify_run_workspace,
)
from .deployment_renderer import render_deployment
from .completion_audit import audit_run_completion
from .workflow import plan_workflow, run_workflow, workflow_status


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "plan-e2e":
        parser = argparse.ArgumentParser(description="Plan the E2E flow or report design gaps")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        args = parser.parse_args()
        run_root = args.run.resolve()
        tasks = plan_e2e_tasks(load_job(args.job.resolve()), run_root)
        gap_path = run_root / "reports/design-gaps/end-to-end-flow.json"
        gap = json.loads(gap_path.read_text(encoding="utf-8"))
        print(json.dumps({"status": gap["status"], "tasks": [task["task_id"] for task in tasks], "gapReport": str(gap_path)}, ensure_ascii=False))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "plan-gateway-adapters":
        parser = argparse.ArgumentParser(description="Plan outbound BCE Gateway adapters")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        args = parser.parse_args()
        tasks = plan_gateway_adapter_tasks(
            load_job(args.job.resolve()), args.run.resolve()
        )
        print(json.dumps({"tasks": [task["task_id"] for task in tasks]}, ensure_ascii=False))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "plan-deployment":
        parser = argparse.ArgumentParser(description="Plan Kubernetes deployment files")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        args = parser.parse_args()
        report = render_deployment(args.run.resolve(), load_job(args.job.resolve()))
        print(json.dumps({"renderer": "deterministic", "files": report["renderedFiles"]}, ensure_ascii=False))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "plan-wiring":
        parser = argparse.ArgumentParser(description="Plan Spring application wiring task")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        args = parser.parse_args()
        tasks = plan_wiring_tasks(
            load_job(args.job.resolve()), args.run.resolve()
        )
        print(json.dumps({"tasks": [task["task_id"] for task in tasks]}, ensure_ascii=False))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] in {
        "plan-workflow",
        "run-workflow",
        "resume-workflow",
        "workflow-status",
    }:
        parser = argparse.ArgumentParser(description="Plan, run, or inspect the resumable implementation workflow")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path, nargs="?")
        parser.add_argument("--approval", type=Path)
        parser.add_argument("--retry-failed", action="store_true")
        args = parser.parse_args()
        if args.command == "workflow-status":
            result = workflow_status(args.run.resolve())
        else:
            if args.job is None:
                parser.error(f"{args.command} requires a job JSON path")
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
    if len(sys.argv) > 1 and sys.argv[1] == "plan-boundary-adapters":
        parser = argparse.ArgumentParser(description="Plan BCE Boundary adapter tasks")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        args = parser.parse_args()
        tasks = plan_boundary_adapter_tasks(
            load_job(args.job.resolve()), args.run.resolve()
        )
        print(json.dumps({"tasks": [task["task_id"] for task in tasks]}, ensure_ascii=False))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "plan-api-adapters":
        parser = argparse.ArgumentParser(description="Plan OpenAPI web adapter tasks")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        args = parser.parse_args()
        tasks = plan_api_adapter_tasks(
            load_job(args.job.resolve()), args.run.resolve()
        )
        print(json.dumps({"tasks": [task["task_id"] for task in tasks]}, ensure_ascii=False))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "plan-persistence":
        parser = argparse.ArgumentParser(description="Plan ERD persistence implementation tasks")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("job", type=Path)
        args = parser.parse_args()
        tasks = plan_persistence_tasks(
            load_job(args.job.resolve()), args.run.resolve()
        )
        print(json.dumps({"tasks": [task["task_id"] for task in tasks]}, ensure_ascii=False))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] in {
        "run-agent",
        "validate-agent",
        "verify-run",
        "audit-run",
    }:
        parser = argparse.ArgumentParser(description="Run or validate one planned OpenHands implementation task")
        parser.add_argument("command")
        parser.add_argument("run", type=Path)
        parser.add_argument("task_id", nargs="?")
        args = parser.parse_args()
        if args.command == "run-agent":
            if not args.task_id:
                parser.error("run-agent requires task_id")
            result = execute_openhands_task(args.run.resolve(), args.task_id)
        elif args.command == "validate-agent":
            if not args.task_id:
                parser.error("validate-agent requires task_id")
            result = validate_openhands_adapter(args.run.resolve(), args.task_id)
        elif args.command == "verify-run":
            result = verify_run_workspace(args.run.resolve())
        else:
            result = audit_run_completion(args.run.resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0
    parser = argparse.ArgumentParser(description="EasyDep implementation-agent prototype")
    parser.add_argument("job", type=Path, help="Path to a prototype job JSON file")
    args = parser.parse_args()

    output = PrototypeOrchestrator(load_job(args.job)).run()
    manifest = json.loads((output / "reports" / "run-manifest.json").read_text(encoding="utf-8"))
    print(json.dumps({"status": manifest["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if manifest["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
