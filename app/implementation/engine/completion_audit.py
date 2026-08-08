"""Compatibility alias for workflow completion auditing."""

import sys

from .workflows import completion as _completion

sys.modules[__name__] = _completion
