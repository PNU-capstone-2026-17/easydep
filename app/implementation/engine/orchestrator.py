"""Compatibility alias for deterministic source generation orchestration."""

import sys

from .generation import orchestrator as _orchestrator

sys.modules[__name__] = _orchestrator
