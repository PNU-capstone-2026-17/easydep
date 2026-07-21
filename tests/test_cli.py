"""CLI 로직 테스트 (start/resume_analysis 목킹, 입출력 주입)."""
from app.requirements import cli


def test_format_results_shows_id_type_text():
    # BERT 단독 분류: 출력은 id/유형/문장만(카테고리·근거·BERT 라인 없음).
    items = [
        {"id": "FR1", "text": "Log in", "type": "FR"},
        {"id": "NFR1", "text": "Be fast", "type": "NFR"},
    ]
    out = cli.format_results(items)
    assert "[FR1] FR" in out and "Log in" in out
    assert "[NFR1] NFR" in out and "Be fast" in out
    assert "BERT" not in out and "근거" not in out


def test_format_results_empty():
    assert "없습니다" in cli.format_results([])


def test_analyze_interactive_completes_directly(monkeypatch):
    monkeypatch.setattr(
        cli, "start_analysis",
        lambda reqs, tid: {"status": "completed", "requirements": [{"id": "R1"}]},
    )
    outputs = []
    payload = cli.analyze_interactive(
        ["req"], "tid", ask=lambda p: "", out=outputs.append
    )
    assert payload["status"] == "completed"


def test_analyze_interactive_clarification_loop(monkeypatch):
    calls = {"n": 0}

    def fake_start(reqs, tid):
        return {"status": "need_clarification", "questions": ["Who?"]}

    def fake_resume(answer, tid):
        calls["n"] += 1
        assert answer == "Shoppers"
        return {"status": "completed", "requirements": [{"id": "R1"}]}

    monkeypatch.setattr(cli, "start_analysis", fake_start)
    monkeypatch.setattr(cli, "resume_analysis", fake_resume)

    outputs = []
    payload = cli.analyze_interactive(
        ["I want a shop"], "tid", ask=lambda p: "Shoppers", out=outputs.append
    )

    assert payload["status"] == "completed"
    assert calls["n"] == 1
    assert any("Who?" in o for o in outputs)


def test_analyze_interactive_feedback_loop(monkeypatch):
    # need_feedback → 피드백 1회 제공 후 빈 입력으로 다음(완료)까지.
    seq = iter([
        {"status": "need_feedback", "phase": "use_cases", "feedback_summary": ["A", "B"]},
        {"status": "completed", "requirements": [{"id": "R1"}], "use_cases": [{"id": "UC1"}]},
    ])
    answers = iter(["merge A and B", ""])
    monkeypatch.setattr(cli, "start_analysis", lambda reqs, tid: next(seq))
    monkeypatch.setattr(cli, "resume_analysis", lambda answer, tid: next(seq))

    outputs = []
    payload = cli.analyze_interactive(["req"], "tid", ask=lambda p: next(answers), out=outputs.append)

    assert payload["status"] == "completed"
    assert any("피드백" in o for o in outputs)          # 피드백 프롬프트 표시
    assert any("use_cases" in str(o) or "A" in str(o) for o in outputs)


def test_main_no_requirements_returns_1(monkeypatch):
    # 인자 없음 + 비대화형(stdin) → 요구사항 0개 → 종료코드 1
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)  # 대화형처럼
    monkeypatch.setattr("builtins.input", lambda p="": "")  # 즉시 빈 줄 → 종료
    assert cli.main([]) == 1
