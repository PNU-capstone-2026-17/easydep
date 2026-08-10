"""멤버·도구 경계 어댑터의 지연 로딩 공개 API."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "CloudDesignAdapter": "cloud_design",
    "DesignAdapter": "design",
    "RequirementsAdapter": "requirements",
    "TestingAdapter": "testing",
    "VmDeliveryAdapter": "vm_delivery",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    return getattr(import_module(f"app.core.orchestration.adapters.{module}"), name)
