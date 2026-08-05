"""`app/core/` 층의 규약 — 문이 문으로 남고, 공용 층이 공용으로 남게 한다.

이 층은 요구사항 에이전트가 배포 KB에 닿는 유일한 통로로 시작했지만 거기서 멈추지 않는다
— **전체 시스템이 쓸 것들의 집**으로 자란다(추적성이 2026-07-28에 첫 사례로 들어왔다).
그래서 규약 하나가 더 중요해졌다: 세입자가 늘어도 **어느 에이전트도 알지 않는 것**.
성질은 코드가 아니라 규율이고, 규율은 문서에 적어 두면 조용히 깨진다.

지키는 것 둘:
  1. `app/core`는 어느 에이전트도 import하지 않는다 — 참조하는 순간 공용이 아니게 된다.
     새 모듈이 들어올 때마다 자동으로 검사된다(디렉터리를 훑는다).
  2. `app/requirements`의 **런타임 경로**는 `app.core.cloudkb`를 직접 import하지 않는다.
     개발·CI 도구만 예외이고, 그건 파이프라인에서 불리지 않는다.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "app" / "core"
_REQUIREMENTS = _ROOT / "app" / "requirements"

#: 지식베이스를 직접 import해도 되는 요구사항 쪽 모듈 — 런타임이 아니라 개발·CI 도구다.
_TOOLS = {"verify_concerns.py"}

#: **실험 하네스는 이 규약 밖이다.**
#:
#: `app/core/cloudkb/tools/`는 라이브러리가 아니라 손으로 돌리는 측정 장치다. 프로브
#: 하네스가 에이전트를 실제로 태워야 하고(`agent_probe`), 상류 산출물 실물을 만들려면
#: 요구사항 에이전트를 돌려야 한다(`build_sample`) — 그게 그 도구의 존재 이유다.
#: 규약이 지키려는 것은 *"공용 층이 특정 에이전트를 알면 공용이 아니다"*인데, 아무도
#: import하지 않는 잎은 층을 오염시키지 않는다. 그래서 **잎으로 남아 있는지를 대신
#: 검사한다**(`test_the_harness_stays_a_leaf`).
_HARNESS = _CORE / "cloudkb" / "tools"
_TESTS = _CORE / "cloudkb" / "tests"


def _library_modules() -> list[Path]:
    """**라이브러리 = 하네스도 테스트도 아닌 것.**

    지식베이스가 `app/core/` 안으로 들어오면서 이 층에 셋이 섞였다 — 라이브러리와
    실험 하네스와 테스트. 규약이 지키려는 것은 라이브러리의 성질이므로 셋을 갈라야
    한다. 안 가르면 규약이 거짓으로 실패하고, 그러면 규약을 끄게 된다.
    """
    return [
        p for p in sorted(_CORE.rglob("*.py"))
        if _HARNESS not in p.parents and _TESTS not in p.parents
    ]


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def test_the_layer_has_modules_to_check():
    """규약 테스트가 빈 목록을 훑으며 통과하는 상황을 막는다."""
    assert _library_modules()


def test_the_harness_stays_a_leaf():
    """하네스를 예외로 두는 값은 **아무도 그것을 import하지 않을 때만** 성립한다.

    라이브러리가 하네스를 부르기 시작하면 예외가 구멍이 된다 — 에이전트 의존이
    하네스를 타고 공용 층으로 들어온다.
    """
    offenders = sorted(
        f"{p.relative_to(_ROOT)} → {name}"
        for p in _library_modules()
        for name in _imports(p)
        if name.startswith("app.core.cloudkb.tools")
    )
    assert not offenders, f"라이브러리가 실험 하네스를 참조한다: {offenders}"


@pytest.mark.parametrize(
    "path", _library_modules(), ids=lambda p: p.name
)
def test_core_does_not_import_any_agent(path: Path):
    """공용 층이 특정 에이전트를 알면 공용이 아니다."""
    offenders = sorted(
        name
        for name in _imports(path)
        if name.startswith(("app.requirements", "app.design", "app.implementation"))
    )
    assert not offenders, f"{path.name}이 에이전트를 참조한다: {offenders}"


def test_requirements_reaches_the_kb_only_through_core():
    """**문이 하나로 남아 있는가.**

    이 검사가 없으면 `app/core`는 문이 아니라 구멍이 된다 — 한 곳이 편의로 직접
    import하는 순간 층이 존재할 이유가 사라지고, 다음 사람은 그게 허용된 줄 안다.
    """
    offenders = []
    for path in sorted(_REQUIREMENTS.rglob("*.py")):
        if path.name in _TOOLS:
            continue
        if any(name.startswith("app.core.cloudkb") for name in _imports(path)):
            offenders.append(str(path.relative_to(_REQUIREMENTS)))
    assert not offenders, (
        f"배포 KB를 직접 끌어온다: {offenders}. app/core/를 거쳐야 한다."
    )
