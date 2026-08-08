"""Compatibility alias for design-based implementation task planning."""

import sys

from .planning import design_context as _design_context

sys.modules[__name__] = _design_context
