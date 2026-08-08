"""Compatibility alias for cross-phase workflow repair planning."""

import sys

from .workflows import repair as _repair

sys.modules[__name__] = _repair
