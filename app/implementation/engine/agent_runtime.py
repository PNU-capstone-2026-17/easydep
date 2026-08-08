"""Compatibility alias for :mod:`app.implementation.engine.agents.runtime`."""

import sys

from .agents import runtime as _runtime

sys.modules[__name__] = _runtime
