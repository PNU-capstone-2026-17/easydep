"""Compatibility alias for Kubernetes delivery rendering."""

import sys

from .delivery import kubernetes as _kubernetes

sys.modules[__name__] = _kubernetes
