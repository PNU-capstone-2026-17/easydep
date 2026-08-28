"""클래스·시퀀스·API 설계의 설명 계약이 코드 구조와 함께 유지되는지 검사한다."""

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
