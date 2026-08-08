"""Compatibility alias for implementation HTTP routes."""

import sys

from .interfaces import http as _http

sys.modules[__name__] = _http
