"""Language-specific verification gates for implementation agents."""

from .frontend import (
    frontend_contract_violations,
    has_mutating_operations,
    run_frontend_verification,
)
from .e2e import e2e_contract_violations

__all__ = [
    "frontend_contract_violations",
    "has_mutating_operations",
    "run_frontend_verification",
    "e2e_contract_violations",
]
