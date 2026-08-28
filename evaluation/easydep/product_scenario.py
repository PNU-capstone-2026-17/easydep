"""브라우저와 같은 공개 HTTP 경로로 EasyDep 전체 흐름을 실행한다.

이 모듈의 목적은 평가 코드가 요구사항이나 설계 단계의 내부 함수를 직접 호출하지 않게
하는 것이다. :class:`ProductScenarioRunner`는 Workspace 화면과 같은 URL에 앱 생성과
command 요청을 보내고, 공개 snapshot과 event만 읽어 다음 동작을 결정한다.

실제 서버에는 :class:`HttpProductScenarioTransport`를 사용한다. 테스트에서는 같은 protocol을
구현한 작은 fake를 넣을 수 있으므로, 테스트만 성공하는 별도 실행 경로를 만들 필요가 없다.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

JsonObject = dict[str, Any]

FILE_ARTIFACT_TYPES = (
    "SOURCE_CODE",
    "FRONTEND_SOURCE_CODE",
    "TEST_CODE",
    "DEPLOYMENT_FILE",
    "IAC_CODE",
)


class ProductScenarioHttpError(RuntimeError):
    """서버가 2xx 이외의 HTTP 상태를 반환했음을 나타낸다."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


@runtime_checkable
class ProductScenarioTransport(Protocol):
    """제품 시나리오 실행기가 사용하는 공개 HTTP 기능의 목록이다.

    메서드 이름은 Python 내부 서비스가 아니라 실제 HTTP endpoint의 역할을 나타낸다.
    따라서 이 protocol을 구현하는 fake도 실제 서버와 같은 요청·응답 모양을 사용한다.
    """

    def create_app(self, message: str) -> Mapping[str, Any]: ...

    def get_workspace(self, app_id: str) -> Mapping[str, Any]: ...

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]: ...

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]: ...

    def get_stage_versions(
        self, app_id: str, stage: str
    ) -> Sequence[Mapping[str, Any]]: ...

    def get_stage_version(
        self, app_id: str, stage: str, version_no: int
    ) -> Mapping[str, Any]: ...

    def get_file_artifact(
        self, app_id: str, artifact_type: str
    ) -> Mapping[str, Any] | None: ...

    def get_artifact_file(
        self, app_id: str, artifact_type: str, path: str
    ) -> Mapping[str, Any]: ...


class HttpProductScenarioTransport:
    """표준 라이브러리만 사용해 실제 EasyDep HTTP API에 연결한다."""

    def __init__(self, base_url: str, *, request_timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds

    def _url(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        """상대 API 경로와 query parameter를 하나의 URL로 합친다."""
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return url

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> JsonObject:
        """JSON 요청을 보내고 오류 응답의 ``detail``도 읽기 쉬운 예외로 바꾼다."""
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with urlopen(  # noqa: S310 - 사용자가 지정한 EasyDep 서버에 연결한다.
                request,
                timeout=timeout_seconds or self.request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                detail = parsed.get("detail", raw)
                if isinstance(detail, dict):
                    detail = detail.get("message", detail)
            except json.JSONDecodeError:
                detail = raw or error.reason
            raise ProductScenarioHttpError(error.code, str(detail)) from error
        if not isinstance(payload, dict):
            raise TypeError(f"Expected a JSON object from {path}.")
        return payload

    def create_app(self, message: str) -> Mapping[str, Any]:
        """최초 요구사항을 보내 Workspace 앱과 첫 command를 만든다."""
        return self._json_request("POST", "/api/workspace/apps", body={"message": message})

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        """화면을 복원할 때 쓰는 Workspace snapshot을 조회한다."""
        return self._json_request("GET", f"/api/workspace/apps/{quote(app_id, safe='')}")

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """사용자가 버튼을 누르거나 답변을 보낼 때와 같은 command를 등록한다."""
        return self._json_request(
            "POST",
            f"/api/workspace/apps/{quote(app_id, safe='')}/commands",
            body=payload,
        )

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]:
        """SSE stream에서 cursor 다음의 Workspace event 한 건을 읽는다.

        SSE 연결은 원래 계속 열린다. 실행기는 짧게 연결해 새 event 한 건을 받은 뒤
        연결을 닫고, 다음에는 받은 event ID를 ``after``로 전달한다. 아무 event도 없는
        동안 socket timeout이 나면 빈 목록을 반환해 snapshot polling을 계속한다.
        """
        path = f"/api/workspace/apps/{quote(app_id, safe='')}/events"
        url = self._url(path, {"after": after})
        request = Request(url, headers={"Accept": "text/event-stream"}, method="GET")
        data_lines: list[str] = []
        try:
            with urlopen(  # noqa: S310 - 사용자가 지정한 EasyDep 서버에 연결한다.
                request,
                timeout=max(0.05, timeout_seconds),
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        payload = json.loads("\n".join(data_lines))
                        if not isinstance(payload, dict):
                            raise TypeError("Workspace SSE data must be a JSON object.")
                        return [payload]
        except TimeoutError:
            return []
        return []

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]:
        """구조화된 요구사항·설계 산출물 목록과 내용을 조회한다."""
        return self._json_request("GET", f"/api/apps/{quote(app_id, safe='')}")

    def get_stage_versions(
        self, app_id: str, stage: str
    ) -> Sequence[Mapping[str, Any]]:
        """구조화 산출물 한 종류의 공개 버전 목록을 조회한다."""
        payload = self._json_request(
            "GET",
            f"/api/apps/{quote(app_id, safe='')}/stages/{quote(stage, safe='')}/versions",
        )
        versions = payload.get("versions") or []
        return [item for item in versions if isinstance(item, dict)]

    def get_stage_version(
        self, app_id: str, stage: str, version_no: int
    ) -> Mapping[str, Any]:
        """선택한 구조화 산출물 버전의 실제 내용을 조회한다."""
        return self._json_request(
            "GET",
            (
                f"/api/apps/{quote(app_id, safe='')}/stages/{quote(stage, safe='')}"
                f"/versions/{version_no}"
            ),
        )

    def get_file_artifact(
        self, app_id: str, artifact_type: str
    ) -> Mapping[str, Any] | None:
        """구현 파일 snapshot을 조회하며, 아직 없다는 404는 ``None``으로 바꾼다."""
        try:
            return self._json_request(
                "GET",
                (
                    f"/api/implementation/apps/{quote(app_id, safe='')}/artifacts/"
                    f"{quote(artifact_type, safe='')}"
                ),
            )
        except ProductScenarioHttpError as error:
            if error.status == 404:
                return None
            raise

    def get_artifact_file(
        self, app_id: str, artifact_type: str, path: str
    ) -> Mapping[str, Any]:
        """구현 snapshot의 파일 한 건을 공개 파일 endpoint에서 읽는다."""
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        return self._json_request(
            "GET",
            (
                f"/api/implementation/apps/{quote(app_id, safe='')}/artifacts/"
                f"{quote(artifact_type, safe='')}/files/{encoded_path}"
            ),
        )


@dataclass(frozen=True)
class PublicAction:
    """현재 화면에서 사용자가 선택할 수 있는 command와 그 payload다."""

    action: str
    payload: Mapping[str, Any] = field(default_factory=dict)


def _first_resource_question(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """현재 응답이 사용하는 단일·목록 질문 형식에서 첫 질문을 찾는다."""
    question = result.get("resource_question")
    if isinstance(question, dict):
        return question
    questions = result.get("resource_questions")
    if isinstance(questions, list):
        return next((item for item in questions if isinstance(item, dict)), None)
    return None


def public_actions(command: Mapping[str, Any]) -> tuple[PublicAction, ...]:
    """공개 command snapshot을 화면에서 누를 수 있는 선택지로 바꾼다.

    이 함수는 프론트엔드와 같은 ``status``, ``stage``, ``result``만 사용한다. 새 backend
    endpoint나 내부 단계 상태를 추측하지 않으며, 선택지가 없으면 빈 tuple을 반환한다.
    """
    command_id = str(command.get("command_id") or "")
    status = str(command.get("status") or "")
    stage = str(command.get("stage") or "")
    raw_result = command.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}

    if status in {"FAILED", "INTERRUPTED"}:
        if stage == "design":
            return (PublicAction("retry_design", {"action_id": command_id}),)
        if stage == "implementation":
            return (PublicAction("rerun_implementation"),)
        if stage == "testing":
            raw_command_payload = command.get("payload")
            implementation_job_id = (
                raw_command_payload.get("implementation_job_id")
                if isinstance(raw_command_payload, dict)
                else None
            )
            if implementation_job_id:
                return (
                    PublicAction(
                        "start_testing",
                        {"implementation_job_id": str(implementation_job_id)},
                    ),
                )
        return ()

    if status == "COMPLETED":
        if stage == "requirements":
            return (PublicAction("start_design"),)
        if stage == "design":
            return (PublicAction("start_implementation", {"allow_assumptions": True}),)
        if stage == "implementation" and result.get("job_id"):
            return (
                PublicAction(
                    "start_testing",
                    {"implementation_job_id": str(result["job_id"])},
                ),
            )
        return ()

    if status != "AWAITING_INPUT":
        return ()

    if result.get("action") == "confirm_change":
        reference = {"action_id": command_id}
        return (
            PublicAction("confirm_change", reference),
            PublicAction("dismiss_change", reference),
        )

    resource_question = _first_resource_question(result)
    if resource_question is not None:
        if resource_question.get("kind") == "suggested":
            return (PublicAction("advance", {"action_id": command_id}),)
        return (PublicAction("message", {"action_id": command_id}),)

    questions = result.get("questions")
    if result.get("kind") == "question" or (
        isinstance(questions, list) and bool(questions)
    ):
        return (PublicAction("message", {"action_id": command_id}),)

    has_method_proposals = (
        stage == "design"
        and isinstance(result.get("method_proposals"), list)
        and bool(result["method_proposals"])
    )
    if result.get("requires_revision") is True:
        actions: list[PublicAction] = []
        if result.get("can_delegate_repair") is True:
            repair_state = result.get("repair_state")
            repair_status = (
                str(repair_state.get("status") or "")
                if isinstance(repair_state, dict)
                else ""
            )
            if repair_status not in {"WAITING_EXTERNAL", "STALLED"}:
                actions.append(
                    PublicAction("delegate_repair", {"action_id": command_id})
                )
        # 사람이 구체적인 수정 지시를 입력하는 선택지는 repair 버튼과 함께 항상 남긴다.
        actions.append(PublicAction("message", {"action_id": command_id}))
        if actions or not has_method_proposals:
            return tuple(actions)

    if stage in {"requirements", "design"}:
        payload: JsonObject = {"action_id": command_id}
        if has_method_proposals:
            payload["auto_approve_method_proposals"] = True
        return (PublicAction("advance", payload),)

    if stage == "implementation" and result.get("job_id") and result.get("request_id"):
        return (
            PublicAction(
                "approve_implementation",
                {
                    "action_id": command_id,
                    "job_id": str(result["job_id"]),
                    "request_id": str(result["request_id"]),
                    "delegate_repair_approvals": True,
                },
            ),
            PublicAction(
                "reject_implementation",
                {
                    "action_id": command_id,
                    "job_id": str(result["job_id"]),
                    "request_id": str(result["request_id"]),
                },
            ),
        )
    return ()


class ScenarioPolicy(Protocol):
    """공개된 선택지 중 하나를 고르는 사용자 동작 정책이다."""

    def choose(
        self,
        actions: Sequence[PublicAction],
        command: Mapping[str, Any],
    ) -> PublicAction | None: ...


QuestionAnswer = Callable[[Mapping[str, Any]], str | None]


class AutoActionPolicy:
    """화면에 이미 나타난 안전한 선택지를 대신 클릭하는 자동 정책이다.

    자동 모드 전용 backend 호출은 없다. repair가 가능하면 ``delegate_repair`` 버튼을,
    다음 단계로 갈 수 있으면 ``advance`` 또는 ``start_*`` 버튼을 고른다. 실제 사용자
    판단이 필요한 질문은 ``question_answer`` callback이 답을 제공한 경우에만 보낸다.
    """

    def __init__(self, question_answer: QuestionAnswer | None = None) -> None:
        self.question_answer = question_answer

    def choose(
        self,
        actions: Sequence[PublicAction],
        command: Mapping[str, Any],
    ) -> PublicAction | None:
        """우선순위가 가장 높은 공개 action을 고르되 답변을 새로 지어내지 않는다."""
        raw_result = command.get("result")
        result = raw_result if isinstance(raw_result, dict) else {}
        # 화면의 자동 모드도 실패 재실행과 변경 확인은 사용자가 결정하도록 멈춘다.
        # ``public_actions``에는 사람이 누를 버튼이 남아 있지만 이 정책이 임의로 누르지는
        # 않는다.
        if str(command.get("status") or "") in {"FAILED", "INTERRUPTED"}:
            return None
        if result.get("action") == "confirm_change":
            return None
        has_question = (
            _first_resource_question(result) is not None
            or result.get("kind") == "question"
            or (
                isinstance(result.get("questions"), list)
                and bool(result["questions"])
            )
        )
        for action in actions:
            if action.action != "message":
                return action
            # ``message``는 질문 답변뿐 아니라 사람이 repair 지시를 직접 쓰는 데에도
            # 사용된다. 자동 정책은 실제 질문일 때만 callback의 답을 전송한다.
            if has_question and self.question_answer is not None:
                answer = self.question_answer(command)
                if answer and answer.strip():
                    return PublicAction(
                        "message", {**dict(action.payload), "text": answer.strip()}
                    )
        return None


@dataclass(frozen=True)
class ArtifactReference:
    """한 산출물이 어느 저장 버전에서 왔는지 보여 주는 정보다."""

    artifact_type: str
    version_no: int
    digest: str
    file_count: int | None = None
    verified_file_count: int = 0


@dataclass
class ScenarioProgress:
    """실행 중 다음 polling과 실패 보고서에 필요한 최소 진행 상태다."""

    app_id: str = ""
    last_command_id: str | None = None
    current_stage: str | None = None
    event_cursor: int = 0
    implementation_job_id: str | None = None
    testing_job_id: str | None = None
    artifact_versions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioFailureReport:
    """중단된 실행을 같은 앱에서 이어갈 수 있도록 남기는 위치 정보다."""

    app_id: str
    last_command_id: str | None
    current_stage: str | None
    event_cursor: int
    implementation_job_id: str | None
    testing_job_id: str | None
    artifact_versions: Mapping[str, int]
    reason: str

    def as_dict(self) -> JsonObject:
        """로그나 JSON 보고서에 바로 기록할 수 있는 dict로 바꾼다."""
        return {
            "app_id": self.app_id,
            "last_command_id": self.last_command_id,
            "current_stage": self.current_stage,
            "event_cursor": self.event_cursor,
            "implementation_job_id": self.implementation_job_id,
            "testing_job_id": self.testing_job_id,
            "artifact_versions": dict(self.artifact_versions),
            "reason": self.reason,
        }


class ProductScenarioStopped(RuntimeError):
    """사용자 입력이나 실행 실패 때문에 자동 진행을 멈췄음을 나타낸다."""

    def __init__(self, report: ScenarioFailureReport) -> None:
        self.report = report
        super().__init__(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))


class ProductScenarioTimeout(ProductScenarioStopped):
    """전체 시나리오가 지정된 시간 안에 끝나지 않았음을 나타낸다."""


class ProductScenarioNeedsInput(ProductScenarioStopped):
    """화면에 선택지가 있지만 policy가 안전하게 고르지 못했음을 나타낸다."""


class ProductScenarioFailed(ProductScenarioStopped):
    """command 실패 또는 산출물 출처 불일치로 실행을 종료했음을 나타낸다."""


@dataclass(frozen=True)
class ProductScenarioResult:
    """전체 제품 경로가 끝난 뒤 평가와 재현에 사용하는 결과다."""

    app_id: str
    last_command_id: str
    event_cursor: int
    implementation_job_id: str
    testing_job_id: str
    artifact_references: Mapping[str, ArtifactReference]
    events: tuple[Mapping[str, Any], ...]


class ProductScenarioRunner:
    """앱 생성부터 테스트와 산출물 조회까지 공개 API 흐름을 조율한다."""

    def __init__(
        self,
        transport: ProductScenarioTransport,
        *,
        policy: ScenarioPolicy | None = None,
        timeout_seconds: float = 7200.0,
        poll_interval_seconds: float = 1.0,
        event_wait_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if poll_interval_seconds < 0 or event_wait_seconds < 0:
            raise ValueError("polling intervals cannot be negative")
        self.transport = transport
        self.policy = policy or AutoActionPolicy()
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.event_wait_seconds = event_wait_seconds
        self.monotonic = monotonic
        self.sleep = sleep
        self.progress = ScenarioProgress()
        self._events: list[Mapping[str, Any]] = []

    def run(self, message: str) -> ProductScenarioResult:
        """새 앱을 만들고 테스트가 통과할 때까지 화면의 공개 command를 실행한다."""
        if not message.strip():
            raise ValueError("message must not be empty")
        self.progress = ScenarioProgress()
        self._events = []
        deadline = self.monotonic() + self.timeout_seconds

        created = self.transport.create_app(message.strip())
        self.progress.app_id = str(created.get("app_id") or "")
        if not self.progress.app_id:
            raise ValueError("Workspace create response did not include app_id.")
        self._record_command(created.get("command"))

        while True:
            self._ensure_time(deadline)
            snapshot = self.transport.get_workspace(self.progress.app_id)
            self.progress.current_stage = str(
                snapshot.get("current_stage") or self.progress.current_stage or ""
            )
            command = snapshot.get("command")
            if not isinstance(command, dict):
                self._read_events(deadline)
                self._pause(deadline)
                continue
            self._record_command(command)
            self._read_events(deadline)

            status = str(command.get("status") or "")
            stage = str(command.get("stage") or "")
            if stage == "testing" and status == "COMPLETED":
                references = self._collect_artifacts(verify_files=True)
                implementation_job_id = self.progress.implementation_job_id
                testing_job_id = self.progress.testing_job_id
                if not implementation_job_id:
                    self._raise_failed("완료 응답에 implementation job ID가 없습니다.")
                if not testing_job_id:
                    self._raise_failed("완료 응답에 testing job ID가 없습니다.")
                return ProductScenarioResult(
                    app_id=self.progress.app_id,
                    last_command_id=str(self.progress.last_command_id or ""),
                    event_cursor=self.progress.event_cursor,
                    implementation_job_id=implementation_job_id,
                    testing_job_id=testing_job_id,
                    artifact_references=references,
                    events=tuple(self._events),
                )

            actions = public_actions(command)
            if status in {"QUEUED", "PENDING", "RUNNING"}:
                self._pause(deadline)
                continue
            if not actions:
                reason = str(command.get("error") or "No public action is available.")
                self._raise_failed(reason)
            selected = self.policy.choose(actions, command)
            if selected is None:
                names = ", ".join(action.action for action in actions)
                raise ProductScenarioNeedsInput(
                    self._failure_report(f"사용자 선택이 필요합니다: {names}")
                )
            response = self.transport.submit_command(
                self.progress.app_id,
                {"action": selected.action, **dict(selected.payload)},
            )
            self._record_command(response.get("command"))

    def _record_command(self, raw_command: Any) -> None:
        """command 공개 응답에서 이후 단계에 필요한 ID와 stage를 기억한다."""
        if not isinstance(raw_command, dict):
            return
        command_id = raw_command.get("command_id")
        if command_id:
            self.progress.last_command_id = str(command_id)
        stage = str(raw_command.get("stage") or "")
        if stage:
            self.progress.current_stage = stage
        payload = raw_command.get("payload")
        if isinstance(payload, dict) and payload.get("implementation_job_id"):
            self.progress.implementation_job_id = str(payload["implementation_job_id"])
        result = raw_command.get("result")
        if not isinstance(result, dict):
            return
        job_id = result.get("job_id")
        if stage == "implementation" and job_id:
            self.progress.implementation_job_id = str(job_id)
        elif stage == "testing" and job_id:
            self.progress.testing_job_id = str(job_id)
        job = result.get("job")
        if isinstance(job, dict):
            implementation_job_id = job.get("implementation_job_id")
            if implementation_job_id:
                observed = str(implementation_job_id)
                expected = self.progress.implementation_job_id
                if expected and observed != expected:
                    self._raise_failed(
                        "Testing 결과가 다른 implementation job을 가리킵니다."
                    )
                self.progress.implementation_job_id = observed

    def _read_events(self, deadline: float) -> None:
        """현재 cursor 다음의 SSE event를 읽고 가장 큰 event ID까지 이동한다."""
        remaining = max(0.0, deadline - self.monotonic())
        wait = min(self.event_wait_seconds, remaining)
        if wait <= 0:
            self._ensure_time(deadline)
        events = self.transport.read_events(
            self.progress.app_id,
            self.progress.event_cursor,
            wait,
        )
        for event in events:
            event_id = event.get("event_id")
            if isinstance(event_id, int) and event_id > self.progress.event_cursor:
                self.progress.event_cursor = event_id
                self._events.append(dict(event))

    def _pause(self, deadline: float) -> None:
        """다음 polling 전 잠시 기다리되 전체 deadline을 넘겨 자지 않는다."""
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            self._ensure_time(deadline)
        self.sleep(min(self.poll_interval_seconds, remaining))

    def _ensure_time(self, deadline: float) -> None:
        """전체 제한 시간을 넘겼으면 현재 위치가 담긴 timeout 보고서를 만든다."""
        if self.monotonic() < deadline:
            return
        self._refresh_artifact_versions()
        raise ProductScenarioTimeout(self._failure_report("전체 실행 시간이 초과되었습니다."))

    def _failure_report(self, reason: str) -> ScenarioFailureReport:
        """현재 진행 상태를 중단·재현 보고서 형식으로 복사한다."""
        return ScenarioFailureReport(
            app_id=self.progress.app_id,
            last_command_id=self.progress.last_command_id,
            current_stage=self.progress.current_stage,
            event_cursor=self.progress.event_cursor,
            implementation_job_id=self.progress.implementation_job_id,
            testing_job_id=self.progress.testing_job_id,
            artifact_versions=dict(self.progress.artifact_versions),
            reason=reason,
        )

    def _raise_failed(self, reason: str) -> NoReturn:
        """실패 시점에도 공개 artifact 버전을 모은 뒤 예외를 발생시킨다."""
        self._refresh_artifact_versions()
        raise ProductScenarioFailed(self._failure_report(reason))

    @staticmethod
    def _has_content(value: Any) -> bool:
        """프론트엔드와 같은 기준으로 산출물 내용이 있는지 확인한다."""
        if isinstance(value, str):
            return bool(value)
        if isinstance(value, (dict, list)):
            return bool(value)
        return value is not None

    @staticmethod
    def _digest(value: Any) -> str:
        """JSON 값을 정렬된 UTF-8 문자열로 바꿔 재현 가능한 SHA-256을 만든다."""
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _current_version(
        versions: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        """버전 목록에서 current 표시가 있는 항목 또는 마지막 항목을 고른다."""
        current = next((item for item in versions if item.get("is_current") is True), None)
        return current or (versions[-1] if versions else None)

    def _refresh_artifact_versions(self) -> None:
        """timeout 보고서용으로 현재 공개 artifact 번호를 가능한 만큼 수집한다."""
        if not self.progress.app_id:
            return
        try:
            document = self.transport.get_artifacts(self.progress.app_id)
            artifacts = document.get("artifacts")
            if isinstance(artifacts, dict):
                for stage, content in artifacts.items():
                    if not self._has_content(content):
                        continue
                    versions = self.transport.get_stage_versions(
                        self.progress.app_id, str(stage)
                    )
                    current = self._current_version(versions)
                    if current and current.get("version_no") is not None:
                        self.progress.artifact_versions[str(stage)] = int(
                            current["version_no"]
                        )
            for artifact_type in FILE_ARTIFACT_TYPES:
                snapshot = self.transport.get_file_artifact(
                    self.progress.app_id, artifact_type
                )
                if snapshot and snapshot.get("version_no") is not None:
                    self.progress.artifact_versions[artifact_type] = int(
                        snapshot["version_no"]
                    )
        # 보고서를 보강하는 조회가 원래 timeout이나 command 실패를 가려서는 안 된다.
        except Exception:
            return

    def _collect_artifacts(
        self, *, verify_files: bool
    ) -> dict[str, ArtifactReference]:
        """최종 구조화 산출물과 구현 파일을 공개 endpoint에서 조회하고 출처를 검사한다."""
        document = self.transport.get_artifacts(self.progress.app_id)
        artifacts = document.get("artifacts")
        if not isinstance(artifacts, dict):
            self._raise_failed("Artifact document did not contain an artifacts object.")
        references: dict[str, ArtifactReference] = {}
        for raw_stage, content in artifacts.items():
            stage = str(raw_stage)
            if not self._has_content(content):
                continue
            versions = self.transport.get_stage_versions(self.progress.app_id, stage)
            current = self._current_version(versions)
            if current is None or current.get("version_no") is None:
                self._raise_failed(f"{stage} 산출물의 현재 버전을 찾을 수 없습니다.")
            version_no = int(current["version_no"])
            stored = self.transport.get_stage_version(
                self.progress.app_id, stage, version_no
            )
            digest = self._digest(stored.get("content"))
            references[stage] = ArtifactReference(stage, version_no, digest)
            self.progress.artifact_versions[stage] = version_no

        for artifact_type in FILE_ARTIFACT_TYPES:
            snapshot = self.transport.get_file_artifact(
                self.progress.app_id, artifact_type
            )
            if not snapshot:
                continue
            version_no = int(snapshot.get("version_no") or 0)
            if version_no <= 0:
                self._raise_failed(f"{artifact_type} 파일 산출물의 버전이 없습니다.")
            self._check_file_provenance(artifact_type, snapshot)
            files = snapshot.get("files")
            if not isinstance(files, list):
                self._raise_failed(f"{artifact_type} 파일 목록의 형식이 잘못되었습니다.")
            verified_count = 0
            for item in files:
                if not isinstance(item, dict) or not item.get("path"):
                    self._raise_failed(f"{artifact_type} 파일 항목의 경로가 없습니다.")
                if verify_files:
                    loaded = self.transport.get_artifact_file(
                        self.progress.app_id, artifact_type, str(item["path"])
                    )
                    content = str(loaded.get("content") or "")
                    observed = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    expected = str(item.get("sha256") or loaded.get("sha256") or "")
                    if not expected or observed != expected:
                        self._raise_failed(
                            f"{artifact_type}/{item['path']} 파일의 SHA-256이 다릅니다."
                        )
                    verified_count += 1
            digest = str(snapshot.get("snapshot_digest") or "")
            if not digest:
                digest_source = "".join(
                    f"{item['path']}\0{item.get('sha256', '')}\n"
                    for item in sorted(files, key=lambda value: str(value.get("path") or ""))
                )
                digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
            references[artifact_type] = ArtifactReference(
                artifact_type=artifact_type,
                version_no=version_no,
                digest=digest,
                file_count=len(files),
                verified_file_count=verified_count,
            )
            self.progress.artifact_versions[artifact_type] = version_no
        return references

    def _check_file_provenance(
        self, artifact_type: str, snapshot: Mapping[str, Any]
    ) -> None:
        """파일 snapshot이 이번 실행의 구현 작업에서 만들어졌는지 확인한다."""
        metadata = snapshot.get("metadata")
        if not isinstance(metadata, dict):
            self._raise_failed(
                f"{artifact_type}에 implementation job 출처 정보가 없습니다."
            )
        observed = metadata.get("implementation_job_id")
        expected = self.progress.implementation_job_id
        if not expected:
            self._raise_failed(
                f"{artifact_type}의 출처와 비교할 implementation job ID가 없습니다."
            )
        if not observed:
            self._raise_failed(
                f"{artifact_type}에 implementation_job_id가 없습니다."
            )
        if str(observed) != expected:
            self._raise_failed(
                f"{artifact_type}가 다른 implementation job에서 만들어졌습니다."
            )
