"""**모르는 것을 0원이라 하는가** — `flipped`의 거울상.

실측 사례(GL1): 도구가 *"값을 매길 수 없는 것 — 이 데이터셋에 가격 축이 없음:
securityGroup · sshKey · subnet · vNet"*이라 했는데 답변이 *"보안 그룹, VPC, 서브넷,
SSH 키: 별도 요금이 부과되지 않음(무료)"*이라고 적었다.

**숫자 대조로는 절대 안 걸린다** — 지어낸 숫자가 없기 때문이다. 그런데 이 프로젝트가
막으려는 실패 그대로다: 모르는 것을 0으로 채우면 사람은 그 숫자로 예산을 정한다.
서브넷 예약 IP를 몰라서 256이라 답하던 것과 같은 모양이고, 그때보다 아프다.

## 이름을 대조하지 않는 이유 — 처음엔 그렇게 짰고 0건이 나왔다

도구는 `securityGroup`·`vNet`이라 쓰는데 답변은 **"보안 그룹"·"VPC"**라고 쓴다.
모델이 옮겨 적으므로 토큰 대조는 살아남지 못한다. 대신 `misattributed`와 같은 논리를
쓴다 — **우리 도구는 어떤 리소스도 공짜라고 말하지 않는다.**
"""

from __future__ import annotations

from app.core.cloudkb.tools.claim_check import check, priced_as_free

# `resource_guideline` 출력 그대로.
TOOL = (
    "vm 을(를) 고르면 — 리소스 군과 그 값  [tumblebug 동적 VM 생성]\n"
    "\n[값을 매길 수 있는 것]\n"
    "  vm  aws ap-northeast-2 t3a.medium  2 vCPU / 4.0 GiB  $0.0468/h\n"
    "      월 약 $34.16 (730시간 기준)\n"
    "\n[값을 매길 수 없는 것 — 이 데이터셋에 가격 축이 없음]\n"
    "  (반드시 함께 만들어짐) securityGroup · sshKey · subnet · vNet\n"
    "  (값을 반드시 줘야 함) image\n"
    "  ※ **무료라는 뜻이 아닙니다.** 이 데이터셋에 그 리소스의 가격 축이 없다는 "
    "뜻이며, 실제로는 과금될 수 있습니다(예: 공인 IP·데이터 전송).\n"
    "\n※ **합계를 내지 않습니다.**\n"
)


def test_the_observed_flip_is_caught() -> None:
    """**핵심 회귀.** GL1 첫 실행에서 실제로 나온 문장이다."""
    answer = (
        "AWS 서울 t3a.medium $0.0468/h 입니다.\n"
        "- 보안 그룹, VPC, 서브넷, SSH 키: 별도 요금이 부과되지 않음(무료)\n"
    )
    assert priced_as_free(answer, [TOOL])


def test_paraphrase_does_not_hide_it() -> None:
    """도구는 `vNet`이라 쓰고 답변은 'VPC'라 쓴다 — 이름 대조로는 못 잡는다."""
    assert priced_as_free("VPC와 서브넷은 무료입니다.", [TOOL])


def test_our_own_disclaimer_is_not_a_violation() -> None:
    """답변이 **우리가 시킨 말**을 옮긴 것을 위반으로 세면 옳은 행동을 벌한다."""
    answer = (
        "vNet·subnet은 이 데이터셋에 가격 축이 없어 값을 내지 않았습니다. "
        "무료라는 뜻은 아닙니다."
    )
    assert priced_as_free(answer, [TOOL]) == []


def test_plain_disclaimer_alone_is_clean() -> None:
    assert priced_as_free("무료라는 뜻이 아닙니다.", [TOOL]) == []


def test_passing_the_price_through_is_clean() -> None:
    answer = "AWS 서울 t3a.medium 시간당 $0.0468, 월 약 $34.16입니다."
    assert priced_as_free(answer, [TOOL]) == []


def test_any_free_claim_counts_even_for_things_we_never_listed() -> None:
    """*"기본 이미지는 무료"*도 우리가 한 말이 아니다 — 근거 없는 건 마찬가지다."""
    assert priced_as_free("기본 리눅스 이미지는 무료입니다.", [TOOL])


def test_silent_when_the_tool_never_wrote_that_block() -> None:
    """다른 도구 출력에까지 이 검사를 걸면 오탐이 는다. **우리 형식이 있을 때만** 본다."""
    assert priced_as_free("무료입니다.", ["t3.medium 시간당 $0.0416입니다."]) == []


def test_check_reports_it_alongside_the_others() -> None:
    verdict = check("모두 무료입니다.", [TOOL], "")
    assert any(f.kind == "free" for f in verdict.unsupported)
