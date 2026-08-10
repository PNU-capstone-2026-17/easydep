import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_ROOT = ROOT / "evaluation" / "research_protocol"
ACTIVE_PROTOCOL_FILES = {
    "ambiguity-cases.json",
    "app-cloud-ablation-cases.json",
    "app-cloud-snapshot-cases.json",
    "capacity-recommendation-cases.json",
    "component-fixed-input-config.json",
}


def _active_python_files():
    return sorted((PROTOCOL_ROOT / "core").glob("*.py")) + sorted(
        (PROTOCOL_ROOT / "commands").glob("*.py")
    )


def test_research_runners_do_not_import_other_modules_private_names():
    violations = []
    for path in _active_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not (node.module or "").startswith("evaluation.research_protocol."):
                continue
            for imported in node.names:
                if imported.name.startswith("_"):
                    violations.append(f"{path.name}:{node.lineno}:{imported.name}")
    assert violations == []


def test_repository_root_is_derived_only_in_paths_module():
    violations = []
    for path in _active_python_files():
        if path == PROTOCOL_ROOT / "core" / "paths.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "Path(__file__).resolve().parents" in source:
            violations.append(path.name)
    assert violations == []


def test_active_protocol_inputs_are_not_mixed_with_runner_modules():
    top_level_json = {path.name for path in PROTOCOL_ROOT.glob("*.json")}
    assert top_level_json == set()
    assert {
        path.name for path in (PROTOCOL_ROOT / "protocols").glob("*.json")
    } == ACTIVE_PROTOCOL_FILES
    assert (PROTOCOL_ROOT / "definitions" / "protocol.json").is_file()


def test_protocol_root_contains_only_navigation_files():
    assert {path.name for path in PROTOCOL_ROOT.iterdir() if path.is_file()} == {
        "README.md"
    }
