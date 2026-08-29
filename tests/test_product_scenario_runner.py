from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from evaluation.easydep.product_scenario import (
    ProductScenarioNeedsInput,
    ProductScenarioRunner,
    ProductScenarioTimeout,
    next_auto_action,
)


def _command(
    number: int,
    stage: str,
    status: str,
    *,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "command_id": f"command-{number}",
        "stage": stage,
        "status": status,
        "result": dict(result or {}),
        "error": None,
    }


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (_command(1, "requirements", "COMPLETED"), {"action": "start_design"}),
        (
            _command(2, "design", "COMPLETED"),
            {"action": "start_implementation", "allow_assumptions": True},
        ),
        (
            _command(3, "implementation", "COMPLETED", result={"job_id": "i-1"}),
            {"action": "start_testing", "implementation_job_id": "i-1"},
        ),
        (
            _command(
                4,
                "requirements",
                "AWAITING_INPUT",
                result={"resource_question": {"kind": "suggested"}},
            ),
            {"action": "advance", "action_id": "command-4"},
        ),
        (
            _command(
                5,
                "requirements",
                "AWAITING_INPUT",
                result={"requires_revision": True, "can_delegate_repair": True},
            ),
            {"action": "delegate_repair", "action_id": "command-5"},
        ),
        (
            _command(
                6,
                "design",
                "AWAITING_INPUT",
                result={"method_proposals": [{"id": "method-1"}]},
            ),
            {
                "action": "advance",
                "action_id": "command-6",
                "auto_approve_method_proposals": True,
            },
        ),
        (
            _command(
                7,
                "implementation",
                "AWAITING_INPUT",
                result={"job_id": "i-1", "request_id": "r-1"},
            ),
            {
                "action": "approve_implementation",
                "action_id": "command-7",
                "job_id": "i-1",
                "request_id": "r-1",
                "delegate_repair_approvals": True,
            },
        ),
    ],
)
def test_auto_action_matches_frontend_buttons(
    command: Mapping[str, Any], expected: Mapping[str, Any]
) -> None:
    action = next_auto_action(command)
    assert action is not None
    assert action.payload() == expected


@pytest.mark.parametrize(
    "result",
    [
        {"kind": "question", "questions": ["리전을 선택하세요."]},
        {"resource_question": {"kind": "choice"}},
        {"action": "confirm_change"},
        {"requires_revision": True, "can_delegate_repair": False},
        {
            "requires_revision": True,
            "can_delegate_repair": True,
            "repair_state": {"status": "STALLED"},
        },
    ],
)
def test_auto_action_does_not_guess_user_decisions(result: Mapping[str, Any]) -> None:
    command = _command(1, "requirements", "AWAITING_INPUT", result=result)
    assert next_auto_action(command) is None


class FakeTransport:
    """공개 HTTP 응답과 같은 값만 제공하는 작은 transport다."""

    def __init__(self, snapshots: Sequence[Mapping[str, Any]]) -> None:
        self.snapshots = list(snapshots)
        self.index = 0
        self.commands: list[dict[str, Any]] = []
        self.cursors: list[int] = []

    def create_app(self, message: str) -> Mapping[str, Any]:
        assert message == "주문 서비스를 만들어 주세요."
        return {"app_id": "app-1", "command": _command(0, "requirements", "QUEUED")}

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        assert app_id == "app-1"
        item = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return {"app_id": app_id, "command": dict(item), "current_stage": item["stage"]}

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        assert app_id == "app-1"
        self.commands.append(dict(payload))
        return {"app_id": app_id}

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]:
        assert app_id == "app-1"
        assert timeout_seconds >= 0
        self.cursors.append(after)
        return [{"event_id": after + 1, "kind": "status"}]

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]:
        assert app_id == "app-1"
        return {"app_id": app_id, "artifacts": {"class_diagram": "@startuml\n@enduml"}}


def test_runner_calls_only_the_public_product_flow() -> None:
    snapshots = [
        _command(1, "requirements", "COMPLETED"),
        _command(
            2,
            "design",
            "AWAITING_INPUT",
            result={"requires_revision": True, "can_delegate_repair": True},
        ),
        _command(3, "design", "COMPLETED"),
        _command(
            4,
            "implementation",
            "AWAITING_INPUT",
            result={"job_id": "implementation-1", "request_id": "approval-1"},
        ),
        _command(
            5,
            "implementation",
            "COMPLETED",
            result={"job_id": "implementation-1"},
        ),
        _command(6, "testing", "COMPLETED", result={"job_id": "testing-1"}),
    ]
    transport = FakeTransport(snapshots)
    runner = ProductScenarioRunner(
        transport, poll_interval_seconds=0, event_wait_seconds=0
    )

    result = runner.run("주문 서비스를 만들어 주세요.")

    assert [payload["action"] for payload in transport.commands] == [
        "start_design",
        "delegate_repair",
        "start_implementation",
        "approve_implementation",
        "start_testing",
    ]
    assert transport.cursors == [0, 1, 2, 3, 4, 5]
    assert result.location.stage == "testing"
    assert result.location.event_cursor == 6
    assert result.implementation_job_id == "implementation-1"
    assert result.testing_job_id == "testing-1"
    assert result.artifacts["artifacts"]["class_diagram"].startswith("@startuml")


def test_runner_stops_at_the_same_question_as_frontend_auto_mode() -> None:
    transport = FakeTransport(
        [
            _command(
                1,
                "requirements",
                "AWAITING_INPUT",
                result={"kind": "question", "questions": ["리전을 선택하세요."]},
            )
        ]
    )
    runner = ProductScenarioRunner(
        transport, poll_interval_seconds=0, event_wait_seconds=0
    )

    with pytest.raises(ProductScenarioNeedsInput) as captured:
        runner.run("주문 서비스를 만들어 주세요.")

    assert captured.value.location.app_id == "app-1"
    assert captured.value.location.stage == "requirements"
    assert captured.value.location.command_id == "command-1"
    assert transport.commands == []


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_timeout_contains_only_the_public_failure_location() -> None:
    transport = FakeTransport([_command(8, "design", "RUNNING")])
    clock = FakeClock()
    runner = ProductScenarioRunner(
        transport,
        timeout_seconds=0.2,
        poll_interval_seconds=0.1,
        event_wait_seconds=0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(ProductScenarioTimeout) as captured:
        runner.run("주문 서비스를 만들어 주세요.")

    assert captured.value.location.as_dict() == {
        "app_id": "app-1",
        "stage": "design",
        "command_id": "command-8",
        "status": "RUNNING",
        "event_cursor": 2,
        "reason": "전체 실행 시간 초과",
    }
