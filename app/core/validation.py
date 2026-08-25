"""Shared contracts for deterministic validation."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

ArtifactT = TypeVar("ArtifactT")
ContextT = TypeVar("ContextT")

FindingOrigin = Literal["schema", "deterministic", "semantic"]
ValidationStatus = Literal["clean", "findings", "needs_input", "disabled", "error"]


class Finding(BaseModel):
    """One validation finding, independent of its domain presentation."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    message: str
    location: str | None = None
    requires_user_input: bool = False
    origin: FindingOrigin = "deterministic"

    def __init__(
        self,
        rule_id: str,
        message: str,
        location: str | None = None,
        requires_user_input: bool = False,
        origin: FindingOrigin = "deterministic",
        **data: Any,
    ) -> None:
        """Retain the compact positional construction used by existing checks."""
        super().__init__(
            rule_id=rule_id,
            message=message,
            location=location,
            requires_user_input=requires_user_input,
            origin=origin,
            **data,
        )


class ValidationReport(BaseModel):
    """Typed outcome from one validation lane."""

    model_config = ConfigDict(frozen=True)

    status: ValidationStatus
    findings: tuple[Finding, ...] = ()
    checked_rule_ids: tuple[str, ...] = ()
    unexamined_rule_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


CheckFn = Callable[[ArtifactT, ContextT], Sequence[Finding]]


@dataclass(frozen=True)
class CheckSpec(Generic[ArtifactT, ContextT]):
    """One deterministic rule and the pure function which judges it."""

    rule_id: str
    run: CheckFn[ArtifactT, ContextT]
    parallel_safe: bool = True


def _finding_key(finding: Finding) -> tuple[str, str, str, bool, FindingOrigin]:
    return (
        finding.rule_id,
        finding.location or "",
        finding.message,
        finding.requires_user_input,
        finding.origin,
    )


def _run_one(
    spec: CheckSpec[ArtifactT, ContextT], artifact: ArtifactT, context: ContextT
) -> tuple[tuple[Finding, ...], str | None]:
    try:
        findings = tuple(spec.run(artifact, context))
    except Exception as exc:  # noqa: BLE001 - report a broken check without hiding siblings
        return (), f"{spec.rule_id}: {type(exc).__name__}: {exc}"

    unexpected = sorted({finding.rule_id for finding in findings} - {spec.rule_id})
    if unexpected:
        return (), (
            f"{spec.rule_id}: check emitted findings for other rules: "
            f"{', '.join(unexpected)}"
        )
    return findings, None


def run_checks(
    checks: Sequence[CheckSpec[ArtifactT, ContextT]],
    artifact: ArtifactT,
    context: ContextT,
    *,
    parallel: bool = False,
    max_workers: int | None = None,
) -> ValidationReport:
    """Run checks and merge their findings in registration order.

    Parallel completion order is deliberately not observable in the report.
    """
    rule_ids = tuple(check.rule_id for check in checks)
    if parallel and any(not check.parallel_safe for check in checks):
        unsafe = ", ".join(check.rule_id for check in checks if not check.parallel_safe)
        return ValidationReport(
            status="error",
            checked_rule_ids=rule_ids,
            errors=(f"parallel execution requested for non-parallel checks: {unsafe}",),
        )

    if parallel and len(checks) > 1:
        workers = max(1, min(len(checks), max_workers or len(checks)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_run_one, check, artifact, context) for check in checks]
            results = [future.result() for future in futures]
    else:
        results = [_run_one(check, artifact, context) for check in checks]

    findings: list[Finding] = []
    seen: set[tuple[str, str, str, bool, FindingOrigin]] = set()
    errors: list[str] = []
    for emitted, error in results:
        if error:
            errors.append(error)
            continue
        for finding in emitted:
            key = _finding_key(finding)
            if key not in seen:
                seen.add(key)
                findings.append(finding)

    if errors:
        status: ValidationStatus = "error"
    elif findings:
        status = "needs_input" if all(finding.requires_user_input for finding in findings) else "findings"
    else:
        status = "clean"
    return ValidationReport(
        status=status,
        findings=tuple(findings),
        checked_rule_ids=rule_ids,
        errors=tuple(errors),
    )
