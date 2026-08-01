"""스펙 하한의 층 셋 — **근거 없는 계수가 다시 생기지 않게.**

여기서 지키는 진짜 불변식은 마지막 것이다: *규모 신호가 스펙 하한을 움직이지
않는다.* 예전 코드는 `users <= 500 → (2,4) else (4,8)`이었고, 그 상수 셋 어디에도
출처가 없었다 — `sizingkb`가 KB에서 명시적으로 배제한 바로 그 형태가 코드에
살아 있었던 것이다. 지우는 것만으로는 재발을 막지 못하므로 검사로 굳힌다.
"""

from __future__ import annotations

from app.core.cloudkb.nim_agent import sizing_floor


def test_no_floor_when_nothing_states_one() -> None:
    """일반 VM에는 **해당하는 공식 하한이 없다** — 그것이 정상이다."""
    floor = sizing_floor.resolve({})
    assert not floor.decided
    assert floor.layer == sizing_floor.LAYER_NONE
    assert floor.vcpu == 0 and floor.mem_gib == 0


def test_k8s_node_has_a_sourced_floor() -> None:
    """층 1 — tumblebug이 적어 둔 `k8s-node` 하한(vCPU 2 · 4 GiB)."""
    floor = sizing_floor.resolve({}, scope="k8s-node")
    assert floor.decided
    assert floor.layers == (sizing_floor.LAYER_KB,)
    assert floor.vcpu >= 2 and floor.mem_gib >= 4
    assert "tumblebug" in floor.why


def test_stated_floor_is_recorded_as_a_claim() -> None:
    """층 2 — 사용자·설계자 진술. `stateless`와 같은 성격이라 그렇게 적힌다."""
    floor = sizing_floor.resolve({"minVCpu": 8, "minMemoryGiB": 32})
    assert floor.layers == (sizing_floor.LAYER_STATED,)
    assert (floor.vcpu, floor.mem_gib) == (8, 32)
    assert "claim" in floor.why and "not a verified fact" in floor.why


def test_layers_compose_by_taking_the_larger() -> None:
    """둘 다 있으면 **둘 다 만족해야** 하므로 축마다 최대를 취한다."""
    floor = sizing_floor.resolve({"minVCpu": 1, "minMemoryGiB": 16}, scope="k8s-node")
    assert floor.layers == (sizing_floor.LAYER_KB, sizing_floor.LAYER_STATED)
    assert floor.vcpu == 2      # KB 하한이 사용자 값을 이긴다
    assert floor.mem_gib == 16  # 사용자 값이 KB 하한보다 크다


def test_bad_values_are_not_floors() -> None:
    """0·음수·글자는 하한이 아니다 — 조용히 통과시키면 필터가 무의미해진다."""
    for bad in ({"minVCpu": 0}, {"minVCpu": -2}, {"minVCpu": "많이"}, {"minVCpu": None}):
        assert not sizing_floor.resolve(bad).decided


def test_scale_signal_does_not_move_the_floor() -> None:
    """**이 저장소가 배제한 변환이 코드로 돌아오지 않는다.**

    동시 사용자 5명과 50,000명이 같은 하한을 낸다. 다르게 하려면 그 변환을 적어 둔
    소스가 있어야 하고, 조사 결론은 **없다**였다
    (`archive/bundle-sizing-research-2026-07-22.md`).
    """
    unit = {"unit": "concurrentUsers"}
    small = sizing_floor.resolve({"scale": {"value": 5, **unit}})
    huge = sizing_floor.resolve({"scale": {"value": 50_000, **unit}})
    assert (small.vcpu, small.mem_gib) == (huge.vcpu, huge.mem_gib) == (0, 0)

    rps = sizing_floor.resolve({"scale": {"value": 9_000,
                                          "unit": "requestsPerSecond"}})
    assert not rps.decided


def test_scale_signal_is_used_as_a_reason_to_ask() -> None:
    """규모는 하한을 정하지 못하지만 **되묻기의 근거**로는 쓰인다(층 3)."""
    note = sizing_floor.undecided_note(
        {"scale": {"value": 3000, "unit": "concurrentUsers"}}, [], [])
    assert "3000 concurrent users" in note
    assert "cannot be converted into a spec" in note
    assert sizing_floor.VCPU_FIELD in note and sizing_floor.MEM_FIELD in note


def test_undecided_note_refuses_to_call_the_cheapest_enough() -> None:
    """최저가를 고르는 것이 **중립이 아니라 더 센 주장**이라는 문장이 남아 있어야 한다."""
    note = sizing_floor.undecided_note({}, [], [])
    assert "cheapest" in note and "not be a neutral choice" in note


def test_no_hidden_coefficient_in_the_compose_path() -> None:
    """설계→계획 경로에 **규모→스펙 상수가 없다.**

    문자열 검사인 것이 요점이다 — 값을 계산해서 비교하면 상수가 다른 모양으로
    돌아와도 통과한다. 여기서 막는 것은 "누군가 다시 숫자를 적어 넣는 일"이다.
    """
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "nim_agent" / "design_tools.py"
    text = source.read_text(encoding="utf-8")
    hits = re.findall(r"scale.{0,120}", text, re.DOTALL)
    for hit in hits:
        assert not re.search(r"<=?\s*\d|>=?\s*\d", hit), (
            "규모 신호를 숫자와 비교하고 있다 — 그 변환에는 출처가 없다:\n" + hit
        )
