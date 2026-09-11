"""Public operation validator facade."""
from __future__ import annotations

from ..operations import (
    OperationContext,
    OperationValidationError,
    validate_operation_payload,
    validated_operation_fragment,
)


def validate(fragment, context: OperationContext):
    return validate_operation_payload(fragment, context)


def require_valid(fragment, context: OperationContext):
    return validated_operation_fragment(fragment, context)


validate_operations = validate


__all__ = [
    "OperationContext",
    "OperationValidationError",
    "require_valid",
    "validate",
    "validate_operation_payload",
    "validate_operations",
]
