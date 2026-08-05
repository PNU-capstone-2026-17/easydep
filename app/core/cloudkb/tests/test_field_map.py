"""`details` 분류표의 **전수성** — 판단하지 않은 칸이 있으면 실패한다.

계획서(`archive/perfkb-field-axis-plan-2026-07-29.md`)가 없다고 진단한 장치가 정확히
이것이다: *"인벤토리에 있는 키가 분류표에 없으면 빌드가 죽는다."*

여기 있는 검사는 **핀 박은 키 목록**(`details_keys.json`, 2026-07-29 실측)만 본다.
살아 있는 덤프(34MB)와 대조하는 것은 빌드가 한다 — 단위 테스트가 34MB를 읽으면
아무도 안 돌린다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.perfkb.parsers.field_map import (
    ADOPT,
    CLASSES,
    FIELD_MAP,
    UNTRACKED_PROVIDERS,
    not_adopted,
    summarize,
    unknown_keys,
)

_PINNED = Path(__file__).resolve().parents[1] / "perfkb" / "parsers" / "details_keys.json"


def _pinned() -> dict:
    return json.loads(_PINNED.read_text(encoding="utf-8"))


@pytest.mark.parametrize("provider", sorted(FIELD_MAP))
def test_every_measured_key_has_a_decision(provider: str) -> None:
    """실측된 키 전부에 판정이 있다 — **이것이 P1의 요점이다.**"""
    keys = _pinned()["providers"][provider]["keys"]
    missing = unknown_keys(provider, keys)
    assert not missing, (
        f"{provider}: 분류표에 없는 키 {len(missing)}개 — {missing}\n"
        "판단이 바뀌는 것은 괜찮지만 판단하지 않은 칸이 있으면 안 됩니다."
    )


@pytest.mark.parametrize("provider", sorted(FIELD_MAP))
def test_no_decision_for_a_key_that_does_not_exist(provider: str) -> None:
    """반대 방향도 본다 — 사라진 키에 대한 판정이 남아 있으면 표가 거짓말을 한다."""
    keys = set(_pinned()["providers"][provider]["keys"])
    stale = sorted(k for k in FIELD_MAP[provider] if k not in keys)
    assert not stale, f"{provider}: 실측에 없는 키를 분류하고 있다 — {stale}"


def test_every_decision_carries_a_reason() -> None:
    """사유를 못 쓰면 분류가 아니라 방치다(위협 T6)."""
    for provider, table in FIELD_MAP.items():
        for key, decision in table.items():
            assert decision.why.strip(), f"{provider}.{key}"
            assert decision.cls in CLASSES


def test_adopted_fields_exist_in_the_schema() -> None:
    """`adopt`가 가리키는 칸이 실재하는가 — 오타면 값이 조용히 안 실린다."""
    from app.core.cloudkb.perfkb.dataset import schema

    fields = set(schema()["$defs"]["spec"]["properties"])
    for provider, table in FIELD_MAP.items():
        for key, decision in table.items():
            if decision.cls != ADOPT:
                continue
            assert decision.field in fields, f"{provider}.{key} → {decision.field}"


def test_duplicate_decisions_name_what_they_duplicate() -> None:
    """"중복"은 **어디와** 겹치는지 적어야 판정이다(위협 T2)."""
    for provider, table in FIELD_MAP.items():
        for key, decision in table.items():
            if decision.cls == "duplicate":
                assert decision.against, f"{provider}.{key}"


def test_summary_counts_cover_the_whole_table() -> None:
    for provider, table in FIELD_MAP.items():
        assert sum(summarize(provider).values()) == len(table)


def test_not_adopted_is_everything_but_adopt() -> None:
    for provider, table in FIELD_MAP.items():
        adopted = [k for k, d in table.items() if d.cls == ADOPT]
        assert len(not_adopted(provider)) == len(table) - len(adopted)


def test_untracked_providers_are_declared_with_evidence() -> None:
    """분류표가 없는 프로바이더도 **왜 없는지**가 있어야 한다.

    "추적하지 않는다"는 답과 "키가 하나뿐이라 담을 것이 없다"는 답은 다르다.
    """
    measured = set(_pinned()["providers"])
    for provider, why in UNTRACKED_PROVIDERS.items():
        assert provider not in measured or provider in FIELD_MAP
        assert any(ch.isdigit() for ch in why), f"{provider}: 실측 근거가 없다"


def test_blocked_keys_say_why_they_are_blocked() -> None:
    """못 읽는 자리도 세어서 남긴다 — 포화의 조건은 '멈춘 이유'다."""
    blocked = _pinned()["blocked"]
    assert blocked, "중첩 2단이 하나도 없다면 인벤토리가 덜 돈 것이다"
    assert all(reason.strip() for reason in blocked.values())
