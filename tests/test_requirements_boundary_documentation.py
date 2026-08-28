"""새 요구사항 bounded context의 README와 한국어 코드 설명을 검증한다."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_ROOT = ROOT / "app" / "requirements"
BOUNDED_CONTEXTS = (
    "contracts",
    "runtime",
    "resources",
    "modeling",
    "orchestration",
)
BOUNDARY_READMES = {
    **{
        f"requirements/{context}": REQUIREMENTS_ROOT / context / "README.md"
        for context in BOUNDED_CONTEXTS
    },
    "metrics": ROOT / "app" / "metrics" / "README.md",
}
HANGUL = re.compile(r"[가-힣]")


def test_new_bounded_context_readmes_cover_the_five_operational_sections() -> None:
    """각 canonical 경계가 입력·출력·부수효과·실패·금지 의존성을 설명한다."""

    missing: list[str] = []
    for context, path in BOUNDARY_READMES.items():
        if not path.is_file():
            missing.append(f"{context}: README.md")
            continue
        text = path.read_text(encoding="utf-8")
        for heading in (
            "## 입력",
            "## 출력",
            "## 부수효과",
            "## 금지 의존성",
            "## 실패 조건",
        ):
            if heading not in text:
                missing.append(f"{context}: {heading}")

    assert missing == []


def test_new_bounded_context_modules_have_korean_responsibility_docstrings() -> None:
    """새 canonical module은 책임을 한국어 module docstring으로 기록한다."""

    missing: list[str] = []
    for context in BOUNDED_CONTEXTS:
        for path in sorted((REQUIREMENTS_ROOT / context).glob("*.py")):
            if path.name == "__init__.py":
                continue
            docstring = ast.get_docstring(
                ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            )
            if not docstring or HANGUL.search(docstring) is None:
                missing.append(path.relative_to(ROOT).as_posix())

    assert missing == []


def test_canonical_public_symbols_have_korean_contract_docstrings() -> None:
    """새 canonical 경계의 공개 class·function 설명 누락을 막는다."""

    missing: list[str] = []
    for context in BOUNDED_CONTEXTS:
        for path in sorted((REQUIREMENTS_ROOT / context).glob("*.py")):
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in tree.body:
                if not isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ) or node.name.startswith("_"):
                    continue
                docstring = ast.get_docstring(node)
                if not docstring or HANGUL.search(docstring) is None:
                    relative = path.relative_to(REQUIREMENTS_ROOT).as_posix()
                    missing.append(f"{relative}:{node.name}")

    assert missing == []
