"""저장된 산출물에서 화면용 추적 조회 결과를 만든다.

이 모듈은 새로운 추적표를 저장하지 않는다. 현재 설계 state, 최신 source snapshot에
보관된 구현 RTM, 그리고 마지막 Testing 결과를 한 번 읽어 기존 순수 projection에
전달할 뿐이다. 그래서 조회가 과거 산출물의 관계를 바꾸지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.artifact_trace import TraceRef, format_ref, parse_ref
from app.artifact_trace_projection import project_artifact_trace
from app.db.models import TYPE_SOURCE_CODE
from app.repositories import artifact_repository
from app.workspace import repository as workspace_repository


class UnknownTraceRef(ValueError):
    """요청한 주소가 현재 고정 산출물 어디에도 없을 때 사용한다."""


def artifact_trace_response(app_id: str, ref_text: str | None = None) -> dict[str, Any]:
    """앱의 최신 읽기 전용 추적표와 선택 주소의 관계를 JSON으로 만든다."""

    state = artifact_repository.load_state(app_id)
    source_snapshot = artifact_repository.load_file_snapshot(app_id, TYPE_SOURCE_CODE)
    implementation_rtm = _implementation_rtm(source_snapshot)
    testing_command = workspace_repository.latest_command(app_id, stage="testing")
    testing_result = _testing_result(testing_command)
    trace = project_artifact_trace(state, implementation_rtm, testing_result)

    selected = parse_ref(ref_text) if ref_text else None
    if selected is not None and selected not in trace.refs:
        raise UnknownTraceRef(format_ref(selected))

    return {
        "app_id": app_id,
        "ref": format_ref(selected) if selected else None,
        "refs": _refs(trace.refs),
        "unknown_source_refs": _refs(trace.unknown_source_refs),
        "sources": _refs(trace.sources(selected)) if selected else [],
        "consumers": _refs(trace.consumers(selected)) if selected else [],
        "upstream": _refs(trace.upstream(selected)) if selected else [],
        "downstream": _refs(trace.downstream(selected)) if selected else [],
        "files": _refs(trace.files(selected)),
        "evidence": _refs(trace.evidence(selected)),
        "source_snapshot": _snapshot_summary(source_snapshot),
        "testing": _testing_summary(testing_command, testing_result),
    }


def _implementation_rtm(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """SOURCE_CODE snapshot metadata에 보관한 구현 RTM만 꺼낸다."""

    metadata = snapshot.get("metadata") if isinstance(snapshot, Mapping) else None
    traceability = (
        metadata.get("implementation_traceability")
        if isinstance(metadata, Mapping)
        else None
    )
    return dict(traceability) if isinstance(traceability, Mapping) else {}


def _testing_result(command: Mapping[str, Any] | None) -> dict[str, Any]:
    """가장 최근 command가 Testing 결과일 때만 작은 projection 입력으로 사용한다."""

    if not isinstance(command, Mapping) or command.get("stage") != "testing":
        return {}
    result = command.get("result")
    if not isinstance(result, Mapping):
        return {}
    job = result.get("job")
    if isinstance(job, Mapping) and isinstance(job.get("result"), Mapping):
        return dict(job["result"])
    return dict(result)


def _snapshot_summary(snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """파일 내용은 보내지 않고 어떤 source snapshot을 읽었는지만 표시한다."""

    if not isinstance(snapshot, Mapping):
        return None
    return {
        key: snapshot.get(key)
        for key in ("version_id", "version_no", "snapshot_digest", "created_at")
    }


def _testing_summary(
    command: Mapping[str, Any] | None, result: Mapping[str, Any]
) -> dict[str, Any] | None:
    """화면이 최신 Testing 결과인지 알 수 있게 최소 상태만 표시한다."""

    if not isinstance(command, Mapping) or command.get("stage") != "testing":
        return None
    return {
        "command_id": command.get("command_id"),
        "status": command.get("status"),
        "passed": result.get("passed"),
        "gate_status": result.get("gateStatus"),
    }


def _refs(values: tuple[TraceRef, ...]) -> list[str]:
    """typed 주소를 API가 바로 돌려줄 안정 문자열 목록으로 바꾼다."""

    return [format_ref(value) for value in values]


__all__ = ["UnknownTraceRef", "artifact_trace_response"]
