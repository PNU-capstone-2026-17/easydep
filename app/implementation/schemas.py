"""Compatibility alias for implementation HTTP schemas."""

import sys

from .interfaces import schemas as _schemas

sys.modules[__name__] = _schemas
