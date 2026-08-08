"""Compatibility alias for agent frontend verification."""

import sys

from .agents.verification import frontend as _frontend

sys.modules[__name__] = _frontend
