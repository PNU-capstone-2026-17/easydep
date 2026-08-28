from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from evaluation.easydep.product_scenario import (
    AutoActionPolicy,
    ProductScenarioFailed,
    ProductScenarioRunner,
    ProductScenarioTimeout,
    ScenarioFailureReport,
    public_actions,
)


def _command(
    number: int,
    stage: str,
    status: str,
    *,
    result: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "command_id": f"command-{number}",
        "app_id": "app-1",
        "action": "message",
        "stage": stage,
        "status": status,
        "payload": dict(payload or {}),
        "result": dict(result or {}),
        "error": None,
    }


class FakePublicTransport:
    """내부 서비스를 거치지 않고 공개 HTTP 응답 모양만 제공하는 테스트 transport다."""

    def __init__(
        self,
        snapshots: Sequence[Mapping[str, Any]],
        *,
        source_job_id: str = "implementation-1",
        include_source_metadata: bool = True,
    ) -> None:
        self.snapshots = list(snapshots)
        self.source_job_id = source_job_id
        self.include_source_metadata = include_source_metadata
        self.snapshot_index = 0
        self.commands: list[dict[str, Any]] = []
        self.event_queries: list[int] = []
        self.file_queries: list[tuple[str, str]] = []
        self.source = "class Main {}\n"
        self.events = [
            {
                "event_id": index,
                "app_id": "app-1",
                "stage": "requirements",
                "kind": "status",
                "actor": "system",
                "text": f"event {index}",
                "metadata": {},
            }
            for index in range(1, 10)
        ]

    def create_app(self, message: str) -> Mapping[str, Any]:
        assert message == "온라인 주문 서비스를 만들어 주세요."
        return {"app_id": "app-1", "command": _command(0, "requirements", "QUEUED")}

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        assert app_id == "app-1"
        index = min(self.snapshot_index, len(self.snapshots) - 1)
        snapshot = dict(self.snapshots[index])
        self.snapshot_index += 1
        return snapshot

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
        self.event_queries.append(after)
        return [event for event in self.events if event["event_id"] > after]

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]:
        assert app_id == "app-1"
        return {
            "app_id": app_id,
            "artifacts": {
                "refined_requirements": {"requirements": [{"id": "FR-1"}]},
                "class_diagram": "",
            },
        }

    def get_stage_versions(
        self, app_id: str, stage: str
    ) -> Sequence[Mapping[str, Any]]:
        assert app_id == "app-1"
        assert stage == "refined_requirements"
        return [
            {"version_no": 1, "is_current": False},
            {"version_no": 2, "is_current": True},
        ]

    def get_stage_version(
        self, app_id: str, stage: str, version_no: int
    ) -> Mapping[str, Any]:
        assert (app_id, stage, version_no) == ("app-1", "refined_requirements", 2)
        return {"version_no": 2, "content": {"requirements": [{"id": "FR-1"}]}}

    def get_file_artifact(
        self, app_id: str, artifact_type: str
    ) -> Mapping[str, Any] | None:
        assert app_id == "app-1"
        if artifact_type != "SOURCE_CODE":
            return None
        digest = hashlib.sha256(self.source.encode("utf-8")).hexdigest()
        snapshot = {
            "artifact_type": artifact_type,
            "version_no": 7,
            "snapshot_digest": "source-snapshot-digest",
            "files": [{"path": "src/Main.java", "sha256": digest}],
            "created_at": "2026-08-28T12:00:00",
        }
        if self.include_source_metadata:
            snapshot["metadata"] = {"implementation_job_id": self.source_job_id}
        return snapshot

    def get_artifact_file(
        self, app_id: str, artifact_type: str, path: str
    ) -> Mapping[str, Any]:
        assert app_id == "app-1"
        self.file_queries.append((artifact_type, path))
        return {
            "path": path,
            "content": self.source,
            "sha256": hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
        }


def _snapshot(command: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "app_id": "app-1",
        "current_stage": command["stage"],
        "command": dict(command),
        "events": [],
        "artifacts": {},
    }


def _completed_scenario_snapshots() -> list[dict[str, Any]]:
    return [
        _snapshot(
            _command(
                1,
                "requirements",
                "AWAITING_INPUT",
                result={"kind": "question", "questions": ["어느 지역을 사용합니까?"]},
            )
        ),
        _snapshot(
            _command(
                2,
                "requirements",
                "AWAITING_INPUT",
                result={
                    "requires_revision": True,
                    "can_delegate_repair": True,
                    "blocking_findings": [{"message": "FR-1을 구체화하세요."}],
                    "repair_state": {"status": "ACTIVE"},
                },
            )
        ),
        _snapshot(_command(3, "requirements", "COMPLETED")),
        _snapshot(
            _command(
                4,
                "design",
                "AWAITING_INPUT",
                result={"method_proposals": [{"id": "method-1"}]},
            )
        ),
        _snapshot(_command(5, "design", "COMPLETED")),
        _snapshot(
            _command(
                6,
                "implementation",
                "AWAITING_INPUT",
                result={"job_id": "implementation-1", "request_id": "approval-1"},
            )
        ),
        _snapshot(
            _command(
                7,
                "implementation",
                "COMPLETED",
                result={"job_id": "implementation-1"},
            )
        ),
        _snapshot(
            _command(
                8,
                "testing",
                "AWAITING_INPUT",
                payload={"implementation_job_id": "implementation-1"},
                result={
                    "job_id": "testing-1",
                    "requires_revision": True,
                    "can_delegate_repair": True,
                    "repair_state": {"status": "ACTIVE"},
                    "job": {"implementation_job_id": "implementation-1"},
                },
            )
        ),
        _snapshot(
            _command(
                9,
                "testing",
                "COMPLETED",
                payload={"implementation_job_id": "implementation-1"},
                result={
                    "job_id": "testing-2",
                    "job": {"implementation_job_id": "implementation-1"},
                },
            )
        ),
    ]


def test_runner_uses_the_public_command_flow_and_collects_provenance() -> None:
    transport = FakePublicTransport(_completed_scenario_snapshots())
    runner = ProductScenarioRunner(
        transport,
        policy=AutoActionPolicy(lambda _command: "AWS 서울 리전을 사용합니다."),
        poll_interval_seconds=0,
        event_wait_seconds=0.01,
    )

    result = runner.run("온라인 주문 서비스를 만들어 주세요.")

    assert [command["action"] for command in transport.commands] == [
        "message",
        "delegate_repair",
        "start_design",
        "advance",
        "start_implementation",
        "approve_implementation",
        "start_testing",
        "delegate_repair",
    ]
    assert transport.commands[0]["text"] == "AWS 서울 리전을 사용합니다."
    assert transport.commands[3]["auto_approve_method_proposals"] is True
    assert transport.commands[5]["delegate_repair_approvals"] is True
    assert transport.commands[6]["implementation_job_id"] == "implementation-1"
    assert result.implementation_job_id == "implementation-1"
    assert result.testing_job_id == "testing-2"
    assert result.event_cursor == 9
    assert transport.event_queries[0] == 0
    assert result.artifact_references["refined_requirements"].version_no == 2
    source = result.artifact_references["SOURCE_CODE"]
    assert (source.version_no, source.file_count, source.verified_file_count) == (7, 1, 1)
    assert transport.file_queries == [("SOURCE_CODE", "src/Main.java")]


def test_runner_can_stop_after_design_without_starting_implementation() -> None:
    snapshots = _completed_scenario_snapshots()[:5]
    transport = FakePublicTransport(snapshots)
    runner = ProductScenarioRunner(
        transport,
        policy=AutoActionPolicy(lambda _command: "AWS 서울 리전을 사용합니다."),
        poll_interval_seconds=0,
        event_wait_seconds=0.01,
    )

    result = runner.run_until(
        "온라인 주문 서비스를 만들어 주세요.", stop_after_stage="design"
    )

    assert result.current_stage == "design"
    assert result.implementation_job_id is None
    assert [command["action"] for command in transport.commands] == [
        "message",
        "delegate_repair",
        "start_design",
        "advance",
    ]


def test_runner_resumes_the_same_app_without_creating_another_one() -> None:
    transport = FakePublicTransport(_completed_scenario_snapshots()[4:])

    def fail_if_created(_message: str) -> Mapping[str, Any]:
        pytest.fail("resume must not create a new app")

    transport.create_app = fail_if_created  # type: ignore[method-assign]
    runner = ProductScenarioRunner(
        transport,
        policy=AutoActionPolicy(),
        poll_interval_seconds=0,
        event_wait_seconds=0.01,
    )
    report = ScenarioFailureReport(
        app_id="app-1",
        last_command_id="command-4",
        current_stage="design",
        event_cursor=4,
        implementation_job_id=None,
        testing_job_id=None,
        artifact_versions={},
        reason="평가 process가 중단됨",
    )

    result = runner.resume_from(report)

    assert result.app_id == "app-1"
    assert result.current_stage == "testing"
    assert result.implementation_job_id == "implementation-1"
    assert result.testing_job_id == "testing-2"


class FakeClock:
    """실제로 기다리지 않고 polling 시간만 앞으로 보내는 테스트용 시계다."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_timeout_report_keeps_resume_location_and_visible_artifact_versions() -> None:
    running = _snapshot(
        _command(
            20,
            "testing",
            "RUNNING",
            payload={"implementation_job_id": "implementation-1"},
            result={"job_id": "testing-running"},
        )
    )
    transport = FakePublicTransport([running])
    clock = FakeClock()
    runner = ProductScenarioRunner(
        transport,
        timeout_seconds=0.25,
        poll_interval_seconds=0.1,
        event_wait_seconds=0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(ProductScenarioTimeout) as captured:
        runner.run("온라인 주문 서비스를 만들어 주세요.")

    report = captured.value.report
    assert report.app_id == "app-1"
    assert report.last_command_id == "command-20"
    assert report.current_stage == "testing"
    assert report.event_cursor == 9
    assert report.implementation_job_id == "implementation-1"
    assert report.testing_job_id == "testing-running"
    assert report.artifact_versions == {"refined_requirements": 2, "SOURCE_CODE": 7}


def test_runner_rejects_files_from_another_implementation_job() -> None:
    transport = FakePublicTransport(
        _completed_scenario_snapshots(), source_job_id="another-implementation"
    )
    runner = ProductScenarioRunner(
        transport,
        policy=AutoActionPolicy(lambda _command: "AWS 서울 리전을 사용합니다."),
        poll_interval_seconds=0,
        event_wait_seconds=0.01,
    )

    with pytest.raises(ProductScenarioFailed) as captured:
        runner.run("온라인 주문 서비스를 만들어 주세요.")

    assert captured.value.report.implementation_job_id == "implementation-1"
    assert "다른 implementation job" in captured.value.report.reason


def test_runner_rejects_file_snapshot_without_job_provenance() -> None:
    transport = FakePublicTransport(
        _completed_scenario_snapshots(), include_source_metadata=False
    )
    runner = ProductScenarioRunner(
        transport,
        policy=AutoActionPolicy(lambda _command: "AWS 서울 리전을 사용합니다."),
        poll_interval_seconds=0,
        event_wait_seconds=0.01,
    )

    with pytest.raises(ProductScenarioFailed) as captured:
        runner.run("온라인 주문 서비스를 만들어 주세요.")

    assert "출처 정보" in captured.value.report.reason


def test_auto_policy_does_not_turn_manual_repair_message_into_a_question_answer() -> None:
    command = _command(
        1,
        "requirements",
        "AWAITING_INPUT",
        result={"requires_revision": True, "can_delegate_repair": False},
    )
    policy = AutoActionPolicy(lambda _command: "질문이 아닌데 보낼 메시지")

    actions = public_actions(command)

    assert [action.action for action in actions] == ["message"]
    assert policy.choose(actions, command) is None


@pytest.mark.parametrize(
    ("stage", "payload", "expected_action"),
    [
        ("requirements", {}, "retry_requirements"),
        ("design", {}, "retry_design"),
        ("implementation", {}, "rerun_implementation"),
        (
            "testing",
            {"implementation_job_id": "implementation-1"},
            "start_testing",
        ),
    ],
)
def test_failed_stage_retry_is_visible_but_auto_mode_waits_for_the_user(
    stage: str, payload: Mapping[str, Any], expected_action: str
) -> None:
    command = _command(1, stage, "FAILED", payload=payload)
    actions = public_actions(command)

    assert [action.action for action in actions] == [expected_action]
    assert AutoActionPolicy().choose(actions, command) is None


def test_auto_policy_does_not_confirm_a_change_without_user_choice() -> None:
    command = _command(
        1,
        "requirements",
        "AWAITING_INPUT",
        result={"action": "confirm_change"},
    )
    actions = public_actions(command)

    assert [action.action for action in actions] == [
        "confirm_change",
        "dismiss_change",
    ]
    assert AutoActionPolicy().choose(actions, command) is None
