"""Compatibility alias for implementation feedback eligibility."""

import sys

from .application import feedback as _feedback

sys.modules[__name__] = _feedback
