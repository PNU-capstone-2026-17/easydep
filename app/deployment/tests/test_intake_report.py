"""상류 산출물 점검 도구 — **원인을 가르는 장치**가 썩지 않게.

2026-07-28에 어댑터 경로로 시연했더니 손으로 쓴 예제보다 결과가 나빴는데(컴포넌트가
하나로 뭉치고 시크릿 저장소가 빠졌다), **입력이 픽스처라 어댑터 탓인지 입력 탓인지
가릴 수 없었다.** 이 도구가 그 구분을 낸다 — 있음 / 없음(입력에 없다) / 못 읽음
(우리 파서의 한계).

여기서 지키는 것은 **도구가 돌아간다**와 **세 갈래가 안 뭉개진다** 둘이다.
샘플의 내용은 검사하지 않는다 — 실물이 들어오면 바뀔 값이고, 그때 바뀌는 것이 맞다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.deployment.tools import intake_report

_CONTROL = Path(__file__).resolve().parent.parent / "appkb" / "samples" / "_synthetic-fixture"


def test_control_sample_exists_and_declares_itself_synthetic() -> None:
    """**대조군은 자기가 합성이라고 밝혀야 한다.**

    안 밝히면 다음 사람이 이걸로 시연하고 같은 지적을 다시 받는다 — 실제로 그랬다.
    """
    if not _CONTROL.is_dir():
        pytest.skip("대조군 샘플 없음")
    text = (_CONTROL / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "합성" in text and "실물이 아닙니다" in text
    assert "시연하지 말 것" in text


def test_report_runs_and_separates_the_three_states(capsys) -> None:
    """있음 / 없음 / 못 읽음이 **한 화면에 갈려 나온다.**"""
    if not _CONTROL.is_dir():
        pytest.skip("대조군 샘플 없음")
    intake_report.main([str(_CONTROL)])
    out = capsys.readouterr().out
    assert "[3] 신호" in out
    assert "있음" in out and "**없음**" in out
    # 어댑터가 짐작한 것은 삼키지 않고 화면에 낸다.
    assert "못 읽음·짐작" in out
    # 계약 위반도 그대로 — 반쯤 채운 사양을 통과시키면 뒤 단계가 그것을 사양으로 안다.
    assert "[4] RESOURCE_SPEC" in out


def test_plan_stands_instead_of_dying_on_the_contract(capsys) -> None:
    """**RESOURCE_SPEC을 설계 계약의 `requirements`에 통째로 꽂지 않는다.**

    둘은 같은 칸이 아니다 — `schemaVersion`·`regionAsWritten`은 안 내려간다. 원본을
    그대로 꽂았더니 `additionalProperties`에 걸려 계획이 **노드 0개**로 나왔고,
    화면에는 상류가 부실한 것처럼 보였다(2026-07-29). 원인을 가르는 도구가 원인을
    지어내고 있던 셈이다. 투영의 진실은 `appkb/easydep.py`의 `_REQ_FIELDS`이고,
    여기서는 그 어댑터를 태워야 한다.
    """
    if not _CONTROL.is_dir():
        pytest.skip("대조군 샘플 없음")
    intake_report.main([str(_CONTROL), "--plan"])
    out = capsys.readouterr().out
    assert "was unexpected" not in out, "설계 계약이 RESOURCE_SPEC 원본에 막혔다"
    assert "[5] 계획 — 노드 0" not in out
    assert "@startuml" in out
    # 계약 통과와 **계획에 닿는 것**은 다르다 — 그 차이를 화면이 말해야 한다.
    assert "계획으로 내려간 칸" in out and "안 내려간 칸" in out


def test_missing_provenance_is_called_out(tmp_path, capsys) -> None:
    """출처 없는 샘플은 **손으로 쓴 것과 구별되지 않는다** — 그 사실을 말한다."""
    (tmp_path / "design").mkdir()
    (tmp_path / "requirements").mkdir()
    intake_report.main([str(tmp_path)])
    assert "PROVENANCE.md가 없습니다" in capsys.readouterr().out
