"""문서 구조 — 문서가 아니라 검사로.

2026-07-24 정리에서 확인한 사실: 최상위 문서가 20편·588KB까지 자랐고, 한 파일이
5일간 29회 수정됐으며, 같은 사실이 다섯 곳(커밋·계획 문서·조사 문서·NOTICE·
docstring)에 적히고 있었다. 저장소 스스로의 교훈("문서로 적은 규약은 두 번
뒤처졌다")대로 정책을 검사로 고정한다 — 정책 본문은 `CLAUDE.md`.

    살아있는 문서      README · CLAUDE.md · NOTICE · document/{kb-book,research}.md
    불변 기록          document/archive/ (날짜 박힌 스냅샷 — 갱신 금지)
    새 문서            여기 허용 목록에 추가하는 커밋 = 정책 변경 — 사유가 필요하다
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: document/ 최상위 허용 목록. 늘리려면 "왜 archive가 아닌가"에 답해야 한다.
#:
#: `constraint-derivation.md` (2026-07-30 추가) — **도출은 끝나지 않고 갱신된다.**
#: 아카이브는 "그때 무엇을 봤나"의 스냅샷이라 갱신을 금지하는데, 이 문서는 새 소스가
#: 나오면 표를 고쳐야 하는 종류다. 실제로 이번에 가속기 축을 빠뜨린 것을 뒤늦게 찾아
#: 고쳤고, 아카이브였다면 정정을 못 싣고 새 문서를 또 만들었을 것이다.
#: `dependency-model.md` (2026-07-30 추가) — **개념 모델은 반증으로 자란다.**
#: 실제로 첫 판의 "필연의 출처 여섯" 가설이 관측 39건 중 9건에 반증돼 두 층 구조로
#: 바뀌었다. 아카이브는 갱신을 금지하므로 반증을 실을 자리가 없고, 새 문서를 또
#: 만들면 어느 것이 현재 모델인지 다시 알 수 없어진다.
#: `resource-catalog.md` (2026-07-30 추가) — **필드 표는 태그가 오르면 낡는다.**
#: 아카이브는 갱신을 금지하므로 소스 태그를 올릴 때 정정을 실을 자리가 없다.
#: `infra-planning-api.md` (2026-07-31 추가) — **다른 영역이 읽는 계약이다.**
#: 아카이브는 "그때 이렇게 불렀다"의 스냅샷이라 갱신을 금지하는데, 이 문서는
#: 설계·구현 에이전트가 호출법을 찾아오는 자리라 계약이 바뀌면 **그 자리에서**
#: 고쳐야 한다. 날짜 박힌 사본을 새로 만들면 어느 것이 현재 계약인지 모른다.
LIVING_DOCS = {
    "kb-book.md", "research.md", "constraint-derivation.md", "dependency-model.md",
    "resource-catalog.md", "infra-planning-api.md",
}


def test_document_top_level_is_fixed() -> None:
    found = {p.name for p in (ROOT / "document").iterdir() if p.is_file()}
    unexpected = found - LIVING_DOCS
    missing = LIVING_DOCS - found
    assert not unexpected, (
        f"document/ 최상위에 새 문서가 생겼습니다: {sorted(unexpected)} — "
        "불변 기록이면 document/archive/로, 살아있는 문서면 이 허용 목록에 "
        "사유와 함께 추가하세요"
    )
    assert not missing, f"살아있는 문서가 사라졌습니다: {sorted(missing)}"


def test_archive_has_an_index() -> None:
    """색인 없는 보관소는 무덤이다 — 한 줄씩이라도 무엇이 있는지 남긴다."""
    assert (ROOT / "document" / "archive" / "README.md").exists()


def test_no_dead_document_references_in_code() -> None:
    """코드 docstring이 가리키는 문서 경로는 실재해야 한다 — 이동·삭제 때
    참조가 조용히 썩는 것을 막는다(정리 중 죽은 참조 2건을 실제로 발견했다)."""
    import re

    pattern = re.compile(r"document/[A-Za-z0-9._/-]+\.md")
    packages = ("appkb", "bundlekb", "capacitykb", "costkb", "envkb", "graphkb",
                "kbcommon", "nim_agent", "patternkb", "perfkb", "sizingkb")
    broken: list[str] = []
    for package in packages:
        for path in (ROOT / package).rglob("*.py"):
            for ref in pattern.findall(path.read_text(encoding="utf-8")):
                if not (ROOT / ref).exists():
                    broken.append(f"{path.relative_to(ROOT)} → {ref}")
    assert not broken, "실재하지 않는 문서를 가리키는 참조:\n  " + "\n  ".join(broken)
