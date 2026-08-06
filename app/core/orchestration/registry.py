"""Explicit provider registry; provider switching never happens implicitly."""

from __future__ import annotations

from app.core.orchestration.contracts import ProviderKind, StepProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[tuple[str, ProviderKind], StepProvider] = {}

    def register(
        self, step: str, kind: ProviderKind, provider: StepProvider
    ) -> ProviderRegistry:
        self._providers[(step, kind)] = provider
        return self

    def resolve(self, step: str, kind: ProviderKind) -> StepProvider:
        try:
            return self._providers[(step, kind)]
        except KeyError as error:
            raise LookupError(
                f"No provider registered for step={step!r}, kind={kind.value!r}"
            ) from error

    def available(self) -> set[tuple[str, ProviderKind]]:
        return set(self._providers)
