"""In-memory boundary around the member-owned design graph."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.orchestration.checkpoint import (
    DEFAULT_CHECKPOINT_PATH,
    SqliteMemorySaver,
)
from app.requirements.contracts.state import AgentState
from app.requirements.orchestration.supervisor import blocking_issues


class DesignContractError(RuntimeError):
    pass


def _duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _handoff_errors(requirements_result: dict[str, Any]) -> list[str]:
    """Return structural defects that make downstream design joins unsafe.

    Semantic findings remain advisory here because their LLM judgement is non-deterministic.
    This boundary rejects only facts that cannot be joined or specifications that do not contain
    the minimum executable black-box path. Orphan FRs and unattached NFRs are deliberately not
    errors: a cross-cutting policy may have no honest use-case attachment and is still preserved
    in ``requirements`` for the later design stages.
    """
    errors: list[str] = []
    actors = requirements_result.get("actors") or []
    use_cases = requirements_result.get("use_cases") or []
    specifications = requirements_result.get("use_case_specs") or []
    requirements = requirements_result.get("requirements") or []

    if not use_cases:
        errors.append("requirements analysis did not produce use_cases")
    if not specifications:
        errors.append("requirements analysis did not produce use_case_specs")
    if errors:
        return errors

    actor_names = {
        str(actor.get("name") or "").strip()
        for actor in actors
        if isinstance(actor, dict) and str(actor.get("name") or "").strip()
    }
    use_case_ids = [
        str(use_case.get("id") or "").strip()
        for use_case in use_cases
        if isinstance(use_case, dict)
    ]
    specification_ids = [
        str(specification.get("use_case_id") or "").strip()
        for specification in specifications
        if isinstance(specification, dict)
    ]
    if "" in use_case_ids:
        errors.append("use_cases contains an empty id")
    if "" in specification_ids:
        errors.append("use_case_specs contains an empty use_case_id")
    for duplicate in _duplicate_values(use_case_ids):
        errors.append(f"duplicate use_case id: {duplicate}")
    for duplicate in _duplicate_values(specification_ids):
        errors.append(f"duplicate use_case_spec id: {duplicate}")

    use_case_set = set(use_case_ids)
    specification_set = set(specification_ids)
    if use_case_set != specification_set:
        missing = sorted(use_case_set - specification_set)
        extra = sorted(specification_set - use_case_set)
        errors.append(f"use_case/use_case_spec id mismatch: missing={missing}, extra={extra}")

    known_requirement_ids = {
        str(requirement.get("id") or "").strip()
        for requirement in requirements
        if isinstance(requirement, dict) and str(requirement.get("id") or "").strip()
    }
    use_cases_by_id = {
        str(use_case.get("id") or "").strip(): use_case
        for use_case in use_cases
        if isinstance(use_case, dict) and str(use_case.get("id") or "").strip()
    }
    for use_case_id, use_case in use_cases_by_id.items():
        primary_actor = str(use_case.get("primary_actor") or "").strip()
        if actor_names and primary_actor not in actor_names:
            errors.append(f"{use_case_id} references unknown primary actor: {primary_actor!r}")
        referenced = {
            str(value).strip()
            for key in ("requirement_ids", "nfr_ids")
            for value in (use_case.get(key) or [])
            if str(value).strip()
        }
        if known_requirement_ids:
            for unknown in sorted(referenced - known_requirement_ids):
                errors.append(f"{use_case_id} references unknown requirement: {unknown}")

    for specification in specifications:
        if not isinstance(specification, dict):
            errors.append("use_case_specs contains a non-object item")
            continue
        use_case_id = str(specification.get("use_case_id") or "").strip()
        if specification.get("generated") is False:
            errors.append(f"{use_case_id} specification generation failed")
        if not str(specification.get("trigger") or "").strip():
            errors.append(f"{use_case_id} specification has no trigger")
        if not specification.get("main_scenario"):
            errors.append(f"{use_case_id} specification has no main scenario")
        if not specification.get("success_guarantee"):
            errors.append(f"{use_case_id} specification has no success guarantee")

        use_case = use_cases_by_id.get(use_case_id) or {}
        accepted_ids = {
            str(value).strip()
            for value in (use_case.get("requirement_ids") or [])
            if str(value).strip()
        }
        for step in specification.get("main_scenario") or []:
            if not isinstance(step, dict):
                errors.append(f"{use_case_id} main scenario contains a non-object step")
                continue
            for requirement_id in step.get("covered_req_ids") or []:
                normalized = str(requirement_id).strip()
                if normalized and normalized not in accepted_ids:
                    errors.append(
                        f"{use_case_id} step references non-local requirement: {normalized}"
                    )
        for field_name in ("success_guarantee", "minimal_guarantee"):
            for guarantee in specification.get(field_name) or []:
                if not isinstance(guarantee, dict):
                    errors.append(f"{use_case_id} {field_name} contains a non-object item")
                    continue
                if not str(guarantee.get("sentence") or "").strip():
                    errors.append(f"{use_case_id} {field_name} contains an empty sentence")
                for requirement_id in guarantee.get("covered_req_ids") or []:
                    normalized = str(requirement_id).strip()
                    if normalized and normalized not in accepted_ids:
                        errors.append(
                            f"{use_case_id} {field_name} references non-local requirement: {normalized}"
                        )

    reported_unknown = (requirements_result.get("coverage") or {}).get(
        "unknown_requirement_refs"
    ) or []
    for requirement_id in reported_unknown:
        errors.append(f"coverage reports unknown requirement: {requirement_id}")
    reported_unknown_use_cases = (requirements_result.get("coverage") or {}).get(
        "unknown_use_case_refs"
    ) or []
    for use_case_id in reported_unknown_use_cases:
        errors.append(f"coverage reports unknown use case: {use_case_id}")
    return list(dict.fromkeys(errors))


class DesignAdapter:
    """Own a private graph instance without using the design MySQL saver."""

    def __init__(self, checkpoint_path=DEFAULT_CHECKPOINT_PATH) -> None:
        from app.design.graphs.design_graph import build_design_graph

        self.graph = build_design_graph(SqliteMemorySaver(checkpoint_path, "design"))
        self._timings: dict[str, list[dict[str, Any]]] = {}

    def timing_events(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._timings.get(session_id) or [])

    def _invoke_with_timings(self, session_id: str, callable_obj):
        from app.design.services.common.structured import capture_llm_timings

        events: list[dict[str, Any]] = []
        try:
            with capture_llm_timings() as events:
                return callable_obj()
        finally:
            # start/retry/resume is one observable invocation. Returning a session
            # accumulation here made resume telemetry count the preceding call twice.
            self._timings[session_id] = list(events)

    @staticmethod
    @contextmanager
    def _without_plantuml_jvm():
        """Skip only the JVM-backed syntax check for orchestration runs."""
        from app.design.services.common import validation

        original = validation.check_plantuml_syntax
        validation.check_plantuml_syntax = lambda _source: []
        try:
            yield
        finally:
            validation.check_plantuml_syntax = original

    @staticmethod
    def _state(requirements_result: dict[str, Any]) -> dict[str, Any]:
        errors = [
            *blocking_issues(cast(AgentState, requirements_result)),
            *_handoff_errors(requirements_result),
        ]
        if errors:
            raise DesignContractError("; ".join(dict.fromkeys(errors)))
        requirements = requirements_result.get("requirements") or []
        relationships = requirements_result.get("relationships") or {}
        requirement_trace = requirements_result.get("traceability") or {}
        return {
            "refined_requirements": requirements,
            "capability_contract": requirements_result.get("capability_contract") or {},
            "resource_intake": requirements_result.get("resource_intake") or {},
            "usecase_spec": {
                "actors": requirements_result.get("actors") or [],
                "use_cases": requirements_result.get("use_cases") or [],
                "use_case_specs": requirements_result.get("use_case_specs") or [],
                "relationships": relationships,
                "traceability": requirement_trace,
            },
            "relationships": relationships,
            "usecase_diagram_puml": requirements_result.get("diagram") or "",
            "resource_spec": requirements_result.get("resource_spec") or {},
        }

    @staticmethod
    def _payload(
        result: dict[str, Any], session_id: str, timing_events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        from app.artifacts_api import to_web_response

        payload: dict[str, Any] = {
            "app_id": session_id,
            **to_web_response(result),
            "llm_timing_events": timing_events,
        }
        validation = payload.get("validation") or {}
        for name in ("class_diagram", "sequence_diagram", "erd", "deployment_diagram"):
            validation[name] = {
                "valid": None,
                "errors": ["PlantUML JVM syntax validation was skipped."],
            }
        payload["validation"] = validation
        payload["plantuml_validation"] = {"status": "skipped", "requires_jvm": True}
        # Keep structured sources for orchestration-owned post-processing. The web
        # response intentionally exposes rendered artifacts only, but cloud design
        # needs the deployment model to distinguish stateless and stateful layouts.
        for key in (
            "extracted_bce_classes",
            "sequence_diagram_model",
            "api_spec_model",
            "erd_bce_classes",
            "deployment_diagram_model",
            "deployment_diagram_bundle",
            "deployment_workload_graph",
            "deployment_plan",
            "deployment_topology",
            "deployment_resource_plan",
            "deployment_diagram_provisioning_puml",
        ):
            if result.get(key):
                payload[key] = result[key]
        interruptions = result.get("__interrupt__") or []
        if interruptions:
            value = interruptions[0].value
            payload.update(
                status="need_feedback",
                stage=value.get("stage"),
                feedback_prompt=value.get("prompt"),
            )
        else:
            payload.update(status="completed", stage=None)
        return payload

    def start(self, *, session_id: str, requirements_result: dict[str, Any]) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        self._timings[session_id] = []
        with self._without_plantuml_jvm():
            result = dict(
                self._invoke_with_timings(
                    session_id,
                    lambda: self.graph.invoke(self._state(requirements_result), config),
                )
            )
        if not self.graph.get_state(config).next:
            result.pop("__interrupt__", None)
        return self._payload(result, session_id, self.timing_events(session_id))

    def has_pending(self, *, session_id: str) -> bool:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        return bool(self.graph.get_state(config).next)

    def retry_pending(self, *, session_id: str) -> dict[str, Any]:
        """Retry only the pending failed graph node without replacing prior state."""
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        if not self.graph.get_state(config).next:
            raise ValueError(f"Design session has no pending node: {session_id}")
        self._timings[session_id] = []
        with self._without_plantuml_jvm():
            result = dict(
                self._invoke_with_timings(
                    session_id,
                    lambda: self.graph.invoke(None, config),
                )
            )
        if not self.graph.get_state(config).next:
            result.pop("__interrupt__", None)
        return self._payload(result, session_id, self.timing_events(session_id))

    def resume(self, *, session_id: str, feedback: str) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        with self._without_plantuml_jvm():
            result = dict(
                self._invoke_with_timings(
                    session_id,
                    lambda: self.graph.invoke(Command(resume=feedback), config),
                )
            )
        if not self.graph.get_state(config).next:
            result.pop("__interrupt__", None)
        return self._payload(result, session_id, self.timing_events(session_id))
