"""Opt-in local OpenTelemetry spans with payload-safe attributes only."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanContext

_TRACER = trace.get_tracer("easydep")
_CONFIGURED = False


def configure_local_tracing(exporter: Any | None = None) -> None:
    """Enable local tracing explicitly; defaults to a batched console exporter."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": "easydep"}))
    provider.add_span_processor(
        BatchSpanProcessor(exporter if exporter is not None else ConsoleSpanExporter())
    )
    trace.set_tracer_provider(provider)
    _CONFIGURED = True


def configure_from_environment() -> None:
    """Enable the local console exporter only when explicitly requested."""
    if os.getenv("EASYDEP_OTEL_CONSOLE", "").lower() in {"1", "true", "yes"}:
        configure_local_tracing()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Create a span; disabled API providers make this a cheap no-op by default."""
    with _TRACER.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as active:
        for key, value in attributes.items():
            if isinstance(value, (str, bool, int, float)):
                active.set_attribute(key, value)
        yield active


def current_span_context() -> SpanContext:
    return trace.get_current_span().get_span_context()


def event(name: str, **attributes: Any) -> None:
    """Add a payload-free structured event to the active span."""
    active = trace.get_current_span()
    safe_attributes = {
        key: value
        for key, value in attributes.items()
        if isinstance(value, (str, bool, int, float))
    }
    active.add_event(name, safe_attributes)


configure_from_environment()
