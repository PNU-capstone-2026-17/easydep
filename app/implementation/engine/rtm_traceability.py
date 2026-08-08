"""Compatibility alias for implementation RTM traceability."""

import sys

from .workflows import traceability as _traceability

sys.modules[__name__] = _traceability
