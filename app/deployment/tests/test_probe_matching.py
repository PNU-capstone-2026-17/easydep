"""프로브 **판정**의 단위 테스트 — 모델을 부르지 않는다.

하네스가 만든 실패는 진짜 실패를 가리므로, 판정 자체를 따로 지킨다.
여기 있는 것들은 전부 **실제로 오판을 만들 뻔했던** 문자들이다.
"""

from __future__ import annotations

from tools.agent_probe import Probe, _normalized


def test_normalizes_lookalike_hyphen() -> None:
    """모델이 `ap-northeast-2`를 U+2011(줄바꿈 없는 하이픈)로 썼다.

    리전 14곳을 정확히 답한 회차인데 하네스는 실패로 기록했다.
    """
    assert _normalized("ap‑northeast‑2") == "ap-northeast-2"


def test_normalizes_digit_separators() -> None:
    """`16,384`를 `16 384`로 쓰고 그 공백이 U+202F였다."""
    assert _normalized("16 384") == "16384"
    assert _normalized("16,384") == "16384"


def test_keeps_ordinary_punctuation() -> None:
    """숫자 사이가 아닌 쉼표는 살려야 한다 — 지우면 문장이 뭉개진다."""
    assert _normalized("가능, 불가") == "가능, 불가"


def test_want_any_matches_through_pretty_characters() -> None:
    """정답을 예쁜 문자로 써도 통과해야 한다 (판정 경로 전체)."""
    probe = Probe("t", "q", "why", want_any=("ap-northeast-2",))
    answer = "다음 리전에서 가능합니다: ap‑northeast‑2"
    assert not probe.failures(["cap_allowed_values"], answer)
