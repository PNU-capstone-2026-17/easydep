"""Compatibility alias for implementation workflow coordination."""

import sys

from .workflows import coordinator as _coordinator

sys.modules[__name__] = _coordinator
