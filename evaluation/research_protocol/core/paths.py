"""연구 프로토콜이 공유하는 저장소 내부 경로."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_ROOT = REPOSITORY_ROOT / "evaluation" / "research_protocol"
DEFINITION_ROOT = PROTOCOL_ROOT / "definitions"
MEASUREMENT_ROOT = PROTOCOL_ROOT / "measurements"
COMPONENT_CASE_ROOT = REPOSITORY_ROOT / "evaluation" / "baselines" / "component-cases"
ARTIFACT_ROOT = REPOSITORY_ROOT / "artifacts"

__all__ = [
    "ARTIFACT_ROOT",
    "COMPONENT_CASE_ROOT",
    "DEFINITION_ROOT",
    "MEASUREMENT_ROOT",
    "PROTOCOL_ROOT",
    "REPOSITORY_ROOT",
]
