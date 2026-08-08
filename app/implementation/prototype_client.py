"""Compatibility alias for the prototype subprocess client."""

import sys

from .application import prototype as _prototype

sys.modules[__name__] = _prototype
