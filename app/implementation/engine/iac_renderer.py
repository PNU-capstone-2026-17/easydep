"""Compatibility alias for Terraform delivery rendering."""

import sys

from .delivery import terraform as _terraform

sys.modules[__name__] = _terraform
