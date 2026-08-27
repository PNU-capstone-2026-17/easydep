"""클래스 설계 단계 사이에서 교환하는 수락된 불변 경계다."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.design.schemas.class_model import Collaboration
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
class AcceptedInventory:
    """구조 단계가 연산 단계에 넘기는 고정 BCE 인벤토리다."""

    classes: tuple[Mapping[str, Any], ...]
    data_types: tuple[Mapping[str, Any], ...]
    relationships: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> AcceptedInventory:
        return cls(
            classes=tuple(deepcopy(item) for item in payload.get("Classes") or []),
            data_types=tuple(deepcopy(item) for item in payload.get("DataTypes") or []),
            relationships=tuple(
                deepcopy(item) for item in payload.get("Relationships") or []
            ),
        )

    def as_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "Classes": [deepcopy(dict(item)) for item in self.classes],
            "DataTypes": [deepcopy(dict(item)) for item in self.data_types],
            "Relationships": [deepcopy(dict(item)) for item in self.relationships],
        }


@dataclass(frozen=True)
class AcceptedFragment:
    """한 유스케이스가 소유한 수락된 연산·로컬 타입 조각이다."""

    use_case_id: str
    payload: Mapping[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return deepcopy(dict(self.payload))


@dataclass(frozen=True)
class CollaborationResult:
    """한 실행 그룹의 수락된 협업 또는 국소 수리 실패다."""

    group_id: str
    collaboration: Collaboration | None
    issue: str = ""

    @classmethod
    def accepted(cls, group_id: str, payload: Mapping[str, Any]) -> CollaborationResult:
        return cls(group_id, Collaboration.model_validate(payload))


@dataclass(frozen=True)
class CallDependency:
    """호출 트리에서 파생된 한 클래스 간 의존선이다."""

    source: str
    target: str
    type: str = "Dependency"

    def as_payload(self) -> dict[str, str]:
        return {"source": self.source, "target": self.target, "type": self.type}

    def get(self, key: str, default: str | None = None) -> str | None:
        """기존 UML 투영기가 읽는 최소 매핑 접근을 제공한다."""
        return self.as_payload().get(key, default)


@dataclass(frozen=True)
class OperationUnit:
    """동시 생성할 수 있는 하나의 유스케이스 실행 슬라이스다."""

    id: str
    use_case: UseCase
    step_ids: tuple[str, ...]
    execution_group_id: str = ""
