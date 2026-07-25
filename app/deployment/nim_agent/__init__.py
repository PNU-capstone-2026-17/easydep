"""nim_agent — OpenAI Agents SDK를 NVIDIA NIM 무료 엔드포인트로 구동하는 참조 스캐폴드."""

from .agent import build_agent
from .config import build_model

__all__ = ["build_agent", "build_model"]
