"""판정과 고지는 **다른 질문**이다 — `is_fact` 대 `needs_hedge`.

옛 계획(P2a)은 "신뢰도 < 0.7이면 경고"였다. 그 선은 `check`가 쓰던 0.8과도 어긋났고
신뢰도를 버리면서 전제가 사라졌다. 승계는 숫자를 다른 숫자로 바꾸는 일이 아니라
**섞여 있던 두 질문을 가르는** 일이었다.

    is_fact      값을 거부해도 되는가      검수가 면제된다
    needs_hedge  유보를 붙여야 하는가      검수는 면제가 아니다

검수의 뜻이 두 방향에서 다르다. 사람이 확인한 제약으로 값을 **막는** 건 정당하지만,
사람이 "가장 가깝다"고 고른 클라우드 간 동치를 **단언**하는 건 정당하지 않다.
"""

from __future__ import annotations

import regex
from pathlib import Path

#: 저장소 기준 경로. CWD 기준으로 열면 easydep 루트에서 돌 때 파일을 못 찾고,
#: exists() 가드가 있는 곳은 실패 대신 **조용히 스킵**된다(병합 때 실제로 그랬다).
_ROOT = Path(__file__).resolve().parent.parent

import pytest

from app.deployment.kbcommon.basis import INFERRED, STATED, is_fact, needs_hedge


@pytest.mark.parametrize(
    ("basis", "reviewed", "fact", "hedge"),
    [
        (STATED, False, True, False),    # 원본이 말했다 — 둘 다 문제없음
        (STATED, True, True, False),
        (INFERRED, False, False, True),  # 순수 짐작 — 거부 근거로도 못 쓰고 유보도 붙는다
        (INFERRED, True, True, True),    # **여기가 갈리는 칸이다**
    ],
)
def test_the_two_questions_diverge_on_reviewed_guesses(basis, reviewed, fact, hedge) -> None:
    assert is_fact(basis, reviewed) is fact
    assert needs_hedge(basis, reviewed) is hedge


def test_reviewed_guess_is_usable_but_not_assertable() -> None:
    """실측 사례: 검수된 동치 엣지를 모델이 단언했다(ALB → ComputeForwardingRule).

    두 함수가 갈리지 않으면 그 답을 막을 수 없다 — 한쪽 기준만으로는 '쓰지도 말라'
    아니면 '마음껏 단언하라' 둘 중 하나가 된다.
    """
    assert is_fact(INFERRED, reviewed=True)
    assert needs_hedge(INFERRED, reviewed=True)


def test_unknown_basis_hedges() -> None:
    """모르는 값은 안전한 쪽으로 — `basis_of`가 모르는 라벨을 짐작으로 보는 것과 같다."""
    assert needs_hedge("", False)
    assert needs_hedge("who-knows", True)


# `basis`를 문자열 리터럴과 직접 비교하는 곳을 찾는다. 정의는 kbcommon 한 곳이어야 한다.
_INLINE_RULE = regex.compile(r"""basis[^\n]{0,40}?[!=]=\s*["'](?:stated|inferred)["']""")

_ALLOWED = {
    _ROOT / "kbcommon/basis.py",  # 정의가 사는 곳
}


def test_the_guard_actually_bites() -> None:
    """**아무것도 안 무는 정규식은 조용히 통과한다.**

    이 저장소에서 정규식을 셸 heredoc으로 쓰다 `\\b`가 진짜 백스페이스 문자가 된 적이
    있다. 화면·grep·편집기에서 안 보였고 `repr()`만 알고 있었다. 그래서 가드 자신을
    검사한다.
    """
    assert _INLINE_RULE.search('if edge.basis != "stated":')
    assert _INLINE_RULE.search("if sustained.get('basis') == 'stated':")
    assert _INLINE_RULE.search('x = rec["basis"] == "inferred"')
    assert not _INLINE_RULE.search("assert needs_hedge(edge.basis, edge.reviewed)")
    assert not _INLINE_RULE.search("basis = STATED")


def test_no_kb_reinlines_the_rule() -> None:
    """**같은 규칙이 두 벌이면 갈라진다.**

    이 판단은 이름 없이 `basis != "stated"` 리터럴로 graphkb·perfkb에 따로 박혀
    있었다. perfkb의 필드 목록이 세 벌로 갈라져 도구끼리 상충하던 것과 같은
    모양이라, 여기서 막는다.
    """
    offenders = []
    for kb in ("graphkb", "capacitykb", "costkb", "perfkb", "kbcommon", "nim_agent"):
        for path in (_ROOT / kb).rglob("*.py"):
            if path in _ALLOWED:
                continue
            for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue  # 주석에서 옛 코드를 설명하는 건 괜찮다
                if _INLINE_RULE.search(line):
                    offenders.append(f"{path}:{num}")
    assert not offenders, (
        "basis를 직접 비교하지 말고 kbcommon.basis의 is_fact/needs_hedge를 쓰세요: "
        + ", ".join(offenders)
    )
