from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Any

import hcl2

from app.core.orchestration.iac_renderer import render_open_tofu
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from evaluation.easydep.requirements.evaluate import (
    preclassified_errors,
    requirements_semantic_oracle,
)

from .catalog import digest, write_json
from .oracles import case_expectation_issues, product_contract_issues


def render_plantuml(puml_text: str, image_format: str = "png") -> bytes:
    """Render checkpoint evidence with the user's local, offline ``puml`` tool."""

    command = shutil.which("puml")
    if not command:
        raise RuntimeError(
            "Local PlantUML renderer 'puml' is not installed or is not on PATH"
        )
    with tempfile.TemporaryDirectory(prefix="easydep-checkpoint-puml-") as directory:
        source = Path(directory) / "diagram.puml"
        source.write_text(puml_text, encoding="utf-8")
        result = subprocess.run(
            [command, str(source), image_format],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        target = source.with_suffix(f".{image_format}")
        if not target.is_file():
            rendered = list(Path(directory).glob(f"*.{image_format}"))
            if len(rendered) == 1:
                target = rendered[0]
        if result.returncode != 0 or not target.is_file():
            detail = "\n".join(
                value.strip() for value in (result.stdout, result.stderr)
                if value.strip()
            )
            raise RuntimeError(detail or "Local PlantUML rendering failed")
        return target.read_bytes()


def _ids(items: Any, *keys: str) -> list[str]:
    if not isinstance(items, list):
        return []
    return sorted(
        str(item.get(key))
        for item in items
        if isinstance(item, dict)
        for key in keys
        if item.get(key)
    )


def semantic_signature(checkpoint: str, state: dict[str, Any]) -> dict[str, Any]:
    if checkpoint == "requirements":
        requirements = state.get("classified") or state.get("refined_requirements") or []
        contract = state.get("capability_contract") or {}
        capabilities = contract.get("capabilities") or contract.get("decisions") or []
        return {
            "requirements": requirements_semantic_oracle(requirements),
            "capabilityCount": len(capabilities) if isinstance(capabilities, list) else 0,
            "resourceSpecSchema": (state.get("resource_spec") or {}).get("schemaVersion"),
        }
    if checkpoint in {"use_cases", "specifications", "usecase_diagram"}:
        return {
            "actorNames": _ids(state.get("actors"), "name"),
            "useCaseIds": _ids(state.get("use_cases"), "id"),
            "specUseCaseIds": _ids(state.get("use_case_specs"), "use_case_id"),
            "hasDiagram": bool(state.get("diagram") or state.get("usecase_diagram_puml")),
        }
    if checkpoint == "class_diagram":
        return {
            "classes": _ids((state.get("extracted_bce_classes") or {}).get("Classes"), "className"),
            "findingRules": sorted(
                _finding_rule(item)
                for item in (state.get("class_diagram_check") or {}).get("findings") or []
            ),
        }
    if checkpoint == "sequence_diagram":
        model = state.get("sequence_diagram_model") or {}
        return {
            "useCaseIds": _ids(model.get("Diagrams"), "use_case_id"),
            "findingRules": sorted(
                _finding_rule(item)
                for item in (state.get("sequence_diagram_check") or {}).get("findings") or []
            ),
        }
    if checkpoint == "api_spec":
        model = state.get("api_spec_model") or {}
        return {
            "operations": sorted(
                f"{str(item.get('method') or '').upper()} {item.get('path')}"
                for item in model.get("Endpoints") or []
            ),
            "schemas": _ids(model.get("Schemas"), "name"),
        }
    if checkpoint == "erd":
        return {
            "entities": _ids((state.get("erd_bce_classes") or {}).get("Classes"), "className"),
            "findingRules": sorted(
                _finding_rule(item)
                for item in (state.get("erd_check") or {}).get("findings") or []
            ),
        }
    if checkpoint == "deployment_diagram":
        bundle = state.get("deployment_diagram_bundle") or {}
        graph = bundle.get("workloadGraph") or state.get("deployment_workload_graph") or {}
        projection = (bundle.get("projections") or [{}])[0]
        plan = projection.get("deploymentPlan") or state.get("deployment_plan") or {}
        resources = projection.get("resourcePlan") or state.get("deployment_resource_plan") or {}
        return {
            "workloads": _ids(graph.get("workloads"), "id"),
            "connections": _ids(graph.get("connections"), "id"),
            "computeKinds": sorted(item.get("kind") for item in plan.get("computeUnits") or []),
            "placements": sorted(
                f"{item.get('workloadRef')}->{item.get('computeUnitRef')}"
                for item in plan.get("placements") or []
            ),
            "resourceKinds": sorted(item.get("providerPrimitiveKind") for item in resources.get("nodes") or []),
            "issueStatuses": sorted(item.get("status") for item in bundle.get("issues") or []),
        }
    return {"stateSha256": digest(state)}


def _finding_rule(value: Any) -> str:
    text = str(value)
    match = re.search(r"\[([^\]]+)\]", text)
    return match.group(1) if match else text


def blocking_issues(state: dict[str, Any], *, through: str = "relationships") -> list[str]:
    """Read the shared requirements/design gate when the production hook exists.

    Keeping the import lazy preserves standalone evaluation and lets a checkpoint
    record a meaningful failure while the shared gate itself is being developed.
    """
    try:
        supervisor = import_module("app.requirements.agent.supervisor")
        checker = getattr(supervisor, "blocking_issues")
    except (ImportError, AttributeError):
        return []
    try:
        issues = checker(state, through=through)
    except Exception as error:  # a broken gate must block promotion, not disappear
        return [f"shared blocking gate failed: {type(error).__name__}: {error}"]
    if not isinstance(issues, list):
        return ["shared blocking gate returned a non-list result"]
    return [str(issue) for issue in issues if str(issue).strip()]


def validate_state(checkpoint: str, state: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    required = {
        "requirements": ("classified", "capability_contract", "resource_spec"),
        "use_cases": ("actors", "use_cases"),
        "specifications": ("use_case_specs", "spec_report"),
        "usecase_diagram": ("diagram",),
        "class_diagram": ("extracted_bce_classes", "class_diagram_puml"),
        "sequence_diagram": ("sequence_diagram_model",),
        "api_spec": ("api_spec_model", "api_spec"),
        "erd": ("erd_bce_classes", "erd_puml"),
        "deployment_diagram": ("deployment_diagram_bundle",),
    }[checkpoint]
    for key in required:
        if not state.get(key):
            errors.append(f"missing required output: {key}")
    if checkpoint == "requirements":
        errors.extend(preclassified_errors(state.get("classified")))
    errors_key = f"{checkpoint}_syntax_errors"
    errors.extend(str(item) for item in state.get(errors_key) or [])
    if checkpoint == "deployment_diagram" and state.get("deployment_diagram_bundle"):
        bundle = state["deployment_diagram_bundle"]
        if bundle.get("schemaVersion") != "easydep-deployment-diagram":
            errors.append("unsupported deployment bundle schema")
        projection = (bundle.get("projections") or [{}])[0]
        if projection.get("status") != "completed":
            errors.append("deployment provider projection is not completed")
    if checkpoint == "usecase_diagram":
        errors.extend(blocking_issues(state, through="relationships"))
    errors.extend(product_contract_issues(checkpoint, state))
    errors.extend(case_expectation_issues(checkpoint, state))
    return {"status": "failed" if errors else "passed", "errors": errors, "warnings": warnings}


def write_outputs(checkpoint: str, state: dict[str, Any], output: Path) -> dict[str, Any]:
    # A resumed checkpoint may replace a previous failed attempt whose output
    # had more diagrams or IaC files. Evidence is a snapshot, not an overlay.
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    def json_file(name: str, value: Any) -> None:
        if value:
            write_json(output / name, value)
            written.append(name)

    def diagram(name: str, source: str) -> None:
        if not source.strip():
            return
        (output / f"{name}.puml").write_text(source, encoding="utf-8")
        rendered = render_plantuml(source, "svg")
        if not rendered:
            raise RuntimeError(f"PlantUML returned an empty SVG for {name}")
        (output / f"{name}.svg").write_bytes(rendered)
        written.extend((f"{name}.puml", f"{name}.svg"))

    if checkpoint == "requirements":
        for name in ("classified", "capability_contract", "resource_intake", "resource_spec"):
            json_file(f"{name}.json", state.get(name))
    elif checkpoint in {"use_cases", "specifications"}:
        for name in ("actors", "use_cases", "use_case_specs", "coverage", "spec_report"):
            json_file(f"{name}.json", state.get(name))
    elif checkpoint == "usecase_diagram":
        diagram("usecase", str(state.get("diagram") or state.get("usecase_diagram_puml") or ""))
    elif checkpoint == "class_diagram":
        json_file("class-model.json", state.get("extracted_bce_classes"))
        diagram("class", str(state.get("class_diagram_puml") or ""))
    elif checkpoint == "sequence_diagram":
        model = state.get("sequence_diagram_model") or {}
        json_file("sequence-model.json", model)
        gallery = output / "gallery"
        for index, item in enumerate(model.get("Diagrams") or [], start=1):
            gallery.mkdir(exist_ok=True)
            source = generate_sequence_from_model(item)
            safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(item.get("use_case_id") or index))
            (gallery / f"{index:02d}-{safe}.puml").write_text(source, encoding="utf-8")
            (gallery / f"{index:02d}-{safe}.svg").write_bytes(render_plantuml(source, "svg"))
            written.extend((f"gallery/{index:02d}-{safe}.puml", f"gallery/{index:02d}-{safe}.svg"))
    elif checkpoint == "api_spec":
        json_file("api-model.json", state.get("api_spec_model"))
        json_file("openapi.json", state.get("api_spec"))
    elif checkpoint == "erd":
        json_file("erd-model.json", state.get("erd_bce_classes"))
        diagram("erd", str(state.get("erd_puml") or ""))
    elif checkpoint == "deployment_diagram":
        bundle = state.get("deployment_diagram_bundle") or {}
        projection = (bundle.get("projections") or [{}])[0]
        json_file("bundle.json", bundle)
        json_file("workload-graph.json", bundle.get("workloadGraph"))
        json_file("deployment-plan.json", projection.get("deploymentPlan"))
        resource_plan = projection.get("resourcePlan") or {}
        json_file("resource-plan.json", resource_plan)
        diagram("runtime", str(state.get("deployment_diagram_puml") or ""))
        diagram("provisioning", str(state.get("deployment_diagram_provisioning_puml") or ""))
        if resource_plan:
            iac = output / "iac"
            iac.mkdir(exist_ok=True)
            for name, content in render_open_tofu(resource_plan).items():
                (iac / name).write_text(content, encoding="utf-8")
                if name.endswith(".tf"):
                    with (iac / name).open(encoding="utf-8") as handle:
                        hcl2.load(handle)
                written.append(f"iac/{name}")
    return {"files": sorted(written)}
