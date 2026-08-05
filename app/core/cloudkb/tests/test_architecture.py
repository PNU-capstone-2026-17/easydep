"""아키텍처 규약 — 문서가 아니라 검사로.

문서로 적은 규약은 이 저장소에서 두 번 뒤처졌다(decisions.md §7이 닫힌 항목을
미결로 들고 있었다). 임포트 방향 규약은 관행으로만 있었고, 그 틈에서 KB끼리
서로의 **비공개 헬퍼**를 빌려 쓰는 일이 실제로 생겼다(아래 예외표의 두 건).
그래서 규약을 여기 AST 전수 검사로 고정한다.

규칙:
    kbcommon   → 프로젝트 내부 임포트 금지 (공용 배관은 아래를 모른다)
    *kb        → kbcommon만 (KB끼리는 조인하지 않는다 — 조인은 도구 계층의 일)
    nim_agent  → KB의 공개 표면만
    공개 표면   = agent_api(문장) · dataset(값) · model(상수·타입) · query(값 —
                 capacitykb·graphkb처럼 dataset이 없거나 값 조회가 query에 있는 KB)
                 + appkb 전부(데이터셋 KB가 아니라 계약·계획 라이브러리)
    비공개      = parsers · cli · invariants · 그 외 전부, 그리고 `_` 접두 이름

tests/와 tools/는 대상이 아니다 — 테스트는 내부를 검사하는 것이 일이고,
tools/는 실험 하네스라 에이전트 전체를 조립한다.

예외표의 요점: **비어 있는 것이 목표가 아니라 정확한 것이 목표다.** 알려진 위반을
사유와 함께 올려 두고, 새 위반(표에 없는 것)과 죽은 예외(표에만 있는 것)를 둘 다
실패로 만든다 — 표가 실제와 어긋나는 순간 검사가 알려 준다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: easydep 병합(2026-07-25) 이후 이 하위 시스템의 임포트 접두. 규약은 접두를 뗀
#: 이름으로 따진다 — 이 검사가 말하는 "프로젝트"는 저장소 전체가 아니라
#: `app/core/cloudkb` 다. 접두를 떼지 않으면 모든 임포트의 top이 "app"이 되어
#: 위반이 한 건도 안 잡히는, 통과하지만 아무것도 안 지키는 검사가 된다.
PACKAGE_PREFIX = "app.core.cloudkb."

KB_PACKAGES = {
    "appkb",
    "bundlekb",
    "capacitykb",
    "costkb",
    "envkb",
    "graphkb",
    "patternkb",
    "perfkb",
    "sizingkb",
}
ALL_PACKAGES = KB_PACKAGES | {"kbcommon", "nim_agent"}

# 도구 계층이 만져도 되는 KB 모듈. agent_api는 문장을, dataset·model·query는 값을
# 돌려준다 — 조인에는 값이 필요하다는 것이 goal2 작업에서 확인된 구분이다.
PUBLIC_MODULES = {"agent_api", "dataset", "model", "query"}

# 표준 모양과 다른 KB의 공개 표면. envkb는 축마다 모듈 하나가 곧 표면이다
# (코드 이동이지 재작성이 아니라 모양을 소급 통일하지 않았다). cloudinfo는
# regions의 파서라 비공개다.
PUBLIC_PER_PACKAGE = {
    "envkb": {"carbon", "latency", "regions", "lifecycle", "cbspider", "images"},
}

# (파일, 임포트한 모듈) → 사유. 항목이 실제 위반과 1:1이 아니면 검사가 실패한다.
# 2026-07-24: 헬퍼 차용 2건(capacitykb↔graphkb)은 본체를 kbcommon.fetch로 올려
# 해소했다 — 남은 한 건은 예외가 아니라 설계다.
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("perfkb/cli.py", "costkb.dataset"): (
        "미러 전제: perfkb는 costkb가 미러한 스펙 카탈로그(BUILT_FILENAME)에 성능 "
        "주석을 다는 축이다 — 의도된 단방향 의존이라 숨기지 않고 여기 남긴다"
    ),
}


def _project_imports(path: Path):
    """파일 하나의 절대 임포트를 (모듈, 이름들)로 낸다. 상대 임포트는 패키지
    내부이므로 여기서 걸러진다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ()
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, tuple(alias.name for alias in node.names)


def _violations() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for pkg in sorted(ALL_PACKAGES):
        for path in sorted((ROOT / pkg).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            for module, names in _project_imports(path):
                module = module.removeprefix(PACKAGE_PREFIX)
                parts = module.split(".")
                top = parts[0]
                if top not in ALL_PACKAGES or top == pkg:
                    continue
                key = (rel, module)

                if pkg == "kbcommon":
                    found[key] = "kbcommon은 프로젝트 내부를 임포트하지 않는다"
                    continue
                if pkg in KB_PACKAGES:
                    if top != "kbcommon":
                        found[key] = "KB는 kbcommon만 임포트한다 (조인은 도구 계층의 일)"
                    continue

                # nim_agent → KB: 공개 표면만.
                if top not in KB_PACKAGES or top == "appkb":
                    continue  # kbcommon·appkb는 전부 공개
                public = PUBLIC_PER_PACKAGE.get(top, PUBLIC_MODULES)
                if len(parts) == 1:
                    # `from costkb import agent_api` 꼴 — 가져온 이름이 곧 모듈이다.
                    hidden = [n for n in names if n not in public]
                    if hidden:
                        found[key] = f"KB 공개 표면이 아님: {', '.join(hidden)}"
                elif parts[1] not in public:
                    found[key] = f"KB 공개 표면({', '.join(sorted(public))})이 아님"
                elif private := [n for n in names if n.startswith("_")]:
                    found[key] = f"비공개 이름 임포트: {', '.join(private)}"
    return found


def test_import_rules_match_exception_table() -> None:
    found = _violations()
    unexpected = {k: v for k, v in found.items() if k not in EXCEPTIONS}
    stale = {k: v for k, v in EXCEPTIONS.items() if k not in found}
    assert not unexpected, (
        "예외표에 없는 임포트 위반 — 규약을 지키거나, 사유가 있으면 표에 올리세요:\n"
        + "\n".join(f"  {rel} → {mod}: {why}" for (rel, mod), why in sorted(unexpected.items()))
    )
    assert not stale, (
        "죽은 예외 — 위반이 사라졌으니 표에서 지우세요:\n"
        + "\n".join(f"  {rel} → {mod}" for (rel, mod) in sorted(stale))
    )


def test_exceptions_all_carry_reasons() -> None:
    """예외는 사유가 있어야 예외다 — 빈 사유는 규약을 조용히 넓힌다."""
    assert all(why.strip() for why in EXCEPTIONS.values())


def test_kbcommon_owns_no_datasets() -> None:
    """**kbcommon에는 데이터셋이 없다.** 자체 산출물을 소유하면 그것은 KB다.

    리전·탄소·지연·수명주기·드라이버 커버리지·이미지가 "공용 배관" 안에서
    ~1,900줄짜리 KB 여섯으로 자랐던 일(→ envkb로 분리, 재편 계획 ⑤)의 재발
    방지다. 소유의 표식은 `ARTIFACT = "….json"` 선언 — 이 규약은 모든 KB가
    따르므로 이것만 봐도 충분하다.
    """
    pattern = re.compile(r"^_?(DEFAULT_)?ARTIFACT\s*=", re.MULTILINE)
    owners = [
        path.name
        for path in sorted((ROOT / "kbcommon").glob("*.py"))
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert owners == [], f"kbcommon이 산출물을 소유한다 — KB로 분리하세요: {owners}"
