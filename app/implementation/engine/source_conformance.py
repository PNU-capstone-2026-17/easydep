"""Compatibility alias for generated source conformance checks."""

import sys

from .workflows import conformance as _conformance

sys.modules[__name__] = _conformance
