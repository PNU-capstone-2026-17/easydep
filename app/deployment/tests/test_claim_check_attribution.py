"""출처 세탁을 잡는다 — **부르지도 않은 우리 도구를 출처로 대는 것**.

조사 24건 중 4건에서 KB 보증과 모델 기억이 안 갈렸고, 가장 위험한 형태가 이것이다.
실측에서 모델이 GPU 사양 표를 지어내고 *"cap_allowed_values 지식베이스에서 조회한
결과"*라고 적었다(`g5g`를 AMD라고 했다 — NVIDIA다). 그 도구는 그 턴에 호출되지 않았다.

**`basis`·소스 핀·교차 확인이 통째로 무의미해지는 실패다.** 숫자 대조와 달리 이건
오탐이 거의 없다 — 우리 도구 이름이 답변에 우연히 나올 수는 없다.
"""

from __future__ import annotations

from tools.claim_check import check, misattributed

KNOWN = frozenset({
    "cap_allowed_values", "cost_describe_spec", "perf_instance_profile",
    "kb_equivalent_types",
})


def test_naming_an_uncalled_tool_is_caught() -> None:
    """실측 사례 그대로."""
    found = misattributed(
        "vCPU 8 / 메모리 32 GiB입니다. cap_allowed_values 지식베이스에서 조회한 결과입니다.",
        called=["cost_describe_spec"],
        known=KNOWN,
    )
    assert [f.token for f in found] == ["cap_allowed_values"]
    assert found[0].kind == "attribution"


def test_naming_a_tool_you_actually_called_is_fine() -> None:
    """부른 도구를 언급하는 것은 정당하다 — 오히려 출처를 밝히는 좋은 답이다."""
    assert not misattributed(
        "cost_describe_spec로 조회하니 64 GiB입니다.",
        called=["cost_describe_spec"], known=KNOWN,
    )


def test_grounded_names_are_exempt() -> None:
    """도구 출력이 먼저 그 이름을 말했으면 답변은 옮긴 것이다."""
    assert not misattributed(
        "성능은 perf_instance_profile로 보세요.", called=[], known=KNOWN,
        grounded="※ 성능 특성은 perf_instance_profile(...) 로 보세요.",
    )
    # 근거가 없으면 그대로 걸린다
    assert misattributed(
        "성능은 perf_instance_profile로 보세요.", called=[], known=KNOWN
    )


def test_check_does_not_flag_names_that_came_from_tool_output() -> None:
    """**우리가 만든 축 연결이 매번 걸리면 안 된다.**

    `cost_describe_spec`이 "성능은 perf_instance_profile로 보세요"를 붙인다 — 답변이
    그걸 옮긴 것을 세탁으로 세면 도구 간 포인터를 붙일 때마다 오탐이 난다.
    """
    verdict = check(
        "성능 특성은 perf_instance_profile로 보세요.",
        ["※ 성능 특성은 perf_instance_profile('gcp','n2-highmem-8') 로 보세요."],
        called_tools=["cost_describe_spec"],
        known_tools=KNOWN,
    )
    assert not [f for f in verdict.unsupported if f.kind == "attribution"]


def test_unknown_snake_case_words_are_not_flagged() -> None:
    """우리 도구가 아닌 것은 건드리지 않는다 — 밑줄 낀 단어는 흔하다."""
    assert not misattributed(
        "max_persistent_disks 값과 instance_type을 보세요.", called=[], known=KNOWN
    )


def test_check_is_unchanged_when_no_roster_is_given() -> None:
    """명단을 안 주면 예전 그대로 — 숫자·식별자 대조만 한다."""
    verdict = check("cap_allowed_values에서 봤습니다.", [], called_tools=[])
    assert not [f for f in verdict.unsupported if f.kind == "attribution"]


def test_trailing_comma_does_not_become_part_of_the_number() -> None:
    """**근거 있는 값이 근거 없다고 나오던 오탐** (실측 N3).

    "vCPU 8, x86_64"에서 `8,`를 토큰으로 잡으면 도구 출력의 `8`과 안 맞는다.
    """
    verdict = check("vCPU 8, 메모리 64 GiB입니다.", ["vCPU 8 · 메모리 64 GiB"])
    assert verdict.clean, [f.token for f in verdict.unsupported]


def test_thousands_separator_still_matches() -> None:
    """쉼표를 없앤 게 아니라 **끝에만** 못 오게 했다."""
    assert check("한도는 16,384입니다.", ["max=16384"]).clean


def test_attribution_counts_toward_checked() -> None:
    """세어 본 주장의 수에 포함돼야 비율이 말이 된다."""
    verdict = check(
        "cap_allowed_values 결과입니다.", [], called_tools=[], known_tools=KNOWN
    )
    assert verdict.checked >= 1
    assert not verdict.clean
