"""평가 기계장치의 규율 — 채점의 눈금과 비교의 정직성.

`tests/test_knowledge.py`가 규칙의 규율을 지키고, 여기서는 **그 규칙을 세는 도구**를 지킨다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.requirements.evaluation import scorecard as sc
from app.requirements.evaluation import seeded
from app.requirements.knowledge import rules
from evaluation.implementation import inspect_repository

_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# 1. 심어 둔 결함 — 검사기의 눈금
# ---------------------------------------------------------------------------
def test_every_seeded_defect_is_detected():
    """못 잡는 규칙이 하나 있으면, 그 규칙에 대한 모든 "0건"이 근거가 없다."""
    report = seeded.detection_report()
    missed = [c["rule_id"] for c in report["cases"] if not c["detected"]]
    assert missed == [], f"심어 둔 결함을 못 잡는다: {missed}"
    assert report["detected"] == report["total"]


def test_the_clean_control_produces_no_findings():
    """대조군에서 나오는 지적은 전부 오탐이고, 오탐은 실행 비교를 오염시킨다."""
    report = seeded.detection_report()
    assert report["false_positives"] == []


def test_each_seed_trips_only_its_own_rule():
    """심은 문장이 두 규칙을 동시에 어기면 그 케이스로는 눈금을 못 읽는다."""
    for case in seeded.detection_report()["cases"]:
        assert case["also_flagged"] == [], f"{case['rule_id']}: {case['also_flagged']}도 함께 걸렸다"


def test_every_detector_rule_has_a_seed():
    """검출기가 있는데 심어 두지 않은 규칙은 눈금이 살아 있는지 아무도 모른다."""
    assert seeded.detection_report()["unseeded_detector_rules"] == []


def test_the_seeded_payload_matches_what_the_pipeline_sends():
    """눈금이 파이프라인과 다른 것을 보여 주면 그 수치는 파이프라인에 대한 말이 아니다.

    `seeded.py`는 자격증명 없이 돌아야 해서 `step3`을 import할 수 없다(그 모듈이 설정·LLM
    스택을 끌고 온다). 그래서 모양이 같은지는 import가 아니라 여기서 지킨다.
    """
    from app.requirements.agent.steps import step3_specifications as s3

    assert seeded._REVIEWED_FIELDS == s3._REVIEWED_FIELDS
    assert seeded._spec_payload(seeded.CLEAN) == s3.spec_review_payload(
        seeded.CLEAN, seeded._REQUIREMENTS
    )


def test_seeded_check_runs_without_credentials():
    """이건 CI 게이트다 — API 키도 그래프도 없이 돌아야 한다.

    `scorecard`가 `agent.compare`를 함수 안에서 import하는 이유가 이것이다. 상단으로
    올리면 LLM 스택 전체가 딸려 오고, 자격증명 없는 환경에서 import만으로 죽는다.
    """
    probe = (
        "import app.requirements.evaluation.seeded as s, sys; "
        "print('config' if 'app.requirements.config' in sys.modules else 'clean'); "
        "print(s.detection_report()['detected'])"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out[0] == "clean", "설정(→LLM 스택)을 끌고 왔다"
    assert out[1] == str(len(seeded.SEEDED))


# ---------------------------------------------------------------------------
# 2. 규칙별 채점
# ---------------------------------------------------------------------------
def test_rule_is_read_back_from_the_tag_we_wrote():
    rule = rules.rule("spec.no-scope-creep")
    issue = f"[semantic] drop the invented capability {rule.tag}"
    assert sc.rule_of(issue) == "spec.no-scope-creep"


def test_an_issue_without_a_tag_is_counted_not_dropped():
    """조용히 버리면 규칙별 합과 전체 합이 어긋나는데 아무 표시가 없다."""
    assert sc.rule_of("[semantic] something we cannot attribute") == sc.UNTAGGED


def _state_with(issues: list[str]) -> dict:
    return {
        "classified": [{"id": "FR1", "text": "x", "type": "FR"}],
        "actors": [],
        "use_cases": [{"id": "UC1", "name": "n", "requirement_ids": ["FR1"], "nfr_ids": []}],
        "use_case_specs": [dict(seeded.CLEAN, use_case_id="UC1", issues=issues,
                                semantic_status="ok", repair_stopped="clean")],
        "relationships": {},
        "model_review": {"issues": [], "semantic_status": "ok", "unexamined_rules": []},
    }


def test_scorecard_counts_recorded_issues_per_rule():
    tag = rules.rule("spec.no-scope-creep").tag
    card = sc.scorecard(_state_with([f"[semantic] a {tag}", f"[semantic] b {tag}"]))

    assert card["as_recorded"] == {"spec.no-scope-creep": 2}
    # 대조군 명세라 오늘의 검출기로는 아무것도 안 나온다 — 두 수의 뜻이 다르다는 증거.
    assert card["static_now"] == {}
    assert card["statuses"]["specs"] == {"ok": 1}
    assert card["totals"]["n_use_cases"] == 1


def test_scorecard_reads_static_defects_with_todays_detectors():
    """`static_now`는 저장된 issues가 아니라 **다시 검증한** 결과다."""
    seeded_case = next(c for c in seeded.SEEDED
                       if c.rule_id == "spec.black-box-no-ui-mechanics")
    state = _state_with([])
    state["use_case_specs"] = [dict(seeded_case.spec, use_case_id="UC1", issues=[])]

    card = sc.scorecard(state)
    assert card["static_now"] == {"spec.black-box-no-ui-mechanics": 1}
    assert card["as_recorded"] == {}      # 그 실행은 기록하지 않았다


# ---------------------------------------------------------------------------
# 3. 비교
# ---------------------------------------------------------------------------
def test_diff_shows_what_got_better_and_what_got_worse_separately():
    """스칼라 합으로는 상쇄돼 보이지 않는 것을 규칙별로 드러낸다."""
    before = {
        "static_now": {"spec.no-branching-in-a-step": 3},
        "as_recorded": {"spec.no-scope-creep": 4, "spec.no-hidden-branching": 1},
        "statuses": {"specs": {"ok": 2}},
        "totals": {"spec_validation_issues": 8},
    }
    after = {
        "static_now": {"spec.no-branching-in-a-step": 3},
        "as_recorded": {"spec.no-scope-creep": 1, "spec.no-hidden-branching": 4},
        "statuses": {"specs": {"ok": 2}},
        "totals": {"spec_validation_issues": 8},
    }
    result = sc.diff(before, after)

    # 합은 5 → 5로 그대로다. 규칙별로 보면 하나는 좋아지고 하나는 나빠졌다.
    assert result["as_recorded"] == {"spec.no-scope-creep": -3, "spec.no-hidden-branching": 3}
    assert result["static_now"] == {}        # 안 바뀐 것은 싣지 않는다
    assert result["totals"] == {}
    assert result["warnings"] == []


def test_diff_refuses_to_compare_silently_when_the_validator_was_off():
    """의미 검증이 꺼진 실행의 `as_recorded`는 켜진 실행과 비교할 수 없다."""
    off = {
        "static_now": {}, "as_recorded": {}, "totals": {},
        "statuses": {"specs": {"disabled": 3}},
    }
    on = {
        "static_now": {}, "as_recorded": {"spec.no-scope-creep": 2}, "totals": {},
        "statuses": {"specs": {"ok": 3}},
    }
    result = sc.diff(off, on)
    assert len(result["warnings"]) == 1
    assert "before" in result["warnings"][0]


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------
def test_cli_seeded_exits_nonzero_when_a_gauge_is_dead(monkeypatch, capsys):
    from app.requirements.evaluation import __main__ as cli

    monkeypatch.setattr(
        seeded, "detection_report",
        lambda: {"cases": [{"rule_id": "r", "seeded": "s", "detected": False,
                            "also_flagged": []}],
                 "detected": 0, "total": 1, "false_positives": [],
                 "unseeded_detector_rules": []},
    )
    assert cli.main(["seeded"]) == 1
    assert "MISS" in capsys.readouterr().out


def test_cli_diff_reads_two_scorecards(tmp_path, capsys):
    from app.requirements.evaluation import __main__ as cli

    card = {"static_now": {}, "as_recorded": {"spec.no-scope-creep": 1},
            "totals": {}, "statuses": {"specs": {"ok": 1}}}
    before = tmp_path / "a.json"
    after = tmp_path / "b.json"
    before.write_text(json.dumps(card), encoding="utf-8")
    after.write_text(json.dumps({**card, "as_recorded": {}}), encoding="utf-8")

    assert cli.main(["diff", str(before), str(after)]) == 0
    assert "-1" in capsys.readouterr().out
def test_markdown_wrappers_make_generated_repository_incomplete(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text(
        "## src/App.java\nclass App {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "AppTest.java").write_text("class AppTest {}\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "main.tf").write_text("terraform {}\n", encoding="utf-8")

    result = inspect_repository(tmp_path)

    assert result["checks"]["generated_files_clean"] is False
    assert result["markdownContaminatedFiles"] == ["src/App.java"]
    assert result["implementationComplete"] is False


def test_kotlin_sources_count_as_implementation_and_tests(tmp_path):
    (tmp_path / "src" / "main").mkdir(parents=True)
    (tmp_path / "src" / "test").mkdir(parents=True)
    (tmp_path / "src" / "main" / "App.kt").write_text(
        "fun main() = println(\"ok\")\n", encoding="utf-8"
    )
    (tmp_path / "src" / "test" / "AppTest.kt").write_text(
        "class AppTest\n", encoding="utf-8"
    )
    (tmp_path / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "main.tf").write_text("terraform {}\n", encoding="utf-8")

    result = inspect_repository(tmp_path)

    assert result["checks"]["source_present"] is True
    assert result["checks"]["test_present"] is True
    assert result["implementationComplete"] is True
