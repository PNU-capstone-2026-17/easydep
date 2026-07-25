"""출처 세탁을 잡는다 — **부르지도 않은 우리 도구를 출처로 대는 것**.

조사 24건 중 4건에서 KB 보증과 모델 기억이 안 갈렸고, 가장 위험한 형태가 이것이다.
실측에서 모델이 GPU 사양 표를 지어내고 *"cap_allowed_values 지식베이스에서 조회한
결과"*라고 적었다(`g5g`를 AMD라고 했다 — NVIDIA다). 그 도구는 그 턴에 호출되지 않았다.

**`basis`·소스 핀·교차 확인이 통째로 무의미해지는 실패다.** 숫자 대조와 달리 이건
오탐이 거의 없다 — 우리 도구 이름이 답변에 우연히 나올 수는 없다.
"""

from __future__ import annotations

from tools.claim_check import check, leaked_internals, misattributed

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


def test_leak_counts_called_tools_too_unlike_misattribution() -> None:
    """**출처 세탁과 내부 용어 누출은 다른 결함이다.**

    `misattributed`는 부르지도 않은 도구를 출처로 댄 것만 센다 — 부른 도구를
    언급하는 것은 출처를 밝히는 정당한 행위라 거른다. 스타일 규칙은 반대다:
    지시문이 "도구/함수 이름을 답변에 쓰지 마세요"라고 하므로 **부른 도구를
    언급해도 위반**이다. 사용자는 우리 함수 이름을 알 필요가 없다.
    """
    known = frozenset({"cost_describe_spec", "cap_resolve_region"})
    answer = "cost_describe_spec 도구로 조회한 결과 4 GiB입니다."

    # 불렀으므로 세탁은 아니다.
    assert not misattributed(answer, ["cost_describe_spec"], known)
    # 그래도 이름이 답변에 있으므로 누출이다.
    assert [f.token for f in leaked_internals(answer, known)] == [
        "cost_describe_spec"
    ]


def test_leak_catches_prospective_mentions_that_are_not_laundering() -> None:
    """**실측에서 세탁으로 잘못 이름 붙던 것.**

    RS1(필수 4칸이 다 빈 질의)은 도구를 하나도 안 부르고 되묻는 것이 옳은데,
    그 답변이 "리전 코드는 `cap_resolve_region` 도구로 확인해야 합니다"라고
    적었다. 앞으로 쓰겠다는 **예고**이지 출처를 사칭한 것이 아니다 —
    세탁으로 세면 심각도를 잘못 읽는다. 누출로는 맞게 잡힌다.
    """
    known = frozenset({"cap_resolve_region"})
    answer = "리전 코드는 cap_resolve_region 도구로 확인해야 합니다."
    assert [f.token for f in leaked_internals(answer, known)] == [
        "cap_resolve_region"
    ]


def test_leak_ignores_prose_that_merely_describes_the_act() -> None:
    """지시문이 요구하는 표현("메커니즘이 아니라 행위")은 걸리면 안 된다 —
    걸리면 옳게 쓴 답변을 벌하게 된다."""
    known = frozenset({"kb_creation_order", "cost_describe_spec"})
    clean = "의존성 지식베이스에서 조회한 결과, vNet(가상 네트워크)이 먼저 필요합니다."
    assert leaked_internals(clean, known) == []


def test_leak_catches_internal_id_prefixes() -> None:
    """`core::vNet`은 답변에서 풀어 써야 한다 — 지시문의 두 번째 스타일 규칙."""
    found = leaked_internals("core::vNet은 aws::AWS::EC2::VPC입니다.", frozenset())
    assert {f.token for f in found} == {"core::", "aws::"}
