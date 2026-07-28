"""표본 뽑기의 규율 — 측정이 목록의 앞부분만 보지 않도록.

같은 편향에 두 번 물렸다(`evaluation/sampling.py` 참고). 두 번째는 더 나빴다: 상한이
한쪽 표에만 걸려서 **코퍼스 비교가 코퍼스가 아니라 뽑기 차이를 재고 있었다.**
"""
from __future__ import annotations

from app.requirements.evaluation.sampling import even_sample


def test_a_subsample_spans_the_whole_list():
    """앞에서 자르지 않는다 — 그게 이 함수가 생긴 이유다."""
    picked = even_sample(list(range(20)), 5)
    assert picked[0] == 0
    assert picked[-1] >= 15, f"뒤쪽을 못 봤다: {picked}"
    assert len(picked) == 5


def test_the_unbound_side_is_returned_unchanged():
    """**이 성질이 있어야 한쪽만 고쳐도 두 표를 나란히 놓을 수 있다.**

    PURE 실행은 명세가 3~5개라 상한(5)이 걸리지 않는다. 그쪽 표본이 조금이라도 달라지면
    이미 끝난 PURE 측정을 다시 돌려야 한다 — 고르게 뽑기를 넣은 것이 그 표를 무르게 하면
    안 된다.
    """
    items = list(range(5))
    assert even_sample(items, 5) is items
    assert even_sample(items, 9) is items


def test_zero_means_everything_not_nothing():
    """예전 코드는 `payloads[:0]`이라 상한 0이 **아무것도 안 재는** 것이었다.

    `probe --limit 0`은 "제한 없음"으로 쓰이고 있어서, 두 경로가 같은 값에 반대 뜻을
    주고 있었다.
    """
    items = list(range(7))
    assert even_sample(items, 0) is items
    assert even_sample(items, -1) is items


def test_the_same_input_gives_the_same_sample():
    """무작위가 아니다 — 표본이 실행마다 달라지면 반복 측정이 반복이 아니게 된다."""
    items = list(range(17))
    assert even_sample(items, 5) == even_sample(items, 5)


def test_order_is_preserved():
    """뽑은 뒤에도 원래 순서다. 위치로 되짚을 수 있어야 한다."""
    picked = even_sample(list(range(30)), 6)
    assert picked == sorted(picked)


def test_it_works_on_the_shape_the_probe_actually_passes():
    """프로브가 넘기는 것은 `(spec_id, payload)` 튜플 목록이다."""
    payloads = [(f"UC{i}", {"n": i}) for i in range(13)]
    picked = even_sample(payloads, 5)
    assert len(picked) == 5
    assert picked[0][0] == "UC0"
    assert picked[-1][0] != "UC4", "앞 5개만 뽑고 있다"
