from __future__ import annotations

from app.core.validation import CheckSpec, Finding, run_checks


def test_runner_keeps_registry_order_when_checks_finish_in_parallel() -> None:
    checks = (
        CheckSpec("rule.first", lambda _artifact, _context: [Finding("rule.first", "first")]),
        CheckSpec("rule.second", lambda _artifact, _context: [Finding("rule.second", "second")]),
    )

    report = run_checks(checks, {}, {}, parallel=True)

    assert report.status == "findings"
    assert [finding.rule_id for finding in report.findings] == ["rule.first", "rule.second"]
    assert report.checked_rule_ids == ("rule.first", "rule.second")


def test_runner_reports_an_exception_without_hiding_other_checks() -> None:
    def broken(_artifact: dict, _context: dict) -> list[Finding]:
        raise ValueError("bad rule")

    checks = (
        CheckSpec("rule.broken", broken),
        CheckSpec("rule.good", lambda _artifact, _context: [Finding("rule.good", "found")]),
    )

    report = run_checks(checks, {}, {})

    assert report.status == "error"
    assert [finding.rule_id for finding in report.findings] == ["rule.good"]
    assert report.errors == ("rule.broken: ValueError: bad rule",)


def test_runner_refuses_parallel_execution_for_an_unsafe_check() -> None:
    invoked: list[str] = []

    def unsafe(_artifact: dict, _context: dict) -> list[Finding]:
        invoked.append("unsafe")
        return []

    report = run_checks(
        (CheckSpec("rule.unsafe", unsafe, parallel_safe=False),),
        {},
        {},
        parallel=True,
    )

    assert report.status == "error"
    assert report.errors == ("parallel execution requested for non-parallel checks: rule.unsafe",)
    assert invoked == []


def test_runner_reports_mixed_user_and_automatic_findings_as_findings() -> None:
    report = run_checks(
        (
            CheckSpec(
                "rule.choice",
                lambda _artifact, _context: [
                    Finding("rule.choice", "needs a decision", requires_user_input=True)
                ],
            ),
            CheckSpec(
                "rule.fix",
                lambda _artifact, _context: [Finding("rule.fix", "needs a repair")],
            ),
        ),
        {},
        {},
    )

    assert report.status == "findings"


def test_runner_reports_only_user_decisions_as_needing_input() -> None:
    report = run_checks(
        (
            CheckSpec(
                "rule.choice",
                lambda _artifact, _context: [
                    Finding("rule.choice", "needs a decision", requires_user_input=True)
                ],
            ),
        ),
        {},
        {},
    )

    assert report.status == "needs_input"
