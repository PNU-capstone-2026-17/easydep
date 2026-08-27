"""AST checks for the application package boundaries.

These checks intentionally inspect source rather than importing modules.  That
keeps the migration gate useful while packages are being moved: a failed
assertion lists every file, line, and import that still needs attention.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPOSITORY_ROOT / "app"
CLOUDKB_ROOT = APP_ROOT / "cloudkb"
DESIGN_SERVICES_ROOT = APP_ROOT / "design" / "services"
IMPLEMENTATION_ROOT = APP_ROOT / "implementation"


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _imports(path: Path) -> list[tuple[int, str]]:
    """Return absolute import names with their source line numbers."""

    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def _forbidden_imports(root: Path, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    offenders: list[str] = []
    for path in _python_files(root):
        for line, imported in _imports(path):
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in forbidden_prefixes
            ):
                relative = path.relative_to(REPOSITORY_ROOT).as_posix()
                offenders.append(f"{relative}:{line}: {imported}")
    return offenders


def _assert_no_forbidden_imports(
    root: Path, forbidden_prefixes: tuple[str, ...]
) -> None:
    offenders = _forbidden_imports(root, forbidden_prefixes)
    assert not offenders, "forbidden package imports remain:\n" + "\n".join(offenders)


def test_no_app_core_imports_remain():
    """The old app.core namespace must disappear from application imports."""

    _assert_no_forbidden_imports(APP_ROOT, ("app.core",))


def test_cloudkb_does_not_import_downstream_application_packages():
    _assert_no_forbidden_imports(
        CLOUDKB_ROOT,
        ("app.requirements", "app.design", "app.implementation"),
    )


def test_design_services_do_not_import_legacy_graph_or_repository_modules():
    _assert_no_forbidden_imports(
        DESIGN_SERVICES_ROOT,
        ("app.design.graphs", "app.repositories"),
    )


def test_implementation_does_not_import_design_service_internals():
    _assert_no_forbidden_imports(
        IMPLEMENTATION_ROOT,
        ("app.design.services",),
    )
