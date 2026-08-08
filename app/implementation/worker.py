"""Compatibility alias for implementation job application services."""

import sys

from .application import jobs as _jobs

sys.modules[__name__] = _jobs
