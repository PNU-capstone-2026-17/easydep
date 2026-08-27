"""상호작용 설계 서비스가 수락한 불변 작업 단위다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.design.services.class_diagram.scenario import UseCase


@dataclass(frozen=True)
class Collision(Exception):
    """이미 수락된 연산과 다른 서명이 충돌했음을 나타낸다."""

    class_name: str
    operation_name: str

    def __str__(self) -> str:
        return f"operation signature collision: {self.class_name}.{self.operation_name}"


@dataclass(frozen=True)
class DataTypeCollision(Exception):
    """같은 이름의 로컬 타입 정의가 달라졌음을 나타낸다."""

    type_name: str

    def __str__(self) -> str:
        return f"DataType definition collision: {self.type_name}"


@dataclass(frozen=True)
class GroupResult:
    """하나의 실행 그룹에서 만든 협업 결과 또는 실패 원인이다."""

    group_id: str
    collaboration: dict[str, Any] | None
    issue: str = ""


@dataclass(frozen=True)
class OperationUnit:
    """동시 생성할 수 있는 하나의 유스케이스 실행 슬라이스다."""

    id: str
    use_case: UseCase
    step_ids: tuple[str, ...]
    execution_group_id: str = ""



