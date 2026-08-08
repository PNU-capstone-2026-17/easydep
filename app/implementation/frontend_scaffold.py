"""Compatibility alias for frontend scaffold generation."""

import sys

from .engine.generation import frontend_scaffold as _frontend_scaffold

sys.modules[__name__] = _frontend_scaffold
