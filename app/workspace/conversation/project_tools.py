"""대화형 에이전트가 사용하는 결정론적 읽기 전용 프로젝트 도구.

수정 후보는 최신 artifact state와 design/implementation RTM에서 만들고, Testing이 실제로
검사한 근거는 별도의 frozen trace 조회에서만 읽는다. 이 모듈은 LLM이나 단계 service를
호출하지 않고 저장된 산출물을 바꾸지도 않는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.artifact_trace import TraceRef
from app.artifact_trace_projection import project_artifact_trace
from app.artifact_trace_service import UnknownTraceRef, artifact_trace_response
from app.db.models import (
    TYPE_API_SPEC,
    TYPE_CLASS,
    TYPE_DEPLOYMENT,
    TYPE_REFINE_REQ,
    TYPE_SEQUENCE,
    TYPE_SOURCE_CODE,
    TYPE_USECASE_SPEC,
)
from app.design.rtm import build_design_rtm
from app.repositories import artifact_repository
from app.workspace import repository as workspace_repository
from app.workspace.actions import offered_actions

TraceView = Literal["editing", "testing-evidence"]
_MAX_CONTENT_STRING_CHARS = 4_000
_MAX_CONTENT_ITEMS = 50
_MAX_CONTENT_DEPTH = 6
_REDACTED = "[REDACTED]"
_SECRET_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "token",
}
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_ASSIGNED_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)"
    r"(\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_VERSION_BY_DESIGN_STAGE = {
    "class_diagram": TYPE_CLASS,
    "sequence_diagram": TYPE_SEQUENCE,
    "api_spec": TYPE_API_SPEC,
    "deployment_diagram": TYPE_DEPLOYMENT,
}


@dataclass(frozen=True, slots=True)
class _Element:
    ref: str
    name: str
    owner: str
    editable: bool
    artifact_type: str | None
    artifact_version_id: int | None
    artifact_version_no: int | None
    content: Any
    canonical_ref: str | None = None
    trace_alias: str | None = None

    def public(self, app_id: str, *, include_content: bool = False) -> dict[str, Any]:
        payload = {
            "app_id": app_id,
            "ref": self.ref,
            "name": self.name,
            "label": self.name,
            "owner": self.owner,
            "editable": self.editable,
            "canonical_ref": self.canonical_ref or self.ref,
            "artifact_type": self.artifact_type,
            "artifact_version_id": self.artifact_version_id,
            "artifact_version_no": self.artifact_version_no,
        }
        safe_content = _safe_content(self.content)
        if include_content:
            payload["content"] = safe_content
        else:
            payload["summary"] = _summary(safe_content)
        return payload


class _Catalog:
    def __init__(self, app_id: str, state: Mapping[str, Any], snapshot: Mapping[str, Any] | None):
        self.app_id = app_id
        self.state = state
        self.snapshot = snapshot or {}
        self.elements: dict[str, _Element] = {}
        self.aliases: dict[str, str] = {}
        self.design_rtm = build_design_rtm(dict(state))
        metadata = self.snapshot.get("metadata")
        implementation_traceability = (
            metadata.get("implementation_traceability")
            if isinstance(metadata, Mapping)
            else None
        )
        self.implementation_rtm = (
            dict(implementation_traceability)
            if isinstance(implementation_traceability, Mapping)
            else {}
        )

    def add(
        self,
        *,
        ref: str,
        name: str,
        owner: str,
        editable: bool,
        artifact_type: str | None,
        content: Any,
        canonical_ref: str | None = None,
        trace_alias: str | None = None,
    ) -> None:
        version_id, version_no = self.version(artifact_type)
        element = _Element(
            ref=ref,
            name=name,
            owner=owner,
            editable=editable,
            artifact_type=artifact_type,
            artifact_version_id=version_id,
            artifact_version_no=version_no,
            content=content,
            canonical_ref=canonical_ref,
            trace_alias=trace_alias,
        )
        previous = self.elements.get(ref)
        if previous is not None and previous.owner != owner:
            raise ValueError(f"public ref has conflicting owners: {ref}")
        self.elements[ref] = element
        if trace_alias and trace_alias != ref:
            self.aliases[trace_alias] = ref

    def version(self, artifact_type: str | None) -> tuple[int | None, int | None]:
        if artifact_type == TYPE_SOURCE_CODE and self.snapshot:
            return _int_or_none(self.snapshot.get("version_id")), _int_or_none(
                self.snapshot.get("version_no")
            )
        versions = self.state.get("artifact_versions")
        version = versions.get(artifact_type) if isinstance(versions, Mapping) else None
        if not isinstance(version, Mapping):
            return None, None
        return _int_or_none(version.get("version_id")), _int_or_none(
            version.get("version_no")
        )

    def resolve(self, ref: str) -> _Element | None:
        direct = self.elements.get(ref)
        if direct:
            return direct
        canonical = self.aliases.get(ref)
        return self.elements.get(canonical) if canonical else None


def _build_catalog(app_id: str) -> _Catalog:
    state = artifact_repository.load_state(app_id)
    snapshot = artifact_repository.load_file_snapshot(app_id, TYPE_SOURCE_CODE)
    catalog = _Catalog(app_id, state, snapshot)
    _add_requirements(catalog)
    _add_design(catalog)
    _add_implementation(catalog)
    _add_findings(catalog)
    return catalog


def _add_requirements(catalog: _Catalog) -> None:
    requirements_value = catalog.state.get("refined_requirements")
    requirements = _records(
        _mapping(requirements_value).get("requirements")
        if isinstance(requirements_value, Mapping)
        else requirements_value
    )
    for item in requirements:
        identifier = _identifier(item, "id")
        if identifier:
            catalog.add(
                ref=f"requirement:{identifier}",
                name=_display_name(item, identifier),
                owner="requirements",
                editable=True,
                artifact_type=TYPE_REFINE_REQ,
                content=item,
            )

    usecase = _mapping(catalog.state.get("usecase_spec"))
    for item in _records(usecase.get("actors")):
        name = _identifier(item, "name")
        if name:
            catalog.add(
                ref=f"actor:{name}",
                name=name,
                owner="requirements",
                editable=True,
                artifact_type=TYPE_USECASE_SPEC,
                content=item,
            )
    for item in _records(usecase.get("use_cases")):
        identifier = _identifier(item, "id")
        if identifier:
            catalog.add(
                ref=f"use_case:{identifier}",
                name=_display_name(item, identifier),
                owner="requirements",
                editable=True,
                artifact_type=TYPE_USECASE_SPEC,
                content=item,
            )
    for item in _records(usecase.get("use_case_specs")):
        identifier = _identifier(item, "use_case_id")
        if identifier:
            catalog.add(
                ref=f"use_case_spec:{identifier}",
                name=_display_name(item, identifier),
                owner="requirements",
                editable=True,
                artifact_type=TYPE_USECASE_SPEC,
                content=item,
            )
    relationships = _mapping(usecase.get("relationships"))
    for relation_kind, values in sorted(relationships.items()):
        for index, item in enumerate(_records(values), start=1):
            identity = _relationship_identity(str(relation_kind), item, index)
            catalog.add(
                ref=f"relationship:{identity}",
                name=identity,
                owner="requirements",
                editable=True,
                artifact_type=TYPE_USECASE_SPEC,
                content=item,
            )


def _add_design(catalog: _Catalog) -> None:
    content_by_ref, aliases_by_ref = _design_content(catalog.state)
    plans = {
        str(item.get("ref")): item
        for item in _records(catalog.design_rtm.get("change_plan"))
        if item.get("ref")
    }
    rows = {
        f"{item.get('stage')}:{item.get('element')}": item
        for item in _records(catalog.design_rtm.get("rows"))
        if item.get("stage") and item.get("element")
    }
    for ref, plan in sorted(plans.items()):
        stage, _, element = ref.partition(":")
        aliases = aliases_by_ref.get(ref, [])
        trace_alias = aliases[0] if aliases else None
        catalog.add(
            ref=ref,
            name=element,
            owner="design",
            editable=True,
            artifact_type=_VERSION_BY_DESIGN_STAGE.get(stage),
            content=content_by_ref.get(ref, rows.get(ref, plan)),
            trace_alias=trace_alias,
        )
        for alias in aliases:
            catalog.aliases[alias] = ref

    # ERD는 class의 결정론적 투영이므로 읽을 수는 있지만 직접 수정 대상으로 공개하지 않는다.
    for entity in _records(_mapping(catalog.state.get("erd_bce_classes")).get("Classes")):
        name = _identifier(entity, "className")
        if not name:
            continue
        class_target = f"class_diagram:{name}"
        catalog.add(
            ref=f"entity:{name}",
            name=name,
            owner="design",
            editable=False,
            artifact_type=TYPE_CLASS,
            content=entity,
            canonical_ref=class_target if class_target in catalog.elements else None,
        )

    # 현재 deployment bundle의 workload/resource projection도 프로젝트 질문에서 읽을 수
    # 있게 한다. 다만 design RTM change_plan에 없는 항목은 실제 revise service가 받을 수
    # 없으므로 편집 가능하다고 꾸미지 않는다.
    bundle = _mapping(catalog.state.get("deployment_diagram_bundle"))
    graph = _mapping(bundle.get("workloadGraph"))
    for workload in _records(graph.get("workloads")):
        identifier = _identifier(workload, "id")
        if not identifier:
            continue
        canonical = f"deployment_diagram:{identifier}"
        if canonical not in catalog.elements:
            catalog.add(
                ref=f"workload:{identifier}",
                name=identifier,
                owner="design",
                editable=False,
                artifact_type=TYPE_DEPLOYMENT,
                content=workload,
                canonical_ref=None,
            )
    for projection in _records(bundle.get("projections")):
        provider = _identifier(projection, "provider")
        region = _identifier(projection, "region")
        if not provider or not region:
            continue
        plan = _mapping(projection.get("resourcePlan"))
        for collection, values in sorted(plan.items()):
            for resource in _records(values):
                identifier = _identifier(resource, "id") or _identifier(resource, "ruleId")
                if not identifier:
                    continue
                ref = f"resource:{provider}:{region}:{collection}:{identifier}"
                catalog.add(
                    ref=ref,
                    name=identifier,
                    owner="design",
                    editable=False,
                    artifact_type=TYPE_DEPLOYMENT,
                    content=resource,
                )


def _design_content(
    state: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    content: dict[str, Any] = {}
    aliases: dict[str, list[str]] = {}
    model = _mapping(state.get("extracted_bce_classes"))
    for item in _records(model.get("Classes")):
        name = _identifier(item, "className")
        if not name:
            continue
        canonical = f"class_diagram:{name}"
        content[canonical] = item
        aliases[canonical] = [f"class:{name}"]
        for operation in _records(item.get("operations")):
            operation_id = _identifier(operation, "operationId")
            if operation_id:
                ref = f"class_diagram:{operation_id}"
                content[ref] = operation
                aliases[ref] = [f"operation:{operation_id}"]
    for collaboration in _records(model.get("Collaborations")):
        identifier = _identifier(collaboration, "collaborationId")
        if identifier:
            ref = f"class_diagram:{identifier}"
            content[ref] = collaboration
            aliases[ref] = [f"collaboration:{identifier}"]
        for call in _records(collaboration.get("calls")):
            call_id = _identifier(call, "callId")
            if call_id:
                ref = f"class_diagram:{call_id}"
                content[ref] = call
                aliases[ref] = [f"call:{call_id}"]

    sequence = _mapping(state.get("sequence_diagram_model"))
    diagrams = _records(sequence.get("Diagrams")) or [sequence]
    for diagram in diagrams:
        use_case_id = _identifier(diagram, "use_case_id")
        if not use_case_id:
            continue
        ref = f"sequence_diagram:{use_case_id}"
        content[ref] = diagram
        aliases[ref] = [f"sequence:{use_case_id}"]
        # 개별 message ref는 관계 조회 alias다. 실제 수정 입력은 UC 단위 sequence ref다.
        for message in _records(diagram.get("Messages")):
            message_id = _identifier(message, "call_id") or _identifier(message, "reply_to")
            if message_id:
                aliases[ref].append(f"message:{message_id}")

    api = _mapping(state.get("api_spec_model"))
    for endpoint in _records(api.get("Endpoints")):
        identifier = _identifier(endpoint, "operation_id")
        if not identifier:
            method = _identifier(endpoint, "method")
            path = _identifier(endpoint, "path")
            identifier = f"{method.upper()} {path}" if method and path else None
        if identifier:
            ref = f"api_spec:{identifier}"
            content[ref] = endpoint
            aliases[ref] = [f"api:{identifier}"]
    for schema in _records(api.get("Schemas")):
        name = _identifier(schema, "name")
        if name:
            ref = f"api_spec:{name}"
            content[ref] = schema
            aliases[ref] = [f"schema:{name}"]

    deployment = _mapping(state.get("deployment_diagram_model"))
    for collection in ("Nodes", "Artifacts"):
        for item in _records(deployment.get(collection)):
            name = _identifier(item, "name")
            if name:
                ref = f"deployment_diagram:{name}"
                content[ref] = item
                aliases[ref] = [f"workload:{name}"]
    return content, aliases


def _add_implementation(catalog: _Catalog) -> None:
    files = catalog.snapshot.get("files")
    files = files if isinstance(files, Mapping) else {}
    for mapping in _records(catalog.implementation_rtm.get("mappings")):
        task_id = _identifier(mapping, "taskId")
        target_file = _identifier(mapping, "target_file")
        if task_id:
            catalog.add(
                ref=f"task:{task_id}",
                name=task_id,
                owner="implementation",
                editable=True,
                artifact_type=TYPE_SOURCE_CODE,
                content=mapping,
            )
        if target_file and target_file in files:
            file_record = files[target_file]
            file_content = (
                file_record.get("content") if isinstance(file_record, Mapping) else file_record
            )
            catalog.add(
                ref=f"file:{target_file}",
                name=target_file,
                owner="implementation",
                editable=True,
                artifact_type=TYPE_SOURCE_CODE,
                content={"mapping": mapping, "file": file_content},
            )


def _add_findings(catalog: _Catalog) -> None:
    for stage in ("requirements", "design", "implementation", "testing"):
        command = workspace_repository.latest_command(catalog.app_id, stage=stage)
        if not command:
            continue
        result = command.get("result")
        if not isinstance(result, Mapping):
            continue
        job = result.get("job")
        job_result = job.get("result") if isinstance(job, Mapping) else None
        candidates = [result, job_result]
        for source in candidates:
            if not isinstance(source, Mapping):
                continue
            for finding in _records(source.get("blocking_findings")):
                identifier = _identifier(finding, "code") or _identifier(finding, "id")
                if not identifier:
                    continue
                owner = str(
                    finding.get("repair_owner")
                    or finding.get("owner")
                    or command.get("stage")
                    or "testing"
                )
                if owner not in {"requirements", "design", "implementation", "testing"}:
                    owner = "testing"
                catalog.add(
                    ref=f"finding:{identifier}",
                    name=identifier,
                    owner=owner,
                    editable=False,
                    artifact_type=None,
                    content={
                        "command_id": command.get("command_id"),
                        "command_status": command.get("status"),
                        **finding,
                    },
                )


class ProjectTools:
    """한 앱에 묶인 읽기 전용 project tool 모음."""

    def __init__(self, app_id: str):
        self.app_id = app_id
        self._cached_catalog: _Catalog | None = None

    def _catalog(self) -> _Catalog:
        if self._cached_catalog is None:
            self._cached_catalog = _build_catalog(self.app_id)
        return self._cached_catalog

    def read_workspace(self) -> dict[str, Any]:
        latest = workspace_repository.latest_command(self.app_id)
        actions = (
            [
                offer.model_dump(mode="json", exclude_none=True)
                for offer in offered_actions(latest)
            ]
            if latest
            else []
        )
        summary = workspace_repository.get_app_summary(self.app_id)
        return {
            **summary,
            "command": {
                "command_id": latest.get("command_id"),
                "action": latest.get("action"),
                "stage": latest.get("stage"),
                "status": latest.get("status"),
                "error": latest.get("error"),
            }
            if latest
            else None,
            "actions": actions,
        }

    def search_elements(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            raise ValueError("search query must not be empty")
        if limit < 1 or limit > 100:
            raise ValueError("search limit must be between 1 and 100")
        catalog = self._catalog()
        needles = tuple(part.casefold() for part in query.split() if part)
        matches: list[tuple[int, str, _Element]] = []
        for element in catalog.elements.values():
            haystack = " ".join(
                (element.ref, element.name, _summary(element.content))
            ).casefold()
            if not all(needle in haystack for needle in needles):
                continue
            name = element.name.casefold()
            ref = element.ref.casefold()
            score = sum(4 if needle in ref else 3 if needle in name else 1 for needle in needles)
            matches.append((-score, element.ref, element))
        return [
            element.public(self.app_id)
            for _, _, element in sorted(matches)[:limit]
        ]

    def read_element(self, ref: str) -> dict[str, Any]:
        catalog = self._catalog()
        element = catalog.resolve(ref)
        if element is None:
            raise KeyError(ref)
        payload = element.public(self.app_id, include_content=True)
        payload["requested_ref"] = ref
        return payload

    def explain_finding(self, ref: str) -> dict[str, Any]:
        if not ref.startswith("finding:"):
            raise ValueError("finding ref must have the form 'finding:id'")
        catalog = self._catalog()
        element = catalog.resolve(ref)
        if element is None or not element.ref.startswith("finding:"):
            raise KeyError(ref)
        payload = element.public(self.app_id, include_content=True)
        try:
            frozen = artifact_trace_response(self.app_id, ref)
        except UnknownTraceRef:
            frozen = None
        payload["testing_evidence"] = frozen
        return payload

    def trace_impact(
        self,
        refs: Sequence[str],
        *,
        view: TraceView = "editing",
    ) -> dict[str, Any]:
        normalized = _normalized_refs(refs)
        if view == "testing-evidence":
            catalog = self._catalog()
            frozen_impacts = []
            for requested_ref in normalized:
                element = catalog.resolve(requested_ref)
                ref = element.trace_alias if element and element.trace_alias else requested_ref
                try:
                    result = artifact_trace_response(self.app_id, ref)
                except UnknownTraceRef:
                    result = {
                        "app_id": self.app_id,
                        "ref": requested_ref,
                        "exists_in_view": False,
                        "trace_scope": "testing-input",
                    }
                else:
                    result["exists_in_view"] = True
                    result["requested_ref"] = requested_ref
                frozen_impacts.append(result)
            return {
                "app_id": self.app_id,
                "view": view,
                "impacts": frozen_impacts,
            }
        if view != "editing":
            raise ValueError("trace view must be 'editing' or 'testing-evidence'")

        catalog = self._catalog()
        trace = project_artifact_trace(
            dict(catalog.state),
            implementation_rtm=catalog.implementation_rtm,
        )
        plans = {
            str(item.get("ref")): item
            for item in _records(catalog.design_rtm.get("change_plan"))
            if item.get("ref")
        }
        impacts: list[dict[str, Any]] = []
        for requested_ref in normalized:
            element = catalog.resolve(requested_ref)
            if element is None:
                impacts.append({"ref": requested_ref, "exists_in_view": False})
                continue
            canonical = element.canonical_ref or element.ref
            plan = plans.get(canonical)
            trace_ref_text = element.trace_alias or requested_ref
            try:
                trace_ref = TraceRef.parse(trace_ref_text)
            except (TypeError, ValueError):
                trace_ref = None
            if trace_ref not in trace.refs:
                trace_ref = None
            impacts.append(
                {
                    "ref": requested_ref,
                    "canonical_ref": canonical,
                    "exists_in_view": True,
                    "owner": element.owner,
                    "related": list(plan.get("related") or []) if plan else [],
                    "affected": list(plan.get("affects") or []) if plan else [],
                    "affected_stages": list(plan.get("affected_stages") or []) if plan else [],
                    "upstream": [str(value) for value in trace.upstream(trace_ref)]
                    if trace_ref
                    else [],
                    "downstream": [str(value) for value in trace.downstream(trace_ref)]
                    if trace_ref
                    else [],
                    "files": [str(value) for value in trace.files(trace_ref)]
                    if trace_ref
                    else [],
                    # 최신 편집 view에 Testing evidence를 섞지 않는다.
                    "evidence": [],
                }
            )
        return {
            "app_id": self.app_id,
            "view": view,
            "artifact_versions": dict(catalog.state.get("artifact_versions") or {}),
            "source_snapshot": {
                key: catalog.snapshot.get(key)
                for key in ("version_id", "version_no", "snapshot_digest")
            }
            if catalog.snapshot
            else None,
            "impacts": impacts,
        }

    def validate_targets(
        self,
        refs: Sequence[str | Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not refs:
            return {
                "app_id": self.app_id,
                "valid": False,
                "targets": [],
                "valid_refs": [],
                "existing_refs": [],
            }
        catalog = self._catalog()
        results: list[dict[str, Any]] = []
        for submitted in refs:
            if isinstance(submitted, str):
                ref, supplied_app, supplied_version = submitted.strip(), None, None
            elif isinstance(submitted, Mapping):
                ref = str(submitted.get("ref") or "").strip()
                supplied_app = submitted.get("app_id")
                supplied_version = submitted.get("artifact_version_id")
            else:
                raise TypeError("target must be a ref string or mapping")
            if not ref:
                raise ValueError("target ref must not be empty")
            element = catalog.resolve(ref)
            app_matches = supplied_app in (None, self.app_id)
            version_matches = bool(
                element
                and (
                    supplied_version is None
                    or supplied_version == element.artifact_version_id
                )
            )
            exists = element is not None and app_matches
            editable = bool(element and element.editable and ref == (element.canonical_ref or element.ref))
            item = {
                "ref": ref,
                "exists": exists,
                "owner": element.owner if element else None,
                "editable": editable,
                "canonical_ref": (element.canonical_ref or element.ref) if element else None,
                "artifact_type": element.artifact_type if element else None,
                "artifact_version_id": element.artifact_version_id if element else None,
                "artifact_version_no": element.artifact_version_no if element else None,
                "app_matches": app_matches,
                "version_matches": version_matches,
            }
            item["valid"] = exists and editable and version_matches
            results.append(item)
        return {
            "app_id": self.app_id,
            "valid": all(item["valid"] for item in results),
            "targets": results,
            "valid_refs": [item["canonical_ref"] for item in results if item["valid"]],
            "existing_refs": [item["ref"] for item in results if item["exists"]],
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _identifier(item: Mapping[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _display_name(item: Mapping[str, Any], fallback: str) -> str:
    for key in ("name", "title", "text", "sentence", "description"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _relationship_identity(kind: str, item: Mapping[str, Any], index: int) -> str:
    fields = {
        "associations": ("actor", "use_case_id", "use_case"),
        "includes": ("base_use_case_id", "included_use_case_id"),
        "extends": ("extension_use_case_id", "base_use_case_id"),
        "generalizations": ("child", "parent", "specific", "general"),
    }.get(kind, ())
    values = [str(item.get(field) or "").strip() for field in fields]
    values = [value for value in values if value]
    return f"{kind}:{'->'.join(values) if values else index}"


def _summary(value: Any, limit: int = 360) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(value)
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def _safe_content(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_CONTENT_DEPTH:
        return "[nested content omitted]"
    if isinstance(value, str):
        text = _PRIVATE_KEY_PATTERN.sub(_REDACTED, value)
        text = _ASSIGNED_SECRET_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}", text
        )
        return _bounded_content_text(text)
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTENT_ITEMS:
                safe["_easydep_omitted_items"] = len(value) - _MAX_CONTENT_ITEMS
                break
            text_key = str(key)
            normalized_key = "".join(character for character in text_key if character.isalnum()).casefold()
            safe[text_key] = (
                _REDACTED
                if normalized_key in _SECRET_KEYS
                else _safe_content(item, depth=depth + 1)
            )
        return safe
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [
            _safe_content(item, depth=depth + 1)
            for item in value[:_MAX_CONTENT_ITEMS]
        ]
        if len(value) > _MAX_CONTENT_ITEMS:
            items.append(f"[{len(value) - _MAX_CONTENT_ITEMS} items omitted]")
        return items
    return value


def _bounded_content_text(text: str) -> str:
    if len(text) <= _MAX_CONTENT_STRING_CHARS:
        return text
    marker = "\n...[content truncated]...\n"
    available = _MAX_CONTENT_STRING_CHARS - len(marker)
    tail = available // 3
    return f"{text[: available - tail]}{marker}{text[-tail:]}"


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _normalized_refs(refs: Sequence[str]) -> list[str]:
    normalized = [ref.strip() for ref in refs if isinstance(ref, str) and ref.strip()]
    if not normalized:
        raise ValueError("at least one ref is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError("refs must be unique")
    return normalized


__all__ = [
    "ProjectTools",
    "TraceView",
]
