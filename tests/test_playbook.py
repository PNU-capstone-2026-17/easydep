"""플레이북의 규율 — 배우는 층이 잡음을 굳히지 않도록.

이 파일이 지키는 것은 "무엇을 배웠나"가 아니라 **무엇을 배우면 안 되는가**다:

  - 흔들리는 판정(LLM 검증자)에서 한 번 본 것으로 배우지 않는다.
  - 배운 것이 규칙인 척하지 않는다.
  - 꺼져 있을 때는 프롬프트가 **한 글자도** 달라지지 않는다(그래야 대조군이 된다).
  - 사람이 지울 수 있다.
"""
from __future__ import annotations

import json

import pytest

from app.requirements import prompts
from app.requirements.agent import playbook
from app.requirements.config import settings
from app.requirements.knowledge import rules

#: 검출기가 판정하는 규칙(결정론)과 검증자가 판정하는 규칙(흔들린다) 하나씩.
DETECTED = "spec.black-box-no-ui-mechanics"
JUDGED = "spec.no-scope-creep"


def _run_dir(tmp_path, run_id: str, dataset: str, issues: list[str], sentence: str):
    """실행 아티팩트 흉내 — 플레이북이 읽는 두 파일만."""
    run = tmp_path / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "manifest.json").write_text(
        json.dumps({"run_id": run_id, "dataset": dataset}), encoding="utf-8"
    )
    (run / "use_case_specs.json").write_text(json.dumps([{
        "use_case_id": "UC1",
        "trigger": "Member opens the catalogue.",
        "main_scenario": [{"step_number": 3, "sentence": sentence, "covered_req_ids": []}],
        "extensions": [],
        "issues": issues,
    }]), encoding="utf-8")
    return run


def _issue(rule_id: str, location: str = "step 3") -> str:
    """실제 지적 문구와 같은 꼴 — 꼬리표까지 붙는다(`rules.tag_of`)."""
    return f"{location}: 위반 {rules.tag_of(rule_id)}"


def test_harvest_pulls_the_sentence_we_actually_wrote(tmp_path):
    """반례는 지어내는 것이 아니라 **아티팩트에서 꺼낸 것**이다.

    "규칙 X에 주의하라"는 이미 규칙 목록에 있는 말이라 새 정보가 없다. 플레이북이 더할 수
    있는 것은 우리가 그 규칙을 **어떻게** 어겼는가뿐이다.
    """
    run = _run_dir(tmp_path, "run_a", "toystore", [_issue(DETECTED)],
                   "System shows the catalogue on the product screen.")
    observed = playbook.harvest(run)

    assert len(observed) == 1
    assert observed[0].rule_id == DETECTED
    assert observed[0].source == "detector"
    assert observed[0].sentence == "System shows the catalogue on the product screen."


def test_harvest_finds_the_sentence_when_the_location_is_in_prose(tmp_path):
    """검증자 지적은 위치를 산문 안에 쓴다 — 검출기와 꼴이 다르다.

    실제 실행에서 나온 꼴이다:
      검출기  `"step 3: UI 용어 [...] [<꼬리표>]"`
      검증자  `"[semantic] Remove the reference ... from step 3 [<꼬리표>]"`
    앞의 꼴만 읽으면 **검증자 쪽 반례가 통째로 비어 버린다**(실제로 그랬다).
    """
    issue = f"[semantic] Remove the reference to the opened database from step 3 {rules.tag_of(JUDGED)}"
    run = _run_dir(tmp_path, "run_a", "keepass", [issue],
                   "System creates the group inside the opened database.")
    assert playbook.harvest(run)[0].sentence == "System creates the group inside the opened database."


def test_an_unlocatable_issue_yields_no_example_rather_than_a_wrong_one(tmp_path):
    """스텝을 가리키지 않는 지적도 있다. 그때는 **세기만** 한다.

    억지로 문장을 하나 고르면 엉뚱한 문장이 반례로 굳는다 — 반례의 값어치는 그것이 실제로
    그 규칙을 어긴 문장이라는 데 있으므로, 틀린 반례는 없느니만 못하다.
    """
    issue = f"[semantic] Relocate the confirmation to a guarantee. {rules.tag_of(JUDGED)}"
    run = _run_dir(tmp_path, "run_a", "keepass", [issue], "System stores the entry.")
    observed = playbook.harvest(run)
    assert observed[0].sentence is None
    assert observed[0].rule_id == JUDGED  # 관찰 자체는 남는다


def test_a_single_noisy_verdict_is_not_learned(tmp_path):
    """**이 파일에서 가장 중요한 테스트.**

    LLM 판정은 도메인에 따라 78~90% 흔들린다(§7~§9). 한 실행에서 한 번 걸린 것은 판정
    잡음과 구별되지 않는다. 그걸 배우면 잡음이 다음 실행의 프롬프트가 되고, 스스로를
    재생산한다 — 안 배우느니만 못하다.
    """
    entries = playbook.curate([], playbook.harvest(
        _run_dir(tmp_path, "run_a", "toystore", [_issue(JUDGED)], "System books a courier.")
    ))
    assert entries and entries[0].rule_id == JUDGED
    assert not entries[0].qualifies, "검증자 판정 한 건으로 배웠다"
    assert playbook.render(entries, rules.WRITE_SPECIFICATIONS) == ""


def test_repetition_across_runs_earns_a_validator_lesson(tmp_path):
    """반복되면 자격을 얻는다 — 문턱은 **서로 다른 실행** 수로 센다."""
    entries: list[playbook.Entry] = []
    for i in range(playbook.MIN_RUNS_VALIDATOR):
        entries = playbook.curate(entries, playbook.harvest(
            _run_dir(tmp_path, f"run_{i}", "toystore", [_issue(JUDGED)],
                     f"System books a courier for order {i}.")
        ))
    assert entries[0].qualifies
    assert JUDGED in playbook.render(entries, rules.WRITE_SPECIFICATIONS)


def test_the_same_run_seen_twice_does_not_count_twice(tmp_path):
    """같은 실행을 다시 읽어도 문턱이 오르지 않는다.

    실행 목록을 두 번 훑는 일은 흔하다(캠페인이 이어서 돌 때). 그때마다 카운트가 오르면
    실행 하나가 문턱을 혼자 넘긴다.
    """
    run = _run_dir(tmp_path, "run_a", "toystore", [_issue(JUDGED)], "System books a courier.")
    entries = playbook.curate(playbook.curate([], playbook.harvest(run)),
                              playbook.harvest(run))
    assert entries[0].runs == 1
    assert not entries[0].qualifies


def test_deterministic_findings_clear_a_lower_bar(tmp_path):
    """검출기는 같은 산출물에 같은 답을 낸다 — 잡음이 아니므로 문턱이 낮다."""
    assert playbook.MIN_RUNS_DETECTOR < playbook.MIN_RUNS_VALIDATOR
    entries: list[playbook.Entry] = []
    for i in range(playbook.MIN_RUNS_DETECTOR):
        entries = playbook.curate(entries, playbook.harvest(
            _run_dir(tmp_path, f"run_{i}", "toystore", [_issue(DETECTED)],
                     f"System shows the catalogue on screen {i}.")
        ))
    assert entries[0].qualifies


def test_unknown_rules_are_never_learned(tmp_path):
    """지식베이스에 없는 규칙을 인용한 지적에서는 배우지 않는다.

    `semantic_status="ungrounded"`가 이미 버리는 것이지만, 여기까지 새어 들어오면 **근거
    없는 문장이 프롬프트에 굳는다.** 두 층에서 다 막는다.
    """
    run = _run_dir(tmp_path, "run_a", "toystore",
                   ["step 3: 위반 [spec.invented-rule · p.999]"], "System does something.")
    assert playbook.harvest(run) == []


def test_rendered_block_says_it_is_not_a_rule(tmp_path):
    """배운 것이 규칙인 척하면 `basis.py`가 그은 선이 프롬프트에서 무너진다.

    규칙 목록의 문장은 좌표를 댈 수 있다. 이 절의 문장은 우리 로그가 전부다 — 읽는 쪽이
    그 차이를 알아야 한다.
    """
    entries = []
    for i in range(playbook.MIN_RUNS_DETECTOR):
        entries = playbook.curate(entries, playbook.harvest(
            _run_dir(tmp_path, f"run_{i}", "toystore", [_issue(DETECTED)],
                     f"System shows the catalogue on screen {i}.")
        ))
    block = playbook.render(entries, rules.WRITE_SPECIFICATIONS)
    assert "Not rules" in block
    assert "run logs" in block


def test_entries_are_capped_so_the_block_cannot_swallow_the_prompt(tmp_path):
    """상한이 없으면 배운 것이 규칙을 밀어낸다.

    ACE가 말하는 context collapse는 "요약해서 잃는 것"이지만, 여기서는 **늘려서 묻는 것**이
    같은 값이다 — §9가 잰 것이 정확히 그것이다(규칙을 여럿 한 프롬프트에 넣으면 안정된
    판정이 0이 됐다).
    """
    entries = [
        playbook.Entry(rule_id=r.id, stage=rules.WRITE_SPECIFICATIONS,
                       detector_runs=[f"run_{i}" for i in range(5)])
        for r in rules.rules_for(rules.WRITE_SPECIFICATIONS, rules.DEFECT)
    ]
    assert len(entries) > playbook.MAX_ENTRIES_PER_STAGE, "상한을 시험하지 못하는 표본이다"
    block = playbook.render(entries, rules.WRITE_SPECIFICATIONS)
    listed = [line for line in block.splitlines() if line.startswith("- (")]
    assert len(listed) == playbook.MAX_ENTRIES_PER_STAGE


def test_disabled_playbook_leaves_the_prompt_byte_identical(monkeypatch):
    """**꺼짐이 곧 대조군이다.**

    켜고 끄는 것 말고 아무것도 달라지지 않아야, 측정된 차이를 플레이북 탓으로 돌릴 수
    있다. 이 저장소가 결론을 뒤집은 적이 있는 자리가 정확히 여기다(오염된 대조군).
    """
    monkeypatch.setattr(settings, "playbook_enabled", False)
    assert prompts.generation_system_for(rules.WRITE_SPECIFICATIONS) == prompts.SPEC_SYSTEM
    assert prompts.generation_system_for(rules.DRAW_DIAGRAM) == prompts.RELATIONSHIPS_SYSTEM


def test_enabled_playbook_appends_after_the_rules(tmp_path, monkeypatch):
    """켜면 규칙 **뒤에** 자기 절로 붙는다 — 규칙 사이에 끼지 않는다."""
    entries = []
    for i in range(playbook.MIN_RUNS_DETECTOR):
        entries = playbook.curate(entries, playbook.harvest(
            _run_dir(tmp_path, f"run_{i}", "toystore", [_issue(DETECTED)],
                     f"System shows the catalogue on screen {i}.")
        ))
    path = tmp_path / "playbook.json"
    playbook.save(path, entries)

    monkeypatch.setattr(settings, "playbook_enabled", True)
    monkeypatch.setattr(settings, "playbook_path", str(path))
    prompt = prompts.generation_system_for(rules.WRITE_SPECIFICATIONS)

    assert prompt.startswith(prompts.SPEC_SYSTEM), "규칙 판이 그대로 앞에 있어야 한다"
    assert prompt.index("[RULES YOU FOLLOW]") < prompt.index("[WHAT WE GOT WRONG BEFORE]")


def test_a_missing_playbook_file_does_not_stop_the_run(monkeypatch, tmp_path):
    """배우는 층은 있으면 좋은 것이지 실행을 세울 이유가 아니다."""
    monkeypatch.setattr(settings, "playbook_enabled", True)
    monkeypatch.setattr(settings, "playbook_path", str(tmp_path / "nope.json"))
    assert prompts.generation_system_for(rules.WRITE_SPECIFICATIONS) == prompts.SPEC_SYSTEM


def test_saved_playbook_round_trips_and_is_human_editable(tmp_path):
    """사람이 지울 수 있어야 한다 — 배운 것이 틀렸을 때 되돌리는 길이 그것뿐이다."""
    entries = playbook.curate([], playbook.harvest(
        _run_dir(tmp_path, "run_a", "toystore", [_issue(DETECTED)], "System uses a button.")
    ))
    path = tmp_path / "pb.json"
    playbook.save(path, entries)

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["note"], "이 파일이 무엇인지 파일 자체가 말해야 한다"
    assert playbook.load(path)[0].as_dict() == entries[0].as_dict()


@pytest.mark.parametrize("missing", ["manifest.json", "use_case_specs.json"])
def test_harvest_survives_a_partial_run(tmp_path, missing):
    """중단된 캠페인은 반쪽짜리 실행 디렉터리를 남긴다 — 그걸로 죽지 않는다."""
    run = _run_dir(tmp_path, "run_a", "toystore", [_issue(DETECTED)], "System uses a button.")
    (run / missing).unlink()
    playbook.harvest(run)  # 예외가 없으면 통과


# ---------------------------------------------------------------------------
# 사용자 피드백에서 배우기 — 과제 목표 1의 비어 있던 절반
# ---------------------------------------------------------------------------
class _Intent:
    """`FeedbackIntent`처럼 생긴 것. 스키마를 끌어오지 않으려고 최소만 흉내낸다."""

    def __init__(self, stage="specs", scope="broad", instruction="Keep steps shorter."):
        self.stage, self.scope, self.instruction = stage, scope, instruction


def _learn(intent, runs) -> list[playbook.FeedbackLesson]:
    lessons: list[playbook.FeedbackLesson] = []
    for run_id in runs:
        lessons = playbook.observe_feedback(lessons, intent, run_id, dataset="toystore")
    return lessons


def test_feedback_repeated_across_runs_becomes_a_lesson():
    """**과제 문구가 요구하는 고리.** 같은 요구를 두 번 받으면 다음 실행이 읽는다.

    지금까지 피드백은 산출물만 고치고 사라졌다 — 같은 지적을 세 번 받아도 네 번째 실행이
    같은 것을 냈다.
    """
    lessons = _learn(_Intent(), ["run_a", "run_b"])
    assert lessons[0].times == 2
    assert lessons[0].qualifies
    block = playbook.render([], rules.WRITE_SPECIFICATIONS, lessons)
    assert "Keep steps shorter." in block


def test_one_off_feedback_is_not_a_standing_instruction():
    """한 번의 지시는 그 산출물에 대한 주문일 수 있다 — 모든 다음 실행에 물리지 않는다."""
    lessons = _learn(_Intent(), ["run_a"])
    assert not lessons[0].qualifies
    assert playbook.render([], rules.WRITE_SPECIFICATIONS, lessons) == ""


def test_local_edits_are_never_learned():
    """`local`은 대상 id를 들고 오는 산출물 수술이지 정책이 아니다.

    "UC3 이름을 바꿔라"를 플레이북에 얹으면 다음 실행이 없는 UC3를 찾는다. 범위가 곧
    일반화 가능성의 신호다.
    """
    intent = _Intent(scope="local", instruction="Rename UC3 to Checkout.")
    assert _learn(intent, ["run_a", "run_b", "run_c"]) == []


def test_the_same_run_asking_twice_counts_once():
    """문턱은 **서로 다른 실행** 수로 센다 — 한 실행에서 두 번 고쳐도 한 번이다."""
    lessons = _learn(_Intent(), ["run_a", "run_a"])
    assert lessons[0].times == 1


def test_wording_differences_do_not_split_the_same_request():
    """공백·대소문자가 달라도 같은 요구다. 저장은 원문으로 한다."""
    lessons: list[playbook.FeedbackLesson] = []
    lessons = playbook.observe_feedback(lessons, _Intent(instruction="Keep steps shorter."), "r1")
    lessons = playbook.observe_feedback(
        lessons, _Intent(instruction="  keep   steps SHORTER.  "), "r2"
    )
    assert len(lessons) == 1
    assert lessons[0].times == 2
    assert lessons[0].instruction == "Keep steps shorter."   # 첫 원문을 유지한다


def test_user_requests_render_apart_from_rule_violations():
    """규칙 위반과 **다른 절**로 나간다.

    앞엣것은 "우리가 틀렸다"이고 뒤엣것은 "사람이 이렇게 원한다"라서 근거의 성격이 다르다.
    한 목록에 섞으면 사용자 요구가 규칙처럼 읽힌다.
    """
    block = playbook.render([], rules.WRITE_SPECIFICATIONS, _learn(_Intent(), ["r1", "r2"]))
    assert "[WHAT USERS KEPT ASKING FOR]" in block
    assert "Not rules" in block
    # 규칙과 충돌하면 규칙이 이긴다는 것을 프롬프트가 말해야 한다.
    assert "follow the rule" in block


def test_saving_rules_does_not_wipe_learned_feedback(tmp_path):
    """규칙 쪽만 갱신하는 호출이 **사용자 피드백을 조용히 지우면** 안 된다."""
    path = tmp_path / "pb.json"
    playbook.save(path, [], _learn(_Intent(), ["r1", "r2"]))
    playbook.save(path, [])              # lessons 를 안 준다
    assert len(playbook.load_feedback(path)) == 1


def test_recording_feedback_never_breaks_the_request(tmp_path):
    """요청 경로에서 불린다 — 배우기 실패가 사용자의 요청을 실패시키면 안 된다."""
    playbook.record_feedback(tmp_path / "no" / "such" / "dir" / "x.json",
                             _Intent(), "run_a")   # 예외가 없으면 통과
