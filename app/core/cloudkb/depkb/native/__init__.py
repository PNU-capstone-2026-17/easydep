"""Provider-native discovery for evidence-first dependency analysis.

This package deliberately does not import ``depkb.vocabulary`` or the reviewed
neutral claim ledger.  Native provider material must be frozen before any
cross-provider alignment is attempted.
"""

from .model import validate_inventory

__all__ = ["validate_inventory"]
