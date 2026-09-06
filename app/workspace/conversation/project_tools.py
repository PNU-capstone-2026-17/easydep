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
from hashlib import sha256
from typing import Any, Literal

from app.artifact_trace import TraceRef
from app.artifact_trace_projection import project_artifact_trace
from app.artifact_trace_service import UnknownTraceRef, artifact_trace_response
from app.db.models import (
    TYPE_API_SPEC,
    TYPE_CLASS,
    TYPE_DEPLOYMENT,
    TYPE_ERD,
    TYPE_REFINE_REQ,
    TYPE_SEQUENCE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
    TYPE_USECASE_SPEC,
)
from app.design.rtm import build_design_rtm
from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.identity import reconcile_stable_ids
from app.repositories import artifact_repository
from app.workspace import repository as workspace_repository
from app.workspace.actions import offered_actions

from .contracts import RevisionTarget

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
    "erd": TYPE_ERD,
    "deployment_diagram": TYPE_DEPLOYMENT,
}
_REQUIREMENTS_KINDS_BY_STAGE = {
    "actors": {"actor"},
    "use_cases": {"use_case"},
    "specs": {"use_case_spec"},
    "relationships": {"relationship"},
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
            self.add_alias(trace_alias, ref)

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

    def add_alias(self, alias: str, ref: str) -> None:
        previous = self.aliases.get(alias)
        if previous is not None and previous != ref:
            raise ValueError(f"catalog alias has conflicting targets: {alias}")
        self.aliases[alias] = ref


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
    version_id, _version_no = catalog.version(TYPE_USECASE_SPEC)
    if version_id is not None:
        for stage in _REQUIREMENTS_KINDS_BY_STAGE:
            catalog.add(
                ref=str(TraceRef("requirements_stage", stage)),
                name=stage.replace("_", " "),
                owner="requirements",
                editable=False,
                artifact_type=TYPE_USECASE_SPEC,
                content={"stage": stage},
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
        parsed = TraceRef.parse(ref)
        stage, element = parsed.kind, parsed.id
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
            catalog.add_alias(alias, ref)

    # ERD는 class의 결정론적 투영이므로 읽을 수는 있지만 직접 수정 대상으로 공개하지 않는다.
    for entity in _records(_mapping(catalog.state.get("erd_bce_classes")).get("Classes")):
        name = _identifier(entity, "className")
        if not name:
            continue
        catalog.add(
            ref=f"entity:{name}",
            name=name,
            owner="design",
            editable=False,
            # ERD entities are read-only projections.  Keep their own
            # artifact/version identity so planning can report them as a
            # downstream projection without accidentally making the backing
            # class diagram an authority target.
            artifact_type=TYPE_ERD,
            content=entity,
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

    for stage, artifact_type in _VERSION_BY_DESIGN_STAGE.items():
        version_id, _version_no = catalog.version(artifact_type)
        if version_id is None:
            continue
        catalog.add(
            ref=str(TraceRef("design_stage", stage)),
            name=stage.replace("_", " "),
            owner="design",
            editable=False,
            artifact_type=artifact_type,
            content={"stage": stage},
        )


def _design_content(
    state: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    content: dict[str, Any] = {}
    aliases: dict[str, list[str]] = {}
    model = _mapping(state.get("extracted_bce_classes"))
    if model:
        try:
            accepted, _metadata = reconcile_stable_ids(
                None,
                BCEModel.model_validate(model),
            )
            model = accepted.model_dump(mode="json", by_alias=True)
        except (TypeError, ValueError):
            # Invalid legacy artifacts remain readable for diagnostics. They
            # cannot gain a guessed identity at this boundary.
            pass
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
                stable_id = _identifier(operation, "stableId")
                if stable_id:
                    aliases[ref].append(f"operation:{stable_id}")
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
                stable_id = _identifier(call, "stableId")
                if stable_id:
                    aliases[ref].append(f"call:{stable_id}")

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
        # 저장 snapshot의 file key는 application root 기준이고 RTM은 workspace 기준이다.
        # 공개 ref는 RTM 경로를 유지하되 내용 조회에만 같은 상대 경로를 사용한다.
        snapshot_file = (
            target_file.removeprefix("application/") if target_file else None
        )
        snapshot_key = (
            target_file
            if target_file in files
            else snapshot_file
            if snapshot_file in files
            else None
        )
        if target_file and snapshot_key:
            file_record = files[snapshot_key]
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
                repair_owner = str(
                    finding.get("repair_owner")
                    or finding.get("owner")
                    or command.get("stage")
                    or "testing"
                )
                if repair_owner not in {
                    "requirements",
                    "design",
                    "implementation",
                    "testing",
                }:
                    repair_owner = "testing"
                catalog.add(
                    ref=f"finding:{identifier}",
                    name=identifier,
                    owner=str(command.get("stage") or "testing"),
                    editable=False,
                    artifact_type=(
                        TYPE_TEST_CODE
                        if command.get("stage") == "testing"
                        else TYPE_SOURCE_CODE
                        if command.get("stage") == "implementation"
                        else None
                    ),
                    content={
                        "command_id": command.get("command_id"),
                        "command_status": command.get("status"),
                        **finding,
                        "repair_owner": repair_owner,
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

    def normalize_revision_targets(
        self,
        refs: Sequence[str | Mapping[str, Any]],
        *,
        require_editable: bool = True,
    ) -> list[RevisionTarget]:
        """Return catalog-owned revision targets or reject the input.

        Editable targets are required for an execution authority.  Callers
        which only need a read-only plan (for example, downstream ERD
        projections) may set ``require_editable=False``.  In that mode the
        returned target keeps the catalog element's own ref and can never be
        silently rewritten to an editable authority.
        """
        validation = (
            self.validate_targets(refs)
            if require_editable
            else self.validate_revision_selections(refs)
        )
        if not validation["valid"]:
            invalid = [item["ref"] for item in validation["targets"] if not item["valid"]]
            raise ValueError(f"invalid revision targets: {', '.join(invalid)}")
        catalog = self._catalog()
        targets: list[RevisionTarget] = []
        for item in validation["targets"]:
            submitted_ref = str(item["ref"])
            canonical = str(item["canonical_ref"] or submitted_ref)
            element = catalog.resolve(canonical if require_editable else submitted_ref)
            if element is None or not element.artifact_type:
                # validate_targets currently prevents this for editable items;
                # retain the guard so a future catalog cannot create a partial
                # public contract by accident.
                raise ValueError(f"target has no revision artifact: {canonical}")
            # ``canonical`` is the executable stage ref. The trace alias,
            # when present, carries the more precise catalog-owned semantic
            # kind (operation/call/schema rather than just class_diagram).
            output_ref = canonical if require_editable else element.ref
            parsed = TraceRef.parse(element.trace_alias or output_ref)
            stable_id = _identifier(_mapping(element.content), "stableId")
            targets.append(
                RevisionTarget(
                    ref=output_ref,
                    kind=parsed.kind,
                    element_id=stable_id or parsed.id,
                    owner=element.owner,
                    artifact_type=element.artifact_type,
                    artifact_version_id=element.artifact_version_id,
                    display_label=element.name,
                )
            )
        if len({target.ref for target in targets}) != len(targets):
            raise ValueError("revision targets must resolve to unique canonical refs")
        return sorted(targets, key=lambda target: target.ref)

    def current_revision_target(self, target: RevisionTarget) -> RevisionTarget | None:
        """Resolve a version-pinned identity in the current catalog.

        Human-readable operation and call refs may change after a rename.  The
        app-managed ``element_id`` remains stable, so callers can produce an
        exact old-ref to new-ref mapping without matching names or prose.
        """

        matches: list[RevisionTarget] = []
        for element in self._catalog().elements.values():
            if element.owner != target.owner or element.artifact_type != target.artifact_type:
                continue
            try:
                current = self.normalize_revision_targets(
                    [element.ref], require_editable=element.editable
                )
            except (TypeError, ValueError):
                continue
            if not current:
                continue
            candidate = current[0]
            if candidate.kind == target.kind and candidate.element_id == target.element_id:
                matches.append(candidate)
        if len(matches) != 1:
            return None
        return matches[0]

    # Short alias for callers that describe the operation as normalization.
    canonical_revision_targets = normalize_revision_targets

    def validate_revision_selections(
        self,
        refs: Sequence[str | Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Validate catalog-owned planning selections without granting edit authority.

        A selection may be a deterministic, non-editable projection such as
        an ERD entity.  It must still be from this app's current catalog and
        match the current artifact version when a version was supplied.
        """
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
            planable = bool(element and element.artifact_type)
            item = {
                "ref": ref,
                "exists": exists,
                "owner": element.owner if element else None,
                "editable": bool(element and element.editable),
                "planable": planable,
                "canonical_ref": (element.canonical_ref or element.ref) if element else None,
                "artifact_type": element.artifact_type if element else None,
                "artifact_version_id": element.artifact_version_id if element else None,
                "artifact_version_no": element.artifact_version_no if element else None,
                "app_matches": app_matches,
                "version_matches": version_matches,
            }
            item["valid"] = exists and planable and version_matches
            results.append(item)
        return {
            "app_id": self.app_id,
            "valid": all(item["valid"] for item in results),
            "targets": results,
            "valid_refs": [
                item["ref"] for item in results if item["valid"]
            ],
            "existing_refs": [item["ref"] for item in results if item["exists"]],
        }

    def revision_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        """Read the frozen planning inputs and derive a stable trace digest.

        No repository write or command creation occurs here. ``refresh`` is
        useful before executing an approved plan: it deliberately rebuilds the
        catalog from the latest persisted artifacts instead of reusing an
        earlier conversational read.
        """
        if refresh:
            self._cached_catalog = None
        catalog = self._catalog()
        trace = project_artifact_trace(
            dict(catalog.state), implementation_rtm=catalog.implementation_rtm
        )
        artifact_versions = _revision_artifact_versions(catalog)
        source_snapshot = {
            key: catalog.snapshot.get(key)
            for key in ("version_id", "version_no", "snapshot_digest")
            if catalog.snapshot.get(key) is not None
        }
        trace_payload = {
            "artifact_versions": artifact_versions,
            "source_snapshot": source_snapshot,
            "nodes": [
                {
                    "ref": str(node.ref),
                    "sources": [str(source) for source in node.direct_sources],
                }
                for node in trace.nodes
            ],
            "design_links": [
                {
                    "from": str(link.get("from") or ""),
                    "to": str(link.get("to") or ""),
                    "relation": str(link.get("relation") or ""),
                }
                for link in catalog.design_rtm.get("links", [])
                if isinstance(link, Mapping)
            ],
        }
        digest = _stable_digest(trace_payload)
        return {
            "app_id": self.app_id,
            "artifact_versions": artifact_versions,
            "source_snapshot": source_snapshot,
            "trace_digest": digest,
            "design_links": trace_payload["design_links"],
        }

    # Public names used by approval/executor code. Both return/read only the
    # same snapshot, avoiding a second independently-defined fingerprint.
    snapshot_fingerprint = revision_snapshot

    def revision_trace_digest(self, *, refresh: bool = False) -> str:
        return str(self.revision_snapshot(refresh=refresh)["trace_digest"])

    def revision_relations(
        self, targets: Sequence[RevisionTarget | str | Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Return only catalog/trace-backed target relations for a plan.

        ``upstream`` is provenance; callers must still apply an ownership rule
        before treating anything as an editable reverse authority. Exact design
        links are returned separately so stage order can never manufacture one.
        """
        normalized_inputs: list[str | Mapping[str, Any]] = []
        for target in targets:
            normalized_inputs.append(target.ref if isinstance(target, RevisionTarget) else target)
        normalized = self.normalize_revision_targets(
            normalized_inputs, require_editable=False
        )
        catalog = self._catalog()
        trace = project_artifact_trace(
            dict(catalog.state), implementation_rtm=catalog.implementation_rtm
        )

        def include_trace_downstream(refs: Sequence[str]) -> list[str]:
            expanded = set(refs)
            for ref in refs:
                trace_ref = _trace_ref_for_element(catalog.resolve(ref))
                if trace_ref is None or trace_ref not in trace.refs:
                    continue
                expanded.update(
                    _catalog_refs_for_trace(
                        catalog,
                        trace.downstream(trace_ref),
                        require_editable=False,
                    )
                )
            return sorted(expanded)

        related: dict[str, dict[str, list[str]]] = {}
        for target in normalized:
            element = catalog.resolve(target.ref)
            if target.kind == "requirements_stage":
                stage_order = list(_REQUIREMENTS_KINDS_BY_STAGE)
                start = stage_order.index(target.element_id)
                included_kinds = {
                    kind
                    for stage in stage_order[start:]
                    for kind in _REQUIREMENTS_KINDS_BY_STAGE[stage]
                }
                downstream_refs = sorted(
                    candidate.ref
                    for candidate in catalog.elements.values()
                    if (
                        (candidate_ref := _trace_ref_for_element(candidate)) is not None
                        and candidate_ref.kind in included_kinds
                    )
                )
                downstream_refs = include_trace_downstream(downstream_refs)
                related[target.ref] = {
                    "upstream": [],
                    "downstream": downstream_refs,
                    "direct_upstream": [],
                    "direct_downstream": downstream_refs,
                }
                continue
            if target.kind == "design_stage":
                stage_order = list(_VERSION_BY_DESIGN_STAGE)
                start = stage_order.index(target.element_id)
                downstream_refs = sorted(
                    candidate.ref
                    for candidate in catalog.elements.values()
                    if candidate.ref != target.ref
                    and candidate.artifact_type
                    in {
                        _VERSION_BY_DESIGN_STAGE[stage]
                        for stage in stage_order[start:]
                    }
                    and (
                        (candidate_ref := _trace_ref_for_element(candidate)) is None
                        or candidate_ref.kind != "design_stage"
                    )
                )
                downstream_refs = include_trace_downstream(downstream_refs)
                related[target.ref] = {
                    "upstream": [],
                    "downstream": downstream_refs,
                    "direct_upstream": [],
                    "direct_downstream": downstream_refs,
                }
                continue
            trace_ref = _trace_ref_for_element(element)
            if trace_ref is None or trace_ref not in trace.refs:
                related[target.ref] = {
                    "upstream": [],
                    "downstream": [],
                    "direct_upstream": [],
                    "direct_downstream": [],
                }
            else:
                related[target.ref] = {
                    "upstream": _catalog_refs_for_trace(catalog, trace.upstream(trace_ref)),
                    "downstream": _catalog_refs_for_trace(catalog, trace.downstream(trace_ref)),
                    "direct_upstream": _catalog_refs_for_trace(
                        catalog, trace.sources(trace_ref), require_editable=False
                    ),
                    "direct_downstream": _catalog_refs_for_trace(
                        catalog, trace.consumers(trace_ref), require_editable=False
                    ),
                }
            if target.kind == "finding" and element is not None:
                evidence = _mapping(element.content)
                exact_refs = {
                    str(value).strip()
                    for key in ("target_ids", "trace_refs")
                    for value in evidence.get(key) or []
                    if isinstance(value, str) and value.strip()
                }
                exact_refs.update(
                    str(TraceRef("file", value.strip()))
                    for value in evidence.get("file_hints") or []
                    if isinstance(value, str) and value.strip()
                )
                exact_catalog_refs = {
                    catalog.resolve(ref).ref
                    for ref in exact_refs
                    if catalog.resolve(ref) is not None
                }
                related[target.ref]["direct_upstream"] = sorted(
                    set(related[target.ref]["direct_upstream"]) | exact_catalog_refs
                )
        snapshot = self.revision_snapshot()
        return {
            **snapshot,
            "targets": [target.model_dump(mode="json") for target in normalized],
            "relations": related,
        }

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
        selection = self.validate_revision_selections(refs)
        results: list[dict[str, Any]] = []
        for raw in selection["targets"]:
            item = dict(raw)
            canonical = item.get("canonical_ref")
            item["editable"] = bool(
                item["editable"] and item["ref"] == canonical
            )
            item["valid"] = bool(item["valid"] and item["editable"])
            results.append(item)
        return {
            "app_id": self.app_id,
            "valid": bool(results) and all(item["valid"] for item in results),
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


def _revision_artifact_versions(catalog: _Catalog) -> dict[str, int]:
    """Return version *ids* only; version numbers are display metadata."""
    versions: dict[str, int] = {}
    raw = catalog.state.get("artifact_versions")
    if isinstance(raw, Mapping):
        for artifact_type, value in raw.items():
            version_id = _int_or_none(value.get("version_id")) if isinstance(value, Mapping) else None
            if version_id is not None:
                versions[str(artifact_type)] = version_id
    source_version = _int_or_none(catalog.snapshot.get("version_id"))
    if source_version is not None:
        versions[TYPE_SOURCE_CODE] = source_version
    return dict(sorted(versions.items()))


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _trace_ref_for_element(element: _Element | None) -> TraceRef | None:
    if element is None:
        return None
    try:
        return TraceRef.parse(element.trace_alias or element.ref)
    except (TypeError, ValueError):
        return None


def _catalog_refs_for_trace(
    catalog: _Catalog,
    refs: Sequence[TraceRef],
    *,
    require_editable: bool = True,
) -> list[str]:
    """Map trace refs to catalog refs without manufacturing edit authority."""
    result: set[str] = set()
    for trace_ref in refs:
        element = catalog.resolve(str(trace_ref))
        if element is not None and (element.editable or not require_editable):
            result.add(
                (element.canonical_ref or element.ref)
                if require_editable
                else element.ref
            )
    return sorted(result)


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
