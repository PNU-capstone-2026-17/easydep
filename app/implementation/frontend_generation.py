"""Compatibility alias for frontend project generation."""

import sys

from .engine.generation import frontend as _frontend

sys.modules[__name__] = _frontend
