"""Run one provider-native ResourcePlan -> diagram -> Terraform development smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.implementation.delivery.vm_delivery import VmDeliveryAdapter

REGIONS = {
    "aws": "ap-northeast-2",
    "azure": "koreacentral",
    "gcp": "asia-northeast3",
}


def _requirements(provider: str) -> dict[str, Any]:
    return {
        "resource_spec": {"provider": provider, "region": REGIONS[provider]},
        "deployment_needs": {
            "durable_shared_state": {
                "required": True,
                "decision": "accepted",
                "requirementIds": ["NFR-STATE"],
                "metadata": {
                    "applicationState": {
                        "durability": "persistent",
                        "accessScope": "shared-service",
                    }
                },
            },
            "private_state_path": {
                "required": True,
                "decision": "accepted",
                "requirementIds": ["NFR-PRIVATE"],
            },
            "runtime_configuration": {
                "required": True,
                "decision": "accepted",
                "requirementIds": ["NFR-CONFIG"],
            },
        },
    }


def _design() -> dict[str, Any]:
    return {
        "deployment_diagram_model": {
            "Nodes": [
                {"name": "Caller", "kind": "device"},
                {"name": "API Runtime", "kind": "executionEnvironment"},
                {"name": "State Store", "kind": "database"},
            ],
            "Connections": [
                {"source": "Caller", "target": "API Runtime", "protocol": "HTTP"},
                {"source": "API Runtime", "target": "State Store", "protocol": "TCP"},
            ],
        }
    }


def _application_contract() -> dict[str, Any]:
    return {
        "schemaVersion": "ApplicationRuntimeContract/v1",
        "facts": [
            {
                "id": "sample.http",
                "kind": "runtime.port",
                "attributes": {"name": "http", "port": 8080, "protocol": "http"},
                "sourceRefs": ["evaluation/dependency_audit/sample_app/service.py"],
            },
            {
                "id": "sample.health",
                "kind": "runtime.health",
                "attributes": {"path": "/health/ready"},
                "sourceRefs": ["evaluation/dependency_audit/sample_app/service.py"],
            },
            {
                "id": "sample.database-url",
                "kind": "runtime.environment",
                "attributes": {"name": "DATABASE_URL", "required": True},
                "sourceRefs": ["evaluation/dependency_audit/sample_app/service.py"],
            },
        ],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(provider: str, output_root: Path) -> Path:
    run_id = f"resource-plan-iac-{provider}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_root = output_root / run_id
    application = run_root / "application"
    shutil.copytree(Path(__file__).with_name("sample_app"), application)

    requirements = _requirements(provider)
    cloud_design = CloudDesignAdapter().finalize(
        requirements_result=requirements,
        design_result=_design(),
    )
    adapter = VmDeliveryAdapter()
    try:
        delivery = adapter.generate(
            requirements_result=requirements,
            cloud_design_result=cloud_design,
            implementation_result={"run_root": str(run_root)},
            application_runtime_contract=_application_contract(),
        )
    except Exception as error:
        result_path = run_root / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "easydep-resource-plan-iac-smoke/v1",
                    "runId": run_id,
                    "purpose": "development",
                    "provider": provider,
                    "region": REGIONS[provider],
                    "status": "failed",
                    "errorType": type(error).__name__,
                    "error": str(error),
                    "timingEvents": adapter.last_timing_events,
                    "resourcePlan": cloud_design.get("resource_plan") or {},
                    "scope": {"cloudApply": False, "businessBehavior": False},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return result_path
    plan = delivery["resourcePlan"]
    diagram = delivery["deploymentDiagramPuml"]
    node_aliases = {
        str(item["id"]).replace("-", "_") for item in plan.get("nodes") or []
    }
    missing_from_diagram = sorted(
        item for item in node_aliases if f"resource_{item}" not in diagram
    )
    infra = application / "infra"
    result = {
        "schemaVersion": "easydep-resource-plan-iac-smoke/v1",
        "runId": run_id,
        "purpose": "development",
        "provider": provider,
        "region": REGIONS[provider],
        "status": "passed" if not missing_from_diagram else "failed",
        "resourcePlan": plan,
        "diagram": {
            "path": "application/infra/deployment-diagram.puml",
            "missingNodeAliases": missing_from_diagram,
        },
        "delivery": {
            "status": delivery["status"],
            "llmCalls": delivery["llmCalls"],
            "timingEvents": delivery["timingEvents"],
            "repairEvents": delivery["repairEvents"],
            "preflight": delivery["preflight"],
            "files": delivery["files"],
        },
        "scope": {
            "application": "domain-neutral key/value API",
            "cloudApply": False,
            "businessBehavior": False,
            "secretsPersisted": False,
        },
        "inputHashes": {
            "sampleDockerfileSha256": _sha256(application / "Dockerfile"),
            "sampleServiceSha256": _sha256(application / "service.py"),
        },
    }
    infra.mkdir(parents=True, exist_ok=True)
    (infra / "deployment-diagram.puml").write_text(diagram, encoding="utf-8")
    result_path = run_root / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result_path


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=sorted(REGIONS), required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/measurements/resource-plan-iac"),
    )
    args = parser.parse_args()
    result = run(args.provider, args.output_root)
    print(result)
    payload = json.loads(result.read_text(encoding="utf-8"))
    return 0 if payload.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
