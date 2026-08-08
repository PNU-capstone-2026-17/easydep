"""Compatibility alias for the implementation intermediate representation."""

import sys

from .domain import implementation_ir as _implementation_ir

sys.modules[__name__] = _implementation_ir
