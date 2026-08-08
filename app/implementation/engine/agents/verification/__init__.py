"""Language-specific verification gates for implementation agents."""

from .frontend import (
    frontend_contract_violations,
    has_mutating_operations,
    run_frontend_verification,
)

__all__ = [
    "frontend_contract_violations",
    "has_mutating_operations",
    "run_frontend_verification",
]
