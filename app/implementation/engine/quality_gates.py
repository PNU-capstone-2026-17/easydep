"""Compatibility alias for agent end-to-end quality gates."""

import sys

from .agents.verification import e2e as _e2e

sys.modules[__name__] = _e2e
