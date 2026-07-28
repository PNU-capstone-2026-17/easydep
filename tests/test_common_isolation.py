"""`app/requirements/common/` 이 요구사항 에이전트를 모른다는 규약을 고정한다.

이 패키지는 다른 에이전트가 쓰게 되면 `app/core/` 로 옮길 것이고, 그때 바뀌는 것이
import 경로뿐이려면 지금부터 상류를 참조하지 않아야 한다. 규약은 문서가 아니라
여기서 지킨다 — 문서에 적어 두면 조용히 깨진다.
"""
import ast
from pathlib import Path

import pytest

COMMON_DIR = Path(__file__).resolve().parent.parent / "app" / "requirements" / "common"


def _module_files() -> list[Path]:
    return sorted(COMMON_DIR.rglob("*.py"))


def _imported_modules(source: str) -> set[str]:
    """이 파일이 끌어오는 모듈 경로를 전부 모은다(상대 import 포함)."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` 는 module이 None이고, 어차피 common 안쪽이라 통과다.
            if node.level == 0 and node.module:
                found.add(node.module)
    return found


def test_the_package_has_modules_to_check():
    """규약 테스트가 빈 목록을 훑으며 통과하는 상황을 막는다."""
    assert _module_files(), f"{COMMON_DIR} 에 검사할 모듈이 없다"


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_common_does_not_import_the_requirements_agent(path: Path):
    offenders = sorted(
        name
        for name in _imported_modules(path.read_text(encoding="utf-8"))
        if name.startswith("app.") and not name.startswith("app.requirements.common")
    )
    assert not offenders, (
        f"{path.name} 이 상류를 참조한다: {offenders}. "
        "common/ 은 app/core/ 로 옮길 수 있어야 하므로 에이전트 코드를 import 하지 않는다."
    )
