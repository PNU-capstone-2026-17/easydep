"""Capability 계약의 기존 import 경로를 보존하는 얇은 facade다."""

from app.requirements.resources.capability_contract import (
    MODELED_DEPENDENCY_CAPABILITY_IDS,
    OUT_OF_SCOPE_DEPENDENCY_CAPABILITY_IDS,
    REALIZATION_CHANGING_FIELDS,
    RECOGNIZED_DEPENDENCY_CAPABILITY_IDS,
    SCHEMA_VERSION,
    CalibrationPoint,
    accepted_needs,
    calibrated_score,
    decide,
    fit_policy,
    link_dependency_capability,
    load_policy,
    requires_load_balanced_ingress,
    requires_persistent_storage,
    wilson_lower,
)

__all__ = [
    "MODELED_DEPENDENCY_CAPABILITY_IDS",
    "OUT_OF_SCOPE_DEPENDENCY_CAPABILITY_IDS",
    "REALIZATION_CHANGING_FIELDS",
    "RECOGNIZED_DEPENDENCY_CAPABILITY_IDS",
    "SCHEMA_VERSION",
    "CalibrationPoint",
    "accepted_needs",
    "calibrated_score",
    "decide",
    "fit_policy",
    "link_dependency_capability",
    "load_policy",
    "requires_load_balanced_ingress",
    "requires_persistent_storage",
    "wilson_lower",
]
