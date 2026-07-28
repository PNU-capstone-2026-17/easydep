"""산문에서 뽑은 **단위**가 그 숫자의 단위인가.

확인된 데이터 결함이었다. `_unit_of`가 매칭된 블록의 **첫 번째** 단위 토큰을 그냥
돌려줘서 셋이 실제로 틀렸다:

    BacktrackWindow  259200  → `hours`   실제 seconds  (**3,600배**)
    Iops               1000  → `second`  실제 IOPS
    MaximumLength  20971520  → `MB`      실제 characters

첫 번째가 특히 나쁘다 — 259,200시간은 29.6년이라 값 자체로도 말이 안 되는데,
`cap_check_value`가 "500 second는 최소 1000 second를 벗어남" 같은 문장을 만들었다.

해결 순서: **속성 이름 → `in X` 선언 → 블록의 단위**. 셋 다 확신을 못 주면
**담지 않는다** — 틀린 단위는 침묵보다 나쁘다.
"""

from __future__ import annotations

import pytest

from app.deployment.capacitykb.prose import _unit_from_name, _unit_of, extract_ranges

# --- 이름이 단위를 말할 때 ---

@pytest.mark.parametrize(
    "prop,want",
    [
        ("TimeoutInMillis", "milliseconds"),
        ("ReceiveMessageWaitTimeSeconds", "seconds"),
        ("HealthCheckConfig/IntervalSeconds", "seconds"),
        ("Iops", "IOPS"),
        ("Tiering/Days", "days"),
        ("BacktrackWindow", None),   # 이름이 말해주지 않는다
        ("MaximumLength", None),
    ],
)
def test_unit_from_property_name(prop: str, want: str | None) -> None:
    """이름은 산문보다 강한 근거다 — 한 문단은 단위를 여럿 섞지만 이름은 하나다."""
    assert _unit_from_name(prop) == want


# --- 실제로 틀렸던 세 건 ---

def test_conversion_in_parentheses_is_not_the_unit() -> None:
    """`(72 hours)`는 259,200을 사람이 읽기 쉽게 바꿔 적은 것이지 단위가 아니다."""
    got = _unit_of(
        "from 0 to 259,200 (72 hours)",
        "BacktrackWindow",
        "The target backtrack window, in seconds. … from 0 to 259,200 (72 hours).",
    )
    assert got == "seconds"


def test_per_second_is_not_the_unit_of_iops() -> None:
    """"operations per second (IOPS)"에서 단위는 IOPS다."""
    got = _unit_of(
        "operations per second (IOPS)",
        "Iops",
        "The number of I/O operations per second (IOPS) provisioned.",
    )
    assert got == "IOPS"


def test_unknown_unit_stays_empty() -> None:
    """`characters`는 우리 단위 목록에 없다 — 그럴 땐 **담지 않는다.**

    예전엔 근처의 `MB`를 집었다. 20,971,520 characters를 "20971520 MB"라고
    적으면 확신에 찬 오답이 된다.
    """
    got = _unit_of(
        "between 1 and 20971520",
        "UserSetting/MaximumLength",
        "Specifies the number of characters that can be copied … (20 MB) …",
    )
    assert got is None


# --- 원래 맞던 것이 안 깨졌는지 ---

@pytest.mark.parametrize(
    "block,prop,full,want",
    [
        ("``1 - 65,536`` GiB", "Size", "+ gp3: ``1 - 65,536`` GiB", "GiB"),
        (
            "maximum allowed value is 900 seconds",
            "Timeout",
            "The amount of time (in seconds) that Lambda allows … 900 seconds.",
            "seconds",
        ),
    ],
)
def test_correct_units_are_kept(block, prop, full, want) -> None:
    assert _unit_of(block, prop, full) == want


def test_mixed_units_in_block_are_refused() -> None:
    """한 블록에 다른 단위가 섞여 있으면 어느 것인지 알 수 없다 — 비운다."""
    assert _unit_of("between 5 minutes and 300 seconds", None, None) is None


def test_extract_ranges_passes_property_name() -> None:
    """전체 경로에서도 이름 근거가 살아 있어야 한다."""
    found = extract_ranges(
        "The target backtrack window, in seconds. Constraints: If specified, "
        "this value must be set to a number from 0 to 259,200 (72 hours).",
        "BacktrackWindow",
    )
    assert found, "범위를 못 뽑았다"
    assert {e.unit for e in found} == {"seconds"}
