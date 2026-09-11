"""Public catalog validator facade."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..catalog import CatalogValidationError, assemble_catalog, catalog_digest
from ..contracts import ValidatedOperationFragment


def validate(
    fragments: Iterable[ValidatedOperationFragment],
    *,
    inventory: Mapping[str, Any],
    scenario: Mapping[str, Any],
):
    try:
        return assemble_catalog(fragments, inventory=inventory, scenario=scenario)
    except CatalogValidationError as exc:
        return list(exc.findings)


__all__ = ["CatalogValidationError", "assemble_catalog", "catalog_digest", "validate"]
