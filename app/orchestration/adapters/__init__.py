"""멤버·도구 경계 어댑터의 지연 로딩 공개 API."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "CloudDesignAdapter": "app.orchestration.adapters.cloud_design",
    "DesignAdapter": "app.orchestration.adapters.design",
    "RequirementsAdapter": "app.orchestration.adapters.requirements",
    "TestingAdapter": "app.orchestration.adapters.testing",
    "VmDeliveryAdapter": "app.implementation.delivery.vm_delivery",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
