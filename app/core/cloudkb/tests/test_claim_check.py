"""주장 토큰 대조기 — 답변의 구체값이 도구 출력에 근거하는지.

**이 검사기 자체가 조용히 틀릴 수 있어서** 회귀를 둔다. 처음 만들었을 때 한국어
답변에서 정답을 오답으로, 오답을 정답으로 둘 다 잘못 읽었다.
"""

from __future__ import annotations

from app.core.cloudkb.tools.claim_check import check


def test_hangul_does_not_break_word_boundaries() -> None:
    r"""파이썬 `\b`·`\w`는 한글을 단어 문자로 본다.

    - `64개` → 경계가 안 잡혀 **검사 자체가 안 됐다**(정답을 못 알아봄).
    - `db.t3.medium을` → 조사까지 토큰에 들어왔다(정답을 오답으로 표시).
    둘 다 실측에서 났다.
    """
    verdict = check("한도는 64개입니다.", ["기본 64 (출처 문서)"])
    assert verdict.checked == 1, "한글에 붙은 숫자를 아예 안 봤다"
    assert verdict.clean

    verdict = check("db.t3.medium을 쓸 수 있습니다.", ["DBInstanceClass = db.t3.medium 가능"])
    assert verdict.clean, "조사가 토큰에 붙어 근거를 못 찾았다"


def test_flags_specifics_absent_from_tool_output() -> None:
    """도구가 준 적 없는 구체값은 표시한다 (실측 S21: 지어낸 GPU 사양)."""
    verdict = check(
        "p3.2xlarge – 1 × V100 | 8 vCPU | 61 GiB",
        ["InstanceType: 허용값 (예: p3.2xlarge, g5.xlarge …)"],
    )
    tokens = {f.token for f in verdict.unsupported}
    assert "61" in tokens, "도구에 없는 사양을 통과시켰다"
    assert "p3.2xlarge" not in tokens, "도구에 있는 값을 잘못 표시했다"


def test_values_from_the_question_are_not_claims() -> None:
    """사용자가 준 값을 되풀이하는 건 주장이 아니다."""
    verdict = check(
        "16.4에서는 확인되지 않았습니다.",
        ["조건에 걸리는 것이 없습니다"],
        "aurora-postgresql 16.4에서 쓸 수 있어?",
    )
    assert verdict.clean


def test_prefixed_instance_classes_are_recognized() -> None:
    """`db.r5.large` 처럼 접두사가 붙은 꼴도 하나의 식별자다.

    처음엔 `r5.large`만 보고 앞의 `db.`를 놓쳐서, 근거 없는 대안 추천이
    통째로 안 걸렸다.
    """
    verdict = check("대신 db.r5.large 를 쓰세요.", ["db.t3.medium 가능"])
    assert {f.token for f in verdict.unsupported} == {"db.r5.large"}


def test_rounding_is_not_invention() -> None:
    """도구가 `73.8%`라 하고 답변이 `74%`라 쓰는 것은 지어내기가 아니다.

    실측(2026-07-25) 분류에서 이 종류가 오탐으로 나왔다. **자릿수를 늘려 주지는
    않는다** — 답변이 쓴 자리에서만 비교하므로 `900` vs `901`은 그대로 걸린다.
    """
    from app.core.cloudkb.tools.claim_check import check

    grounded = check("co-occurs in 74% of templates", ["… 73.8% AWS::EC2::VPC …"], "")
    assert not [f for f in grounded.unsupported if f.token == "74"]

    invented = check("the max is 900 seconds", ["… max 901 seconds …"], "")
    assert [f for f in invented.unsupported if f.token == "900"]


def test_advisory_only_turns_are_not_value_checked() -> None:
    """**patternkb는 값이 아니라 산문 인용을 준다.**

    설계 지침 답변에 모델이 "back-off 30s" 같은 관례를 덧붙이는 것은 클라우드
    사실을 지어내는 것이 아니다(실측 CF5: 답변에서도 "Typical implementation"
    열 아래에 있었다). 세면 잡음만 는다 — 124회에서 숫자 표시 42건 중 26건이
    이 한 프로브였고 진짜는 1건이었다.
    """
    from app.core.cloudkb.tools.claim_check import check

    advisory = check(
        "Configure a capped back-off (e.g., 30 s) and 3-5 attempts.",
        ["Design guidance search (2) — evidence: design pattern document"],
        "",
        called_tools=["pattern_search"],
        known_tools=frozenset({"pattern_search"}),
    )
    assert not advisory.unsupported

    # 값 축을 **함께** 불렀으면 다시 대조 대상이다 — 그때는 사실을 말하고 있다.
    mixed = check(
        "Configure a capped back-off (e.g., 30 s); the max volume is 99999 GiB.",
        ["Design guidance search (2)", "Size: max 16384 GiB"],
        "",
        called_tools=["pattern_search", "cap_check_value"],
        known_tools=frozenset({"pattern_search", "cap_check_value"}),
    )
    assert [f for f in mixed.unsupported if f.token == "99999"]
