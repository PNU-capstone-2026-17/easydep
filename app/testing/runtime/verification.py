"""One verification pass: unit tests, static analysis, and dynamic checks.

The two entry points that test a generated application — the web testing API
and the orchestration step — both need the same three things done in the same
order, including bringing the application up for the dynamic stages and taking
it down again afterwards.  That sequence lives here once.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from app.metrics import langsmith as langsmith_metrics
from app.testing.graphs.testing_graph import create_testing_graph, initial_state
from app.testing.runtime.app_container import (
    ApplicationLaunchError,
    running_application,
)


def _launch(app_id: str, target_url: str):
    """Reuse a caller-supplied URL, otherwise start the stored application."""
    if target_url:
        return nullcontext((target_url, {"source": "caller"}))
    return running_application(app_id)


def run_verification_graph(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    manifests_dir: str = "",
    iac_dir: str = "",
    repair_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with langsmith_metrics.trace_scope(
        "easydep.testing.verification",
        metadata={
            "agent": "testing",
            "operation": "verification",
            "run_id": run_id,
            "app_id": app_id,
        },
    ):
        return _run_verification_graph(
            run_id=run_id,
            app_id=app_id,
            target_url=target_url,
            manifests_dir=manifests_dir,
            iac_dir=iac_dir,
            repair_history=repair_history,
        )


def _run_verification_graph(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    manifests_dir: str = "",
    iac_dir: str = "",
    repair_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the testing graph against a live instance of the stored application.

    A launch failure is reported, not raised: static analysis of the deployment
    and IaC artifacts is still worth having when the application cannot be
    started, and the dynamic stages skip themselves when no URL is available.
    """
    graph = create_testing_graph()
    application: dict[str, Any] = {}
    launch_error: str | None = None

    try:
        with _launch(app_id, target_url) as (url, application):
            result = graph.invoke(
                initial_state(
                    run_id=run_id,
                    app_id=app_id,
                    target_url=url,
                    manifests_dir=manifests_dir,
                    iac_dir=iac_dir,
                    repair_history=repair_history,
                )
            )
    except ApplicationLaunchError as error:
        launch_error = str(error)
        result = graph.invoke(
            initial_state(
                run_id=run_id,
                app_id=app_id,
                manifests_dir=manifests_dir,
                iac_dir=iac_dir,
                repair_history=repair_history,
            )
        )

    reports = {
        "static": result.get("static_report"),
        "iac": result.get("iac_report"),
        "dynamicFunctional": result.get("dynamic_functional_report"),
        "dynamicNfr": result.get("dynamic_nfr_report"),
    }
    blocking = blocking_reason(reports)
    return {
        "reports": reports,
        "application": application,
        "applicationLaunchError": launch_error,
        "errors": result.get("errors") or [],
        "passed": blocking is None,
        "blockingReason": blocking,
        "diagnostics": misconfiguration_diagnostics(reports),
    }


def blocking_reason(reports: dict[str, Any]) -> str | None:
    """Why this verification should fail the run, or ``None``.

    Only an executed-and-failed dynamic functional run blocks.  A skipped stage
    proved nothing but also disproved nothing, and static misconfiguration
    findings describe the deployment artifacts rather than whether the
    application behaves as the requirements say — both are reported instead.
    """
    report = reports.get("dynamicFunctional") or {}
    if str(report.get("status", "")).upper() == "FAILED":
        return str(report.get("reason") or "Dynamic functional tests failed.")
    return None


def misconfiguration_diagnostics(reports: dict[str, Any]) -> list[dict[str, str]]:
    """Static analysis findings, surfaced so a scan that ran is a scan somebody reads."""
    diagnostics = []
    for subject, key in (("DEPLOYMENT", "static"), ("IAC", "iac")):
        report = reports.get(key) or {}
        issues = report.get("issues") or []
        if report.get("status") == "UNAVAILABLE":
            # An unscanned artifact must not look like a clean one.
            diagnostics.append(
                {
                    "code": f"{subject}_NOT_SCANNED",
                    "message": str(report.get("message") or "Nothing was scanned."),
                }
            )
        elif issues:
            diagnostics.append(
                {
                    "code": f"{subject}_MISCONFIGURATION",
                    "message": f"Trivy found {len(issues)} issue(s): "
                    + "; ".join(issues[:5]),
                }
            )
    return diagnostics
