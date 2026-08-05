"""**뒤집기** — 도구가 "가능"이라 한 값을 답변이 못 한다고 단언하는가.

실측 사례: 도구가 *"938가지 중 840가지에서 가능"*이라 했는데 답변은 *"16.4에서는
지원되지 않음으로 표시되었습니다"*로 뒤집었다.

**숫자 대조로는 안 걸린다** — `16.4`는 도구 출력에 있기 때문이다. 뒤집힌 것은 값이
아니라 **부호**다. 그래서 "문장의 뜻을 봐야 하는 일이라 방법이 없다"로 미결이었다.

되는 이유는 뜻을 읽어서가 아니라 **우리가 쓴 형식을 읽어서**다. `가능:` / `불가:` 줄은
`capacitykb.agent_api.check`가 만든다. 임의의 주장이 뒤집혔는지는 여전히 못 잡는다.
"""

from __future__ import annotations

from app.core.cloudkb.tools.claim_check import check, flipped

# `cap_check_value`의 조건부 판정 출력 그대로.
TOOL = (
    "조건에 따라 다릅니다: AWS::RDS::DBInstance.EngineVersion = 16.4 는 Region 에 "
    "따라 달라져서, 그 값을 알아야 확정할 수 있습니다. 조건 38가지 중 "
    "**30가지에서 가능**, 8가지에서 불가입니다.\n"
    "  가능: Region = ap-northeast-2; Region = us-east-1; 16.4 외 5가지\n"
    "  불가: Region = ap-east-1; Region = eu-south-2"
)


def test_the_observed_flip_is_caught() -> None:
    """**핵심 회귀.** 실측에서 났던 그 답변이다."""
    found = flipped("16.4에서는 지원되지 않음으로 표시되었습니다.", [TOOL])
    assert [f.token for f in found] == ["16.4"]
    assert found[0].kind == "flip"


def test_agreeing_answer_is_clean() -> None:
    assert not flipped("16.4는 대부분의 리전에서 가능합니다.", [TOOL])


def test_denied_value_may_be_negated() -> None:
    """`불가:`에도 있는 값은 세지 않는다 — 답변의 부정이 근거 있는 말이다."""
    assert not flipped("Region = ap-east-1 에서는 사용할 수 없습니다.", [TOOL])


def test_contrastive_sentence_does_not_false_positive() -> None:
    """**"A는 되지만 B는 안 됩니다"** — 절로 자르지 않으면 A가 부정에 걸린다."""
    answer = "Region = us-east-1 에서는 가능하지만 Region = ap-east-1 에서는 불가능합니다."
    assert not flipped(answer, [TOOL])


def test_no_possible_line_means_nothing_to_flip() -> None:
    """도구가 '가능' 목록을 안 냈으면 이 검사는 아무 말도 하지 않는다."""
    assert not flipped("지원되지 않습니다.", ["알려진 제약이 없어 판정할 수 없습니다."])
    assert not flipped("지원되지 않습니다.", [])


def test_short_tokens_are_ignored() -> None:
    """한 글자짜리 값이 답변 아무 데나 걸리면 신호가 아니라 소음이다."""
    tool = "조건 3가지 중 **2가지에서 가능**, 1가지에서 불가입니다.\n  가능: a; b\n  불가: c"
    assert not flipped("이 기능은 지원되지 않습니다.", [tool])


def test_version_numbers_survive_clause_splitting() -> None:
    """**하필 이 검사가 노리는 것이 버전 문자열이다.**

    절을 `[.!?]`로 자르면 `16.4`가 `16`과 `4`로 쪼개져 통째로 사라진다 —
    처음 구현에서 실제로 그랬고, 위 회귀 테스트가 잡았다.
    """
    from app.core.cloudkb.tools.claim_check import _CLAUSE

    assert _CLAUSE.split("16.4는 안 됩니다.") == ["16.4는 안 됩니다", ""]
    # 문장 끝 마침표는 여전히 자른다
    assert len(_CLAUSE.split("가능합니다. 불가합니다.")) == 3


def test_check_reports_flips_alongside_other_findings() -> None:
    verdict = check("16.4에서는 지원되지 않습니다.", [TOOL])
    assert [f.kind for f in verdict.unsupported if f.kind == "flip"] == ["flip"]
    assert not verdict.clean
