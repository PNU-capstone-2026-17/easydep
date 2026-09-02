"""workspace command는 MySQL에, 실시간 event는 bounded process memory에 둔다.

이 모듈은 action이 무엇을 실행할지 판단하지 않는다. command의 동시 실행 방지, 상태 저장과
시간 직렬화처럼 데이터베이스에 가까운 규칙만 담당하며 HTTP status code도 결정하지 않는다.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta, timezone
from itertools import count
from threading import RLock
from typing import Any

from sqlalchemy import select

from app.db.models import App, WorkspaceCommand
from app.db.session import session_scope

ACTIVE_STATUSES = {"QUEUED", "RUNNING"}
REQUIREMENTS_ARTIFACT_STAGES = {
    "refined_requirements",
    "capability_contract",
    "resource_intake",
    "usecase_spec",
    "usecase_diagram",
    "resource_spec",
}
DESIGN_ARTIFACT_STAGES = {
    "class_diagram",
    "sequence_diagram",
    "api_spec",
    "erd",
    "deployment_diagram",
}

KST = timezone(timedelta(hours=9), name="KST")
_EVENT_LIMIT_PER_APP = 1_000
# Use an epoch-based start so a browser's Last-Event-ID from before a process
# restart cannot hide newly emitted in-memory events behind a reset-to-one cursor.
_event_ids = count(int(datetime.now(UTC).timestamp() * 1_000_000))
_event_lock = RLock()
_events: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_EVENT_LIMIT_PER_APP))


def now() -> datetime:
    """MySQL의 timezone 없는 DATETIME 열에 넣을 UTC 현재 시각을 반환한다."""
    return datetime.now(UTC).replace(tzinfo=None)


def _timestamp_in_kst(value: datetime | None) -> str | None:
    """UTC로 저장한 DATETIME을 timezone이 표시된 한국 시각 문자열로 바꾼다.

    DB 값에는 timezone 정보가 없지만 EasyDep는 UTC로 저장한다는 규칙을 사용한다. 먼저 UTC를
    명시한 뒤 KST로 변환해야 단순히 9시간을 더하면서 생길 수 있는 중복 변환을 피할 수 있다.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC).astimezone(KST).isoformat()


def workflow_stage(stage: str | None) -> str:
    """세부 artifact stage를 UI가 사용하는 네 개의 큰 stage로 묶는다."""
    if stage in REQUIREMENTS_ARTIFACT_STAGES:
        return "requirements"
    if stage in DESIGN_ARTIFACT_STAGES:
        return "design"
    if stage in {"requirements", "design", "implementation", "testing"}:
        return str(stage)
    return "requirements"


def command_dict(row: WorkspaceCommand) -> dict[str, Any]:
    """ORM command 행을 DB Session 밖에서도 안전하게 쓸 수 있는 dict로 복사한다."""
    return {
        "command_id": row.command_id,
        "app_id": row.app_id,
        "action": row.action,
        "stage": row.stage,
        "status": row.status,
        "payload": row.payload or {},
        "result": row.result,
        "error": row.error,
        "created_at": _timestamp_in_kst(row.created_at),
        "started_at": _timestamp_in_kst(row.started_at),
        "completed_at": _timestamp_in_kst(row.completed_at),
    }


def event_dict(row: Any, *, include_llm_timings: bool = True) -> dict[str, Any]:
    """이전 ORM 형식의 event 객체를 현재 API 형식으로 바꾼다.

    원격 DB 정리 이후 새 event는 메모리에 저장하지만, 기존 호출부와 단위 테스트가 넘기는
    event 모양도 간단히 변환할 수 있도록 이 작은 호환 함수는 유지한다.
    """
    metadata = _event_metadata(
        getattr(row, "event_data", None) or {},
        include_llm_timings=include_llm_timings,
    )
    return {
        "event_id": row.event_id,
        "app_id": row.app_id,
        "command_id": row.command_id,
        "stage": row.stage,
        "kind": row.kind,
        "actor": row.actor,
        "text": row.text,
        "metadata": metadata,
        "created_at": _timestamp_in_kst(row.created_at),
    }


def _event_metadata(metadata: dict[str, Any], *, include_llm_timings: bool) -> dict[str, Any]:
    """목록 응답에서는 큰 LLM 원문 대신 개수만 남긴다."""
    result = dict(metadata)
    if not include_llm_timings and result.get("progress_event") == "designLlmMetrics":
        timings = result.pop("llm_timing_events", [])
        result["llm_timing_count"] = len(timings) if isinstance(timings, list) else 0
    return result


def create_command(
    command_id: str,
    app_id: str,
    action: str,
    stage: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """앱에 활성 command가 없을 때만 새 QUEUED command를 만든다.

    한 앱에서 두 command가 동시에 단계 state를 수정하면 checkpoint와 artifact 버전이 서로
    섞일 수 있다. 따라서 QUEUED 또는 RUNNING command가 있으면 새 command를 거절한다.
    """
    with session_scope() as session:
        # SELECT 후 INSERT만 하면 두 요청이 동시에 "활성 command 없음"을 보고 둘 다
        # 저장할 수 있다. 항상 존재하는 부모 app 행을 잠가 앱별 생성 절차를 직렬화한다.
        app = session.scalar(select(App).where(App.app_id == app_id).with_for_update())
        if app is None:
            raise KeyError(app_id)
        active = session.scalar(
            select(WorkspaceCommand)
            .where(
                WorkspaceCommand.app_id == app_id,
                WorkspaceCommand.status.in_(ACTIVE_STATUSES),
            )
            .limit(1)
        )
        if active is not None:
            raise RuntimeError(f"An active workspace command already exists: {active.command_id}")
        row = WorkspaceCommand(
            command_id=command_id,
            app_id=app_id,
            action=action,
            stage=stage,
            status="QUEUED",
            payload=payload,
        )
        session.add(row)
        session.flush()
        return command_dict(row)


def get_command(command_id: str) -> dict[str, Any] | None:
    """command ID로 한 건을 조회하며 없으면 `None`을 반환한다."""
    with session_scope() as session:
        row = session.get(WorkspaceCommand, command_id)
        return command_dict(row) if row is not None else None


def latest_command(
    app_id: str,
    *,
    exclude_command_id: str | None = None,
    stage: str | None = None,
) -> dict[str, Any] | None:
    """앱의 가장 최근 command를 조회한다.

    ``stage``를 지정하면 이후 단계가 실행됐더라도 해당 단계의 마지막 결과를 찾는다.
    """
    with session_scope() as session:
        query = select(WorkspaceCommand).where(WorkspaceCommand.app_id == app_id)
        if exclude_command_id:
            query = query.where(WorkspaceCommand.command_id != exclude_command_id)
        if stage:
            query = query.where(WorkspaceCommand.stage == stage)
        row = session.scalar(query.order_by(WorkspaceCommand.created_at.desc()).limit(1))
        return command_dict(row) if row is not None else None


def update_command(command_id: str, **changes: Any) -> dict[str, Any]:
    """command의 지정된 필드만 갱신하고 갱신 직후 snapshot을 반환한다."""
    with session_scope() as session:
        row = session.get(WorkspaceCommand, command_id)
        if row is None:
            raise KeyError(command_id)
        for key, value in changes.items():
            setattr(row, key, value)
        session.flush()
        return command_dict(row)


def append_event(
    app_id: str,
    *,
    stage: str,
    kind: str,
    actor: str,
    text: str,
    command_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """프로세스 메모리의 bounded workspace timeline에 event를 추가한다."""
    with session_scope() as session:
        if session.get(App, app_id) is None:
            raise KeyError(app_id)
        if command_id is not None:
            command = session.get(WorkspaceCommand, command_id)
            if command is None or command.app_id != app_id:
                raise KeyError(command_id)
    with _event_lock:
        event = {
            "event_id": next(_event_ids),
            "app_id": app_id,
            "command_id": command_id,
            "stage": stage,
            "kind": kind,
            "actor": actor,
            "text": text,
            "metadata": metadata or {},
            "created_at": _timestamp_in_kst(now()),
        }
        _events[app_id].append(event)
        return dict(event)


def list_events(
    app_id: str,
    *,
    after: int = 0,
    limit: int = 500,
    include_llm_timings: bool = True,
) -> list[dict[str, Any]]:
    """현재 process가 보유한 ``after`` 이후 event를 반환한다.

    서버 재시작 전 event는 의도적으로 복원하지 않는다. 최종 상태는 MySQL의 command에서
    복원하고, event history는 앱당 최근 1,000건으로 제한한다.
    """
    with _event_lock:
        events = [
            dict(event) for event in _events.get(app_id, ()) if int(event["event_id"]) > after
        ][:limit]
    for event in events:
        event["metadata"] = _event_metadata(
            event.get("metadata", {}),
            include_llm_timings=include_llm_timings,
        )
    return events


def get_event_llm_timings(
    app_id: str, event_id: int, *, offset: int = 0, limit: int = 20
) -> dict[str, Any]:
    """메모리에 남아 있는 설계 LLM 원문을 작은 page 단위로 반환한다."""
    with _event_lock:
        event = next(
            (item for item in _events.get(app_id, ()) if int(item["event_id"]) == event_id),
            None,
        )
        if event is None:
            raise KeyError(event_id)
        metadata = event.get("metadata", {})
        timings = metadata.get("llm_timing_events")
        if metadata.get("progress_event") != "designLlmMetrics" or not isinstance(timings, list):
            raise ValueError("The event does not contain design LLM timings.")
        page = list(timings[offset : offset + limit])
        total = len(timings)
    return {
        "event_id": event_id,
        "total": total,
        "offset": offset,
        "timings": page,
    }


def get_app_summary(app_id: str) -> dict[str, Any]:
    """workspace 첫 화면에 필요한 앱 식별자·현재 단계·생성 시각만 조회한다."""
    with session_scope() as session:
        row = session.get(App, app_id)
        if row is None:
            raise KeyError(app_id)
        return {
            "app_id": row.app_id,
            "current_stage": workflow_stage(row.current_stage),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def save_deployment_preferences(app_id: str, selection: dict[str, Any]) -> dict[str, Any]:
    """활성 command와 별개로 최신 배포 선택 초안을 insert 또는 update한다.

    사용자가 요구사항 분석 중에 지역을 바꿀 수 있으므로 이 값은 artifact 버전을 만들지 않는
    draft다. 다음 requirements command가 시작될 때 읽어 정식 resource 입력에 반영한다.
    """
    with session_scope() as session:
        app = session.get(App, app_id)
        if app is None:
            raise KeyError(app_id)
        app.deployment_preferences = selection
        session.flush()
        return dict(app.deployment_preferences or {})


def get_deployment_preferences(app_id: str) -> dict[str, Any] | None:
    """저장된 최신 배포 선택 초안을 반환한다."""
    with session_scope() as session:
        app = session.get(App, app_id)
        if app is None or app.deployment_preferences is None:
            return None
        return dict(app.deployment_preferences)


def list_workspace_apps(limit: int = 50) -> list[dict[str, Any]]:
    """사이드바에 표시할 최근 앱과 각 앱의 최신 command를 조회한다."""
    with session_scope() as session:
        latest_command_id = (
            select(WorkspaceCommand.command_id)
            .where(WorkspaceCommand.app_id == App.app_id)
            .order_by(
                WorkspaceCommand.created_at.desc(),
                WorkspaceCommand.command_id.desc(),
            )
            .limit(1)
            .correlate(App)
            .scalar_subquery()
        )
        rows = session.execute(
            select(App, WorkspaceCommand)
            .outerjoin(
                WorkspaceCommand,
                WorkspaceCommand.command_id == latest_command_id,
            )
            .order_by(App.created_at.desc())
            .limit(limit)
        ).all()
        result: list[dict[str, Any]] = []
        for app, command in rows:
            first_line = next(
                (
                    line.strip()
                    for line in (app.requirements_text or "").splitlines()
                    if line.strip()
                ),
                "",
            )
            result.append(
                {
                    "app_id": app.app_id,
                    "title": first_line[:72] or f"EasyDep app {app.app_id[:8]}",
                    "current_stage": (
                        command.stage if command is not None else workflow_stage(app.current_stage)
                    ),
                    "created_at": app.created_at.isoformat() if app.created_at else None,
                    "command": command_dict(command) if command is not None else None,
                }
            )
        return result


def interrupt_unfinished() -> int:
    """서버 재시작 전에 끝나지 않은 command를 INTERRUPTED로 표시한다.

    process-local worker는 재시작 후 존재하지 않으므로 QUEUED/RUNNING 상태를 그대로 두면 UI가
    영원히 진행 중으로 보인다. 성공으로 추정하지 않고, 검증된 checkpoint에서 재개하라는
    명시적인 오류를 남긴다.
    """
    changed = 0
    with session_scope() as session:
        rows = session.scalars(
            select(WorkspaceCommand).where(WorkspaceCommand.status.in_(ACTIVE_STATUSES))
        ).all()
        for row in rows:
            row.status = "INTERRUPTED"
            row.error = (
                "The server restarted and could not restore the in-flight command. "
                "Resume from a validated checkpoint."
            )
            row.completed_at = now()
            changed += 1
    return changed


def interrupted_testing_commands() -> list[dict[str, Any]]:
    """고정 입력이 저장되어 있어 안전하게 다시 실행할 수 있는 Testing 명령을 반환한다."""
    with session_scope() as session:
        rows = session.scalars(
            select(WorkspaceCommand).where(
                WorkspaceCommand.stage == "testing",
                WorkspaceCommand.status == "INTERRUPTED",
            )
        ).all()
        return [
            command_dict(row)
            for row in rows
            if isinstance((row.payload or {}).get("testing_checkpoint"), dict)
        ]
