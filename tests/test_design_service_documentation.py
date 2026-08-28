"""설계 단계별 설명 계약이 코드 구조와 함께 유지되는지 검사한다."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_READMES = (
    ROOT / "app/design/services/README.md",
    ROOT / "app/design/services/class_diagram/README.md",
    ROOT / "app/design/services/class_diagram/validation/README.md",
    ROOT / "app/design/services/sequence_diagram/README.md",
    ROOT / "app/design/services/api_spec/README.md",
    ROOT / "app/design/services/erd/README.md",
    ROOT / "app/design/services/deployment_diagram/README.md",
)
CENTRAL_GUIDES = (
    ROOT / "docs/README.md",
    ROOT / "docs/class-design-pipeline.md",
    ROOT / "docs/class-design-code-conventions.md",
)
PYTHON_MODULES = tuple(
    path
    for directory in (
        ROOT / "app/design/services/class_diagram",
        ROOT / "app/design/services/sequence_diagram",
        ROOT / "app/design/services/api_spec",
        ROOT / "app/design/services/erd",
        ROOT / "app/design/services/deployment_diagram",
    )
    for path in directory.rglob("*.py")
    if path.name != "__init__.py"
)
PUBLIC_CONTRACTS = {
    ROOT / "app/design/services/class_diagram/inventory.py": (
        "inventory_payload",
        "inventory_proposal",
    ),
    ROOT / "app/design/services/class_diagram/operations.py": (
        "build_fragments",
        "repair_failed_operations",
    ),
    ROOT / "app/design/services/class_diagram/collaboration.py": ("process_group",),
    ROOT / "app/design/services/class_diagram/feedback.py": (
        "feedback_scope",
        "propose_inventory_revision",
        "replace_selected_groups",
    ),
    ROOT / "app/design/services/class_diagram/service.py": (
        "generate_class_model",
        "resume_class_model",
        "revise_class_model",
    ),
    ROOT / "app/design/services/class_diagram/validation/model.py": (
        "validate_class_model",
    ),
    ROOT / "app/design/services/sequence_diagram/projection.py": (
        "project_sequence_model",
        "sequence_findings",
    ),
    ROOT / "app/design/services/sequence_diagram/validation.py": (
        "validate_sequence_model",
    ),
    ROOT / "app/design/services/api_spec/service.py": (
        "generate_api_spec_model",
        "revise_api_spec_model",
    ),
    ROOT / "app/design/services/api_spec/normalization.py": (
        "normalize_api_spec_model",
    ),
    ROOT / "app/design/services/api_spec/validation.py": (
        "validate_api_spec_model",
    ),
    ROOT / "app/design/services/api_spec/projection.py": (
        "build_openapi_from_model",
    ),
    ROOT / "app/design/services/erd/service.py": ("revise_erd_model",),
    ROOT / "app/design/services/erd/projection.py": ("project_logical_model",),
    ROOT / "app/design/services/erd/table_mapping.py": ("build_entity_tables",),
    ROOT / "app/design/services/erd/relationship_mapping.py": (
        "build_multivalued_child",
        "relationship_endpoints",
        "map_relationship",
    ),
    ROOT / "app/design/services/erd/plantuml.py": ("render_logical_model",),
    ROOT / "app/design/services/deployment_diagram/service.py": (
        "generate_workload_graph",
        "revise_workload_graph",
    ),
    ROOT / "app/design/services/deployment_diagram/planning_facts.py": (
        "extract_planning_facts",
        "planning_context",
        "planning_inputs_stale",
    ),
    ROOT / "app/design/services/deployment_diagram/normalization.py": (
        "validate_workload_graph",
        "normalize_workload_graph",
    ),
    ROOT / "app/design/services/deployment_diagram/placement.py": (
        "build_deployment_plan",
        "validate_deployment_plan",
    ),
    ROOT / "app/design/services/deployment_diagram/runtime_binding.py": (
        "bind_runtime_contract",
    ),
    ROOT / "app/design/services/deployment_diagram/digest.py": (
        "workload_graph_structure_digest",
        "deployment_plan_structure_digest",
        "resource_plan_structure_digest",
    ),
    ROOT / "app/design/services/deployment_diagram/provider_template_generation.py": (
        "build_complete_provider_template",
        "provider_template_structure_digest",
    ),
    ROOT / "app/design/services/deployment_diagram/provider_template_validation.py": (
        "validate_complete_provider_template",
    ),
    ROOT / "app/design/services/deployment_diagram/runtime_renderer.py": (
        "render_runtime_deployment",
    ),
    ROOT / "app/design/services/deployment_diagram/provisioning_renderer.py": (
        "render_provisioning_dependencies",
    ),
    ROOT / "app/design/services/deployment_diagram/provider_plantuml.py": (
        "deployment_bundle_runtime_puml",
        "deployment_bundle_provisioning_puml",
    ),
}


def test_design_service_directories_have_local_readmes() -> None:
    missing = [str(path.relative_to(ROOT)) for path in LOCAL_READMES if not path.is_file()]
    assert not missing, f"missing design service README files: {missing}"


def test_local_document_links_resolve() -> None:
    link_pattern = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")
    missing: list[str] = []
    for document in (*LOCAL_READMES, *CENTRAL_GUIDES):
        content = document.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(content):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "/")):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {raw_target}")
    assert not missing, "broken local documentation links:\n" + "\n".join(missing)


def test_design_service_modules_explain_their_boundary() -> None:
    missing = [
        str(path.relative_to(ROOT))
        for path in PYTHON_MODULES
        if not ast.get_docstring(ast.parse(path.read_text(encoding="utf-8-sig")))
    ]
    assert not missing, f"modules without responsibility docstrings: {missing}"


def test_core_public_apis_document_input_output_and_invariants() -> None:
    missing: list[str] = []
    required_sections = ("Args:", "Returns:", "Notes:")
    for path, function_names in PUBLIC_CONTRACTS.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for function_name in function_names:
            function = functions.get(function_name)
            docstring = ast.get_docstring(function) if function is not None else None
            absent = [section for section in required_sections if section not in (docstring or "")]
            if function is None or absent:
                missing.append(
                    f"{path.relative_to(ROOT)}:{function_name} missing {absent or ['function']}"
                )
    assert not missing, "incomplete public API documentation:\n" + "\n".join(missing)
