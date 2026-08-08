"""Compatibility alias for generated frontend planning contracts."""

import sys

from .planning import frontend_contracts as _frontend_contracts

sys.modules[__name__] = _frontend_contracts
