"""Compatibility alias for implementation domain models."""

import sys

from .domain import models as _models

sys.modules[__name__] = _models
