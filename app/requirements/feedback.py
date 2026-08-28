"""Requirements feedback orchestration의 기존 import 경로를 보존하는 얇은 facade다."""

from app.requirements.modeling.feedback import classify_feedback, resolve_intent
from app.requirements.orchestration.feedback import apply_feedback, apply_feedback_upto

__all__ = [
    "apply_feedback",
    "apply_feedback_upto",
    "classify_feedback",
    "resolve_intent",
]
