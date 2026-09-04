"""프론트엔드와 같은 공개 API로 EasyDep 흐름을 한 번 실행한다.

이 모듈은 평가 플랫폼이 아니라 얇은 HTTP 실행기다. 앱을 만들고, Workspace 상태와
이벤트를 읽고, 프론트엔드 자동 모드가 누르는 것과 같은 버튼을 command API로 보낸다.
완료 뒤에는 공개 산출물 응답을 가공하지 않고 그대로 돌려준다. 품질 점수나 통계 계산은
이 실행기의 책임이 아니다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

JsonObject = dict[str, Any]
STAGES = ("requirements", "design", "implementation", "testing")


class ProductScenarioHttpError(RuntimeError):
    """공개 API가 성공 범위가 아닌 HTTP 상태를 반환했다."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


@runtime_checkable
class ProductScenarioTransport(Protocol):
    """실행기가 사용하는 공개 API의 작은 목록이다."""

    def create_app(self, message: str) -> Mapping[str, Any]: ...

    def get_workspace(self, app_id: str) -> Mapping[str, Any]: ...

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]: ...

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CloudCoordinates:
    """앱 생성 화면이 요구사항과 별도로 받는 배포 좌표다.

    요구사항 원문에 리전이 문장으로 적혀 있어도 분석은 구조화된 값을 따로 묻는다.
    자동 실행은 사람 대신 답을 지어내지 않으므로, 이 값을 미리 넘겨야 끝까지 간다.
    """

    provider: str | None = None
    region: str = ""
    monthly_budget_amount: float | None = None
    monthly_budget_currency: str = "USD"
    resource_constraints_text: str = ""

    def as_create_fields(self) -> JsonObject:
        fields: JsonObject = {}
        if self.provider:
            fields["provider"] = self.provider
        if self.region:
            fields["region"] = self.region
        if self.monthly_budget_amount is not None:
            fields["monthly_budget_amount"] = self.monthly_budget_amount
            fields["monthly_budget_currency"] = self.monthly_budget_currency
        if self.resource_constraints_text:
            fields["resource_constraints_text"] = self.resource_constraints_text[:12000]
        return fields


class HttpProductScenarioTransport:
    """프론트엔드가 사용하는 URL에 표준 라이브러리로 연결한다."""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout_seconds: float = 30.0,
        cloud: CloudCoordinates | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds
        self.cloud = cloud

    def _url(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        url = f"{self.base_url}{path}"
        return f"{url}?{urlencode(query)}" if query else url

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> JsonObject:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with urlopen(  # noqa: S310 - 사용자가 지정한 EasyDep 서버에 연결한다.
                request, timeout=self.request_timeout_seconds
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
            raise TypeError(f"{path} 응답은 JSON 객체여야 합니다.")
        return payload

    def create_app(self, message: str) -> Mapping[str, Any]:
        # 앱 생성 화면은 요구사항과 함께 배포 좌표도 받는다. 좌표를 주지 않으면 요구사항
        # 분석이 리전을 되묻고 멈추므로, 무인 실행에서는 호출자가 미리 넘긴다.
        body: JsonObject = {"message": message}
        if self.cloud is not None:
            body.update(self.cloud.as_create_fields())
        return self._json_request("POST", "/api/workspace/apps", body=body)

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        return self._json_request(
            "GET", f"/api/workspace/apps/{quote(app_id, safe='')}"
        )

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._json_request(
            "POST",
            f"/api/workspace/apps/{quote(app_id, safe='')}/commands",
            body=payload,
        )

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]:
        """EventSource와 같은 SSE 주소에서 새 이벤트 한 건을 읽는다."""
        path = f"/api/workspace/apps/{quote(app_id, safe='')}/events"
        request = Request(
            self._url(path, {"after": after}),
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        data_lines: list[str] = []
        try:
            with urlopen(  # noqa: S310 - 사용자가 지정한 EasyDep 서버에 연결한다.
                request, timeout=max(0.05, timeout_seconds)
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        payload = json.loads("\n".join(data_lines))
                        if not isinstance(payload, dict):
                            raise TypeError("Workspace SSE data는 JSON 객체여야 합니다.")
                        return [payload]
        except TimeoutError:
            return []
        return []

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]:
        return self._json_request("GET", f"/api/apps/{quote(app_id, safe='')}")


@dataclass(frozen=True)
class AutoAction:
    """자동 모드가 공개 command API로 보낼 한 번의 버튼 동작이다."""

    action: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    def payload(self) -> JsonObject:
        return {"action": self.action, **dict(self.extra)}


def next_auto_action(command: Mapping[str, Any] | None) -> AutoAction | None:
    """서버가 공개한 목록에서 자동 실행 가능하다고 표시한 첫 action을 고른다."""
    if not command:
        return None
    raw_result = command.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}
    actions = result.get("actions")
    if not isinstance(actions, list):
        return None
    for offer in actions:
        if not isinstance(offer, dict) or offer.get("auto_selectable") is not True:
            continue
        action = str(offer.get("action") or "")
        payload = offer.get("payload")
        if action and isinstance(payload, dict):
            return AutoAction(action, payload)
    return None


@dataclass(frozen=True)
class ScenarioLocation:
    """실행이 끝나거나 멈춘 공개 Workspace 위치다."""

    app_id: str
    stage: str | None
    command_id: str | None
    status: str | None
    event_cursor: int
    reason: str = ""

    def as_dict(self) -> JsonObject:
        return asdict(self)


class ProductScenarioStopped(RuntimeError):
    """자동 실행이 끝까지 진행되지 못했다."""

    def __init__(self, location: ScenarioLocation) -> None:
        self.location = location
        super().__init__(json.dumps(location.as_dict(), ensure_ascii=False))


class ProductScenarioTimeout(ProductScenarioStopped):
    """지정한 전체 실행 시간을 넘겼다."""


class ProductScenarioNeedsInput(ProductScenarioStopped):
    """프론트엔드에서도 사람이 선택하거나 답해야 하는 상태에 도달했다."""


class ProductScenarioFailed(ProductScenarioStopped):
    """공개 command가 실패했거나 사용할 다음 동작이 없다."""


@dataclass(frozen=True)
class ProductScenarioResult:
    """후처리 도구가 사용할 가공하지 않은 제품 실행 결과다."""

    location: ScenarioLocation
    implementation_job_id: str | None
    testing_job_id: str | None
    workspace: Mapping[str, Any]
    artifacts: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...]

    @property
    def app_id(self) -> str:
        return self.location.app_id

    def as_dict(self) -> JsonObject:
        return {
            "location": self.location.as_dict(),
            "implementation_job_id": self.implementation_job_id,
            "testing_job_id": self.testing_job_id,
            "workspace": dict(self.workspace),
            "artifacts": dict(self.artifacts),
            "events": [dict(event) for event in self.events],
        }


class ProductScenarioRunner:
    """공개 Workspace API를 polling하며 프론트엔드 자동 동작을 대신 누른다."""

    def __init__(
        self,
        transport: ProductScenarioTransport,
        *,
        timeout_seconds: float = 7200.0,
        poll_interval_seconds: float = 1.0,
        event_wait_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds는 0보다 커야 합니다.")
        if poll_interval_seconds < 0 or event_wait_seconds < 0:
            raise ValueError("polling 대기 시간은 음수일 수 없습니다.")
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.event_wait_seconds = event_wait_seconds
        self.monotonic = monotonic
        self.sleep = sleep

    def run(
        self, message: str, *, stop_after_stage: str = "testing"
    ) -> ProductScenarioResult:
        """새 앱을 만들고 선택한 단계가 완료될 때까지 한 번 실행한다."""
        message = message.strip()
        if not message:
            raise ValueError("message는 비어 있을 수 없습니다.")
        if stop_after_stage not in STAGES:
            raise ValueError(f"지원하지 않는 완료 단계입니다: {stop_after_stage}")

        created = self.transport.create_app(message)
        app_id = str(created.get("app_id") or "")
        if not app_id:
            raise ValueError("앱 생성 응답에 app_id가 없습니다.")

        deadline = self.monotonic() + self.timeout_seconds
        cursor = 0
        events: list[Mapping[str, Any]] = []
        implementation_job_id: str | None = None
        testing_job_id: str | None = None
        raw_initial = created.get("command")
        last_command = raw_initial if isinstance(raw_initial, dict) else None

        while True:
            if self.monotonic() >= deadline:
                raise ProductScenarioTimeout(
                    self._location(app_id, last_command, cursor, "전체 실행 시간 초과")
                )
            workspace = self.transport.get_workspace(app_id)
            raw_command = workspace.get("command")
            command = raw_command if isinstance(raw_command, dict) else None
            if command is not None:
                last_command = command
                stage = str(command.get("stage") or "")
                raw_result = command.get("result")
                result = raw_result if isinstance(raw_result, dict) else {}
                job_id = result.get("job_id")
                if stage == "implementation" and job_id:
                    implementation_job_id = str(job_id)
                elif stage == "testing" and job_id:
                    testing_job_id = str(job_id)

            remaining = max(0.0, deadline - self.monotonic())
            incoming = self.transport.read_events(
                app_id, cursor, min(self.event_wait_seconds, remaining)
            )
            for event in incoming:
                event_id = event.get("event_id")
                if isinstance(event_id, int) and event_id > cursor:
                    cursor = event_id
                    events.append(dict(event))

            if command is None:
                self._pause(deadline)
                continue
            status = str(command.get("status") or "")
            stage = str(command.get("stage") or "")
            if status == "COMPLETED" and stage == stop_after_stage:
                return ProductScenarioResult(
                    location=self._location(app_id, command, cursor),
                    implementation_job_id=implementation_job_id,
                    testing_job_id=testing_job_id,
                    workspace=dict(workspace),
                    artifacts=dict(self.transport.get_artifacts(app_id)),
                    events=tuple(events),
                )
            if status in {"QUEUED", "PENDING", "RUNNING"}:
                self._pause(deadline)
                continue

            action = next_auto_action(command)
            if action is None:
                reason = str(command.get("error") or "사용자 선택이 필요합니다.")
                location = self._location(app_id, command, cursor, reason)
                if status == "AWAITING_INPUT":
                    raise ProductScenarioNeedsInput(location)
                raise ProductScenarioFailed(location)
            self.transport.submit_command(app_id, action.payload())

    def _pause(self, deadline: float) -> None:
        remaining = max(0.0, deadline - self.monotonic())
        self.sleep(min(self.poll_interval_seconds, remaining))

    @staticmethod
    def _location(
        app_id: str,
        command: Mapping[str, Any] | None,
        cursor: int,
        reason: str = "",
    ) -> ScenarioLocation:
        command = command or {}
        return ScenarioLocation(
            app_id=app_id,
            stage=str(command.get("stage")) if command.get("stage") else None,
            command_id=(
                str(command.get("command_id")) if command.get("command_id") else None
            ),
            status=str(command.get("status")) if command.get("status") else None,
            event_cursor=cursor,
            reason=reason,
        )
