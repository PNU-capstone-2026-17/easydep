"""Run the isolated executable-behavior contracts with a real LLM.

The command reads one design checkpoint but never mutates graph state or MySQL.
Validated stage files are written below ``.easydep/experiments`` and reused only
when the app, scenario, provider, and model manifest still match.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.design.graphs.design_graph import graph
from app.design.schemas.class_model import BCEModel, ClassParameter
from app.design.services.class_diagram.inventory import (
    INVENTORY_PROMPT,
    finding_text,
    inventory_model,
    inventory_payload,
    normalize_inventory,
)
from app.design.services.class_diagram.models import AcceptedInventory
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.proposals import (
    InventoryItem,
    InventoryProposal,
    InventoryRelationship,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    build_scenario_index,
)
from app.design.services.class_diagram.validation.inventory import validate_inventory
from app.design.services.common.structured import (
    bind_context,
    capture_llm_timings,
    parse_structured,
)
from app.design.services.executable_behavior.bindings import (
    BindingSelectionError,
    Source,
    binding_candidate_sets,
    validated_binding_plan,
)
from app.design.services.executable_behavior.calls import (
    ActorEntry,
    CallProposal,
    CallValidationError,
    catalog_operations,
    validated_call_structure,
)
from app.design.services.executable_behavior.catalog import CatalogResult
from app.design.services.executable_behavior.contracts import (
    AcceptedBehaviorModel,
    ValidatedBindingPlan,
    ValidatedCallStructure,
    ValidatedCatalogDraft,
    ValidatedOperationFragment,
    canonical_digest,
)
from app.design.services.executable_behavior.materialize import materialize_bce_model
from app.design.services.executable_behavior.operations import (
    OperationContext,
    OperationValidationError,
    canonical_type,
    validated_operation_fragment,
)
from app.design.services.executable_behavior.orchestration import accept_behavior
from app.design.services.executable_behavior.patches import (
    OperationFragmentPatch,
    OperationPatchAction,
    OperationPatchError,
    apply_operation_fragment_patch,
)
from app.design.services.executable_behavior.reviews import (
    BEHAVIOR_CATEGORIES,
    INVENTORY_CATEGORIES,
    REVIEW_VALIDATOR_VERSION,
    AdjudicatedSemanticReview,
    BehaviorSemanticReviewProposal,
    FindingLedgerSnapshot,
    FindingLedgerStatus,
    InventorySemanticReviewProposal,
    ReviewCategory,
    ReviewEvidenceKind,
    ReviewEvidenceRecord,
    ReviewFindingDisposition,
    ReviewOwnerStage,
    SemanticReviewAdjudicationProposal,
    SemanticReviewFinding,
    SemanticReviewProposal,
    SemanticReviewStage,
    ValidatedSemanticReview,
    advance_finding_ledger,
    blocking_finding_digest,
    semantic_finding_id,
    validated_review_adjudication,
    validated_semantic_review,
)
from app.design.services.executable_behavior.semantics import (
    LocallySealedOperationFragment,
    LocalSemanticContract,
    LocalSemanticValidationError,
    ObligationKind,
    OperationResponsibility,
    OperationSemantics,
    ScenarioObligation,
    StateEffect,
    StateEffectOperation,
    ValidatedLocalSemanticContract,
    assemble_sealed_catalog,
    local_review_evidence_index,
    local_review_subject,
    seal_local_operation_fragment,
    validate_effect_call_links,
    validated_local_semantic_contract,
)
from app.design.services.executable_behavior.witness import (
    validate_execution_witness,
)
from app.llm_connection import build_llm_connection

DEFAULT_APP_ID = "522d73e6-3aee-42af-a787-15a22ab364b0"
DEFAULT_RUN_NAME = "executable-behavior-contract-v3-app522-004"
MAX_SEMANTIC_REPAIRS_PER_OWNER = 2
MAX_OPERATION_GENERATION_ATTEMPTS = 4
OPERATION_ATTEMPT_VERSION = "v6"
INVENTORY_REVIEW_CATEGORIES = INVENTORY_CATEGORIES - frozenset(
    {ReviewCategory.ENTITY_STRUCTURE}
)
WORKSPACE = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = WORKSPACE / ".easydep" / "experiments"


class ProposalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CallChoice(ProposalModel):
    call_id: str = Field(alias="callId", pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    receiver_operation_id: str = Field(alias="receiverOperationId", min_length=1)
    parent_call_id: str | None = Field(alias="parentCallId")
    group_key: str = Field(alias="groupKey", min_length=1)
    guard_refs: list[str] = Field(alias="guardRefs")
    export_result: bool = Field(alias="exportResult")


class CallChoicePlan(ProposalModel):
    calls: list[CallChoice] = Field(min_length=1)


class BindingChoice(ProposalModel):
    call_id: str = Field(alias="callId", min_length=1)
    parameter: str = Field(min_length=1)
    source_ref: str = Field(alias="sourceRef", min_length=1)


class BindingChoicePlan(ProposalModel):
    selections: list[BindingChoice]


class InventoryConflictResolution(ProposalModel):
    item: InventoryItem


class InventoryRelationshipResolution(ProposalModel):
    relationship: InventoryRelationship


class OperationSignatureResolution(ProposalModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: list[ClassParameter]
    return_type: str = Field(alias="returnType", min_length=1)


class ScenarioObligationDecision(ProposalModel):
    source_ref: str = Field(alias="sourceRef", min_length=1)
    kind: ObligationKind
    state_refs: list[str] = Field(alias="stateRefs")


class ScenarioObligationPlan(ProposalModel):
    decisions: list[ScenarioObligationDecision] = Field(min_length=1)


class StateEffectDecision(ProposalModel):
    state_ref: str = Field(alias="stateRef", min_length=1)
    operation: Literal[
        "SET", "UPDATE", "INCREMENT", "DECREMENT", "CREATE", "DELETE"
    ]
    # Providers sometimes fill an unused nullable JSON-schema branch with [] or {}.
    # Accept it at the proposal edge; the deterministic compiler below discards
    # operands that the selected operation cannot consume, while StateEffect still
    # validates operands that are semantically required.
    value: Any = None
    delta: Any = None
    condition: str | None = None


class EntityOperationSemanticDecision(ProposalModel):
    operation_ref: str = Field(alias="operationRef", min_length=1)
    effects: list[StateEffectDecision]


class EntityOperationSemanticPlan(ProposalModel):
    operations: list[EntityOperationSemanticDecision]


class ExperimentFailure(RuntimeError):
    pass


class LogicalCallBudget:
    def __init__(self, limit: int, *, run_name: str = DEFAULT_RUN_NAME) -> None:
        self.limit = limit
        self.run_name = run_name
        self.used = 0
        self._lock = threading.Lock()

    def claim(self, operation: str) -> int:
        with self._lock:
            if self.used >= self.limit:
                raise ExperimentFailure(
                    f"logical LLM call budget exhausted before {operation}"
                )
            self.used += 1
            return self.used


@dataclass(frozen=True)
class UseCaseInputs:
    use_case: UseCase
    groups: tuple[ExecutionGroup, ...]
    allowed_steps: tuple[str, ...]
    allowed_owners: tuple[str, ...]
    durable_entities: tuple[str, ...]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_immutable_json(path: Path, value: Any) -> None:
    """Create an attempt once; never rewrite its historical content."""
    if path.exists():
        stored = _read_json(path)
        if canonical_digest(stored) != canonical_digest(value):
            raise ExperimentFailure(f"immutable attempt already exists: {path.name}")
        return
    _write_json(path, value)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _finding_ledger_path(run_dir: Path, checkpoint_key: str) -> Path:
    return run_dir / "validated" / "finding-ledgers" / f"{checkpoint_key}.json"


def _load_finding_ledger(
    run_dir: Path, checkpoint_key: str
) -> FindingLedgerSnapshot:
    path = _finding_ledger_path(run_dir, checkpoint_key)
    if not path.exists():
        return FindingLedgerSnapshot()
    return FindingLedgerSnapshot.model_validate(_read_json(path))


def _persist_finding_ledger(
    run_dir: Path,
    checkpoint_key: str,
    ledger: FindingLedgerSnapshot,
) -> None:
    payload = ledger.model_dump(by_alias=True)
    _write_json(_finding_ledger_path(run_dir, checkpoint_key), payload)
    if ledger.transitions:
        _write_immutable_json(
            run_dir
            / "attempts"
            / "finding-ledgers"
            / (
                f"{checkpoint_key}-{len(ledger.transitions):04d}-"
                f"{canonical_digest(payload)[:12]}.json"
            ),
            payload,
        )


def _record_adjudicated_findings(
    *,
    run_dir: Path,
    checkpoint_key: str,
    review: AdjudicatedSemanticReview,
) -> None:
    ledger = _load_finding_ledger(run_dir, checkpoint_key)
    current = {
        item.finding_id: item.disposition for item in review.adjudications
    }
    for finding_id, status in tuple(ledger.latest_statuses.items()):
        if status is FindingLedgerStatus.PATCHED and finding_id not in current:
            ledger = advance_finding_ledger(
                ledger,
                finding_id=finding_id,
                to_status=FindingLedgerStatus.VERIFIED_RESOLVED,
                subject_digest=review.subject_digest,
                candidate_digest=None,
                rationale="The patched finding is absent from the revalidated review.",
            )

    for item in review.adjudications:
        finding_id = item.finding_id
        latest = ledger.latest_statuses.get(finding_id)
        if latest is None:
            ledger = advance_finding_ledger(
                ledger,
                finding_id=finding_id,
                to_status=FindingLedgerStatus.REPORTED,
                subject_digest=review.subject_digest,
                candidate_digest=None,
                rationale="A grounded HIGH semantic claim was reported.",
            )
            latest = FindingLedgerStatus.REPORTED
        if latest is FindingLedgerStatus.PATCHED:
            post_patch_status = {
                ReviewFindingDisposition.CONFIRMED: FindingLedgerStatus.STILL_OPEN,
                ReviewFindingDisposition.REFUTED: (
                    FindingLedgerStatus.VERIFIED_RESOLVED
                ),
                ReviewFindingDisposition.UNRESOLVED: FindingLedgerStatus.UNRESOLVED,
            }[item.disposition]
            ledger = advance_finding_ledger(
                ledger,
                finding_id=finding_id,
                to_status=post_patch_status,
                subject_digest=review.subject_digest,
                candidate_digest=None,
                rationale=f"Post-patch adjudication: {item.disposition.value}.",
            )
            continue
        disposition_status = {
            ReviewFindingDisposition.CONFIRMED: FindingLedgerStatus.CONFIRMED,
            ReviewFindingDisposition.REFUTED: FindingLedgerStatus.REFUTED,
            ReviewFindingDisposition.UNRESOLVED: FindingLedgerStatus.UNRESOLVED,
        }[item.disposition]
        if latest in {
            FindingLedgerStatus.REPORTED,
            FindingLedgerStatus.UNRESOLVED,
        } and latest is not disposition_status:
            ledger = advance_finding_ledger(
                ledger,
                finding_id=finding_id,
                to_status=disposition_status,
                subject_digest=review.subject_digest,
                candidate_digest=None,
                rationale=f"Independent adjudication: {item.disposition.value}.",
            )
    _persist_finding_ledger(run_dir, checkpoint_key, ledger)


def _record_patched_findings(
    *,
    run_dir: Path,
    checkpoint_key: str,
    review: AdjudicatedSemanticReview,
    candidate_digest: str,
) -> None:
    ledger = _load_finding_ledger(run_dir, checkpoint_key)
    missing = {
        semantic_finding_id(finding)
        for finding in review.blocking_findings
        if semantic_finding_id(finding) not in ledger.latest_statuses
    }
    if missing:
        _record_adjudicated_findings(
            run_dir=run_dir,
            checkpoint_key=checkpoint_key,
            review=review,
        )
        ledger = _load_finding_ledger(run_dir, checkpoint_key)
    for finding in review.blocking_findings:
        finding_id = semantic_finding_id(finding)
        latest = ledger.latest_statuses.get(finding_id)
        if latest not in {
            FindingLedgerStatus.CONFIRMED,
            FindingLedgerStatus.STILL_OPEN,
        }:
            raise ExperimentFailure(
                f"cannot patch finding in ledger state {latest}: {finding_id}"
            )
        ledger = advance_finding_ledger(
            ledger,
            finding_id=finding_id,
            to_status=FindingLedgerStatus.PATCHED,
            subject_digest=review.subject_digest,
            candidate_digest=candidate_digest,
            rationale="A scoped owner repair was applied and awaits revalidation.",
        )
    _persist_finding_ledger(run_dir, checkpoint_key, ledger)


def _finding_ledger_metrics(run_dir: Path) -> dict[str, Any]:
    root = run_dir / "validated" / "finding-ledgers"
    snapshots = (
        [
            FindingLedgerSnapshot.model_validate(_read_json(path))
            for path in root.glob("*.json")
        ]
        if root.exists()
        else []
    )
    latest = [
        status for snapshot in snapshots for status in snapshot.latest_statuses.values()
    ]
    patch_attempts = sum(
        transition.to_status is FindingLedgerStatus.PATCHED
        for snapshot in snapshots
        for transition in snapshot.transitions
    )
    resolved = sum(
        status is FindingLedgerStatus.VERIFIED_RESOLVED for status in latest
    )
    regressions = sum(
        status in {FindingLedgerStatus.STILL_OPEN, FindingLedgerStatus.REGRESSION}
        for status in latest
    )
    return {
        "findingLedgerCount": len(latest),
        "findingPatchAttemptCount": patch_attempts,
        "findingPatchResolvedCount": resolved,
        "findingPatchRegressionCount": regressions,
        "findingPatchSolutionRate": (
            round(resolved / patch_attempts, 4) if patch_attempts else None
        ),
    }


def _inside_experiment_root(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(EXPERIMENT_ROOT.resolve())
    except ValueError as error:
        raise ValueError(
            "experiment output must stay below .easydep/experiments"
        ) from error
    return resolved


def _scenario_payload(index: ScenarioIndex) -> dict[str, Any]:
    return {
        "useCases": [
            {
                "id": use_case.id,
                "name": use_case.name,
                "primaryActor": use_case.primary_actor,
                "steps": [
                    {
                        "id": step.id,
                        "subject": step.subject,
                        "sentence": step.sentence,
                        "branch": step.branch,
                        "condition": step.condition,
                    }
                    for step in use_case.steps
                ],
                "preconditions": list(use_case.precondition_refs),
            }
            for use_case in index.use_cases
        ],
        "relationships": [
            {
                "kind": item.kind,
                "baseUseCaseId": item.base_id,
                "relatedUseCaseId": item.child_id,
                "anchorStepRefs": list(item.anchor_step_ids),
            }
            for item in index.relationships
        ],
    }


def _load_index(app_id: str) -> ScenarioIndex:
    snapshot = graph.get_state({"configurable": {"thread_id": app_id}})
    raw = snapshot.values.get("usecase_spec") if snapshot.values else None
    if not isinstance(raw, dict):
        raise ExperimentFailure("the app has no structured use-case checkpoint")
    return build_scenario_index(dict(raw))


def _findings(values: Iterable[Any]) -> list[str]:
    return [str(item) for item in values]


def _invoke(
    *,
    budget: LogicalCallBudget,
    operation: str,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    use_case_id: str | None = None,
    max_tokens: int = 8192,
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    call_number = budget.claim(operation)
    started = perf_counter()
    print(
        json.dumps(
            {
                "event": "llm.started",
                "number": call_number,
                "operation": operation,
                "useCaseId": use_case_id,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    result = parse_structured(
        messages,
        schema,
        reasoning_effort=reasoning_effort,
        max_completion_tokens=max_tokens,
        operation=operation,
        metadata={
            "experiment": budget.run_name,
            "useCaseId": use_case_id,
            "executionSlice": use_case_id or "inventory",
        },
    )
    print(
        json.dumps(
            {
                "event": "llm.completed",
                "number": call_number,
                "operation": operation,
                "useCaseId": use_case_id,
                "elapsedSeconds": round(perf_counter() - started, 3),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return result


def _inventory(
    index: ScenarioIndex,
    run_dir: Path,
    budget: LogicalCallBudget,
    *,
    parallelism: int,
) -> dict[str, Any]:
    checkpoint = run_dir / "validated" / "inventory.json"
    fragments_checkpoint = run_dir / "validated" / "inventory-fragments.json"
    merge_checkpoint = run_dir / "validated" / "inventory-merge.json"
    if (
        checkpoint.exists()
        and fragments_checkpoint.exists()
        and merge_checkpoint.exists()
    ):
        cached_fragments = _read_json(fragments_checkpoint)
        merge_record = _read_json(merge_checkpoint)
        if isinstance(cached_fragments, Mapping) and all(
            isinstance(cached_fragments.get(use_case.id), Mapping)
            and not _inventory_fragment_findings(
                index,
                use_case,
                cached_fragments[use_case.id],
            )
            for use_case in index.use_cases
        ):
            payload = _read_json(checkpoint)
            accepted = AcceptedInventory.from_payload(payload)
            report = validate_inventory(accepted.as_payload(), index)
            inventory_model(accepted)
            if (
                isinstance(merge_record, Mapping)
                and merge_record.get("fragmentsDigest")
                == canonical_digest(cached_fragments)
                and merge_record.get("inventoryDigest")
                == canonical_digest(accepted.as_payload())
                and not report.errors
                and not report.findings
            ):
                print('{"event":"checkpoint.hit","stage":"inventory"}', flush=True)
                return accepted.as_payload()

    def generate(use_case: UseCase) -> tuple[str, dict[str, Any]]:
        fragment_checkpoint = (
            run_dir / "validated" / "inventory-fragments" / f"{use_case.id}.json"
        )
        if fragment_checkpoint.exists():
            fragment = _read_json(fragment_checkpoint)
            if not _inventory_fragment_findings(index, use_case, fragment):
                print(
                    f'{{"event":"checkpoint.hit","stage":"inventoryFragment","useCaseId":"{use_case.id}"}}',
                    flush=True,
                )
                return use_case.id, fragment
        # An earlier executable attempt can become valid after a deterministic
        # notation/validator improvement. Revalidate before spending another LLM
        # call; the run manifest already pins app, scenario, provider, and model.
        for attempt_number in (2, 1):
            attempt_checkpoint = (
                run_dir
                / "attempts"
                / "inventory"
                / f"{use_case.id}-{attempt_number}.json"
            )
            if not attempt_checkpoint.exists():
                continue
            try:
                stored_proposal = InventoryProposal.model_validate(
                    _read_json(attempt_checkpoint)
                )
                stored_fragment = normalize_inventory(stored_proposal).as_payload()
            except ValueError:
                continue
            if not _inventory_fragment_findings(
                index, use_case, stored_fragment
            ):
                _write_json(fragment_checkpoint, stored_fragment)
                print(
                    f'{{"event":"checkpoint.promoted","stage":"inventoryFragment",'
                    f'"useCaseId":"{use_case.id}","attempt":{attempt_number}}}',
                    flush=True,
                )
                return use_case.id, stored_fragment
        prompt_input = _inventory_fragment_payload(index, use_case)
        base_messages = [
            {"role": "system", "content": _INVENTORY_FRAGMENT_PROMPT},
            {"role": "user", "content": json.dumps(prompt_input, ensure_ascii=False)},
        ]
        previous: dict[str, Any] | None = None
        findings: list[str] = []
        seen: set[str] = set()
        for attempt in range(2):
            messages = list(base_messages)
            if previous is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": (
                                    "Repair only this use-case inventory fragment and "
                                    "return the complete object."
                                ),
                                "previousCandidate": previous,
                                "findings": findings,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            parsed = _invoke(
                budget=budget,
                operation=(
                    "ExecutableInventoryFragment"
                    if attempt == 0
                    else "ExecutableInventoryFragmentRepair"
                ),
                messages=messages,
                schema=InventoryProposal,
                use_case_id=use_case.id,
                max_tokens=4096,
                reasoning_effort="low",
            )
            proposal = InventoryProposal.model_validate(parsed)
            previous = proposal.model_dump(by_alias=True)
            _write_json(
                run_dir
                / "attempts"
                / "inventory"
                / f"{use_case.id}-{attempt + 1}.json",
                previous,
            )
            fragment = normalize_inventory(proposal).as_payload()
            digest = canonical_digest(fragment)
            if digest in seen:
                raise ExperimentFailure(
                    f"{use_case.id} repeated the same rejected inventory fragment"
                )
            seen.add(digest)
            findings = _inventory_fragment_findings(index, use_case, fragment)
            if not findings:
                _write_json(fragment_checkpoint, fragment)
                return use_case.id, fragment
        raise ExperimentFailure(
            f"{use_case.id} inventory fragment remained invalid: " + "; ".join(findings)
        )

    fragments: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(bind_context(generate), use_case): use_case.id
            for use_case in index.use_cases
        }
        for future in as_completed(futures):
            use_case_id = futures[future]
            try:
                fragment_id, fragment = future.result()
                fragments[fragment_id] = fragment
            except Exception as error:  # noqa: BLE001 - report every independent fragment failure
                failures.append(f"{use_case_id}: {type(error).__name__}: {error}")
    if failures:
        raise ExperimentFailure(" | ".join(sorted(failures)))
    return _compose_inventory(
        index=index,
        fragments=fragments,
        run_dir=run_dir,
        budget=budget,
        parallelism=parallelism,
    )


_INVENTORY_FRAGMENT_PROMPT = (
    INVENTORY_PROMPT
    + "\n\nYou are designing a fragment for exactly one use case. Return only classes, "
    "Entity structural types, and relationships grounded in that use case. Every BCE "
    "class must list exactly this use case ID in useCaseIds. Do not mention another "
    "use case or create a relationship to an Entity outside this fragment. Reusable "
    "names follow durable domain identity: use the same canonical name for the same "
    "real-world concept even when this use case observes only a subset of its fields. "
    "The merge stage reconciles compatible per-use-case structural views. Choose a "
    "different name only for a genuinely distinct identity, lifecycle, or role."
    " Use role-explicit BCE names: every Boundary name ends with Boundary and every "
    "Control name ends with Control. Reserve unsuffixed durable domain nouns such as "
    "Student or CourseOffering for Entity classes; an Entity name cannot end with "
    "Boundary or Control."
)


def _inventory_fragment_payload(
    index: ScenarioIndex, use_case: UseCase
) -> dict[str, Any]:
    """Keep the LLM decision space to one UC; merge owns all cross-fragment logic."""
    full = inventory_payload(index)
    return {
        "useCases": [
            item for item in full["useCases"] if item.get("id") == use_case.id
        ],
        "relationships": [],
    }


def _inventory_fragment_findings(
    index: ScenarioIndex,
    use_case: UseCase,
    fragment: Mapping[str, Any],
) -> list[str]:
    """Fully validate one fragment before it becomes a reusable checkpoint."""
    fragment_index = ScenarioIndex(
        raw=index.raw,
        use_cases=(use_case,),
        relationships=(),
        groups=tuple(
            group for group in index.groups if group.use_case_id == use_case.id
        ),
    )
    try:
        accepted = AcceptedInventory.from_payload(dict(fragment))
        inventory_model(accepted)
    except ValueError as error:
        return [str(error)]
    report = validate_inventory(accepted.as_payload(), fragment_index)
    findings = [*report.errors, *finding_text(report.findings)]
    for item in fragment.get("Classes", []) or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("className") or "")
        role = str(item.get("stereotype") or "")
        if role == "Boundary" and not name.endswith("Boundary"):
            findings.append(
                f"Boundary class {name} must end with Boundary to keep BCE roles "
                "disjoint in the global namespace"
            )
        if role == "Control" and not name.endswith("Control"):
            findings.append(
                f"Control class {name} must end with Control to keep BCE roles "
                "disjoint in the global namespace"
            )
        if role == "Entity" and name.endswith(("Boundary", "Control")):
            findings.append(
                f"Entity class {name} must use a durable domain name without a BCE "
                "coordination suffix"
            )
    return findings


_INVENTORY_CONFLICT_PROMPT = """
Resolve exactly one same-name BCE inventory collision. Return one canonical item and
nothing else. Copy the supplied name exactly, use the one common kind, and set
useCaseIds to exactly all supplied use case IDs.

Preserve the durable facts required across all variants, but reconcile aliases into
one coherent contract instead of blindly duplicating fields. An Entity needs typed
fields and identifiers that name declared fields. Boundary and Control retain no
fields, identifiers, or values. A valueObject has fields only; an enumeration has
values only. Field types may use built-in types or globalDeclaredNames only. Do not
invent operations, relationships, calls, bindings, or another named type.

For semantic repair, inspect globalInventory and reviewFindings as well as the
same-name variants. A HIGH finding about overlap with another declared item takes
precedence over copying duplicated fields: retain the distinction between the two
concepts and, when supported by the source variants, represent the dependency with
a typed field that references the already-declared item.
""".strip()


def _inventory_conflicts(
    index: ScenarioIndex,
    fragments: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Find only cross-fragment declarations that require a semantic decision."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for use_case in index.use_cases:
        fragment = fragments.get(use_case.id) or {}
        for item in fragment.get("Classes", []) or []:
            if isinstance(item, Mapping):
                grouped.setdefault(f"class:{item.get('className')}", []).append(
                    {"useCaseId": use_case.id, "item": dict(item)}
                )
        for item in fragment.get("DataTypes", []) or []:
            if isinstance(item, Mapping):
                grouped.setdefault(f"type:{item.get('name')}", []).append(
                    {"useCaseId": use_case.id, "item": dict(item)}
                )
    result: dict[str, list[dict[str, Any]]] = {}
    for key, variants in grouped.items():
        contracts = {
            canonical_digest(
                {
                    field: value
                    for field, value in variant["item"].items()
                    if field != "useCaseIds"
                }
            )
            for variant in variants
        }
        if len(variants) > 1 and len(contracts) > 1:
            result[key] = variants
    return result


def _normalized_resolution_item(item: InventoryItem) -> dict[str, Any]:
    normalized = normalize_inventory(
        InventoryProposal(items=[item], Relationships=[])
    ).as_payload()
    values = [*normalized["Classes"], *normalized["DataTypes"]]
    if len(values) != 1:
        raise ExperimentFailure("conflict resolution must normalize to one item")
    result = dict(values[0])
    result["useCaseIds"] = list(item.use_case_ids)
    return result


def _resolution_findings(
    key: str,
    variants: list[dict[str, Any]],
    item: Mapping[str, Any],
) -> list[str]:
    prefix, expected_name = key.split(":", 1)
    expected_use_cases = {str(value["useCaseId"]) for value in variants}
    expected_kinds = {
        str(value["item"].get("stereotype") or value["item"].get("kind") or "")
        for value in variants
    }
    name_key = "className" if prefix == "class" else "name"
    kind_key = "stereotype" if prefix == "class" else "kind"
    findings: list[str] = []
    if str(item.get(name_key) or "") != expected_name:
        findings.append(f"name must remain {expected_name}")
    if len(expected_kinds) != 1 or str(item.get(kind_key) or "") not in expected_kinds:
        findings.append("kind must be the one common variant kind")
    if set(item.get("useCaseIds") or []) != expected_use_cases:
        findings.append("useCaseIds must cover exactly all conflicting variants")
    fields = list(item.get("fields") or [])
    identifiers = list(item.get("identifier") or [])
    values = list(item.get("values") or [])
    kind = str(item.get(kind_key) or "")
    field_names = {
        str(value).partition(":")[0].strip()
        for value in fields
        if str(value).partition(":")[1]
    }
    if kind == "Entity" and (
        not fields or values or not identifiers or not set(identifiers) <= field_names
    ):
        findings.append("Entity requires fields, valid identifiers, and no values")
    if kind in {"Boundary", "Control"} and (fields or identifiers or values):
        findings.append("Boundary and Control cannot retain structural state")
    if kind == "valueObject" and (not fields or identifiers or values):
        findings.append("valueObject requires fields only")
    if kind == "enumeration" and (fields or identifiers or not values):
        findings.append("enumeration requires values only")
    return findings


def _resolve_inventory_conflicts(
    *,
    index: ScenarioIndex,
    conflicts: Mapping[str, list[dict[str, Any]]],
    fragments: Mapping[str, Mapping[str, Any]],
    run_dir: Path,
    budget: LogicalCallBudget,
    parallelism: int,
) -> dict[str, dict[str, Any]]:
    if not conflicts:
        return {}
    declared_names = sorted(
        {
            str(item.get("className") or item.get("name") or "")
            for fragment in fragments.values()
            for collection in ("Classes", "DataTypes")
            for item in fragment.get(collection, []) or []
            if isinstance(item, Mapping)
        }
    )
    source_use_cases = {
        item["id"]: item for item in inventory_payload(index)["useCases"]
    }

    def resolve(key: str) -> tuple[str, dict[str, Any]]:
        variants = conflicts[key]
        input_digest = canonical_digest(
            {
                "key": key,
                "variants": variants,
                "declaredNames": declared_names,
            }
        )
        checkpoint = (
            run_dir
            / "validated"
            / "inventory-resolutions"
            / f"{key.replace(':', '-')}.json"
        )
        if checkpoint.exists():
            stored = _read_json(checkpoint)
            if (
                isinstance(stored, Mapping)
                and stored.get("inputDigest") == input_digest
                and isinstance(stored.get("item"), Mapping)
                and not _resolution_findings(key, variants, stored["item"])
            ):
                print(
                    f'{{"event":"checkpoint.hit","stage":"inventoryResolution","key":"{key}"}}',
                    flush=True,
                )
                return key, dict(stored["item"])
        use_case_ids = [str(value["useCaseId"]) for value in variants]
        prompt_payload = {
            "collisionKey": key,
            "requiredUseCaseIds": use_case_ids,
            "useCases": [source_use_cases[value] for value in use_case_ids],
            "variants": variants,
            "globalDeclaredNames": declared_names,
        }
        base_messages = [
            {"role": "system", "content": _INVENTORY_CONFLICT_PROMPT},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
        ]
        previous: dict[str, Any] | None = None
        findings: list[str] = []
        seen: set[str] = set()
        for attempt in range(2):
            messages = list(base_messages)
            if previous is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "Repair only this canonical collision item.",
                                "previousCandidate": previous,
                                "findings": findings,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            parsed = _invoke(
                budget=budget,
                operation=(
                    "ExecutableInventoryResolution"
                    if attempt == 0
                    else "ExecutableInventoryResolutionRepair"
                ),
                messages=messages,
                schema=InventoryConflictResolution,
                use_case_id=key,
                max_tokens=4096,
                reasoning_effort="low",
            )
            proposal = InventoryConflictResolution.model_validate(parsed)
            previous = proposal.model_dump(by_alias=True)
            _write_json(
                run_dir
                / "attempts"
                / "inventory-resolutions"
                / f"{key.replace(':', '-')}-{attempt + 1}.json",
                previous,
            )
            item = _normalized_resolution_item(proposal.item)
            digest = canonical_digest(item)
            if digest in seen:
                raise ExperimentFailure(
                    f"{key} repeated the same rejected inventory resolution"
                )
            seen.add(digest)
            findings = _resolution_findings(key, variants, item)
            if not findings:
                _write_json(
                    checkpoint,
                    {"inputDigest": input_digest, "item": item},
                )
                return key, item
        raise ExperimentFailure(
            f"{key} inventory resolution remained invalid: " + "; ".join(findings)
        )

    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(bind_context(resolve), key): key for key in conflicts
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, item = future.result()
                results[result_key] = item
            except Exception as error:  # noqa: BLE001 - aggregate resolution failures
                failures.append(f"{key}: {type(error).__name__}: {error}")
    if failures:
        raise ExperimentFailure(" | ".join(sorted(failures)))
    return results


_INVENTORY_RELATIONSHIP_CONFLICT_PROMPT = """
Resolve exactly one duplicated Entity relationship. Return one relationship and
nothing else. Its endpoints must be exactly the supplied entity pair, in either
direction. Choose the single structural relationship type and endpoint
multiplicities best supported by all variants; do not copy contradictory duplicate
relationships. Do not invent classes, fields, operations, calls, or bindings.
""".strip()


def _relationship_pair(item: Mapping[str, Any]) -> str:
    return "|".join(
        sorted((str(item.get("source") or ""), str(item.get("target") or "")))
    )


def _canonical_relationship_contract(item: Mapping[str, Any]) -> dict[str, Any]:
    source = str(item.get("source") or "")
    target = str(item.get("target") or "")
    source_multiplicity = str(item.get("sourceMultiplicity") or "")
    target_multiplicity = str(item.get("targetMultiplicity") or "")
    if source > target:
        source, target = target, source
        source_multiplicity, target_multiplicity = (
            target_multiplicity,
            source_multiplicity,
        )
    return {
        "source": source,
        "target": target,
        "type": str(item.get("type") or ""),
        "sourceMultiplicity": source_multiplicity,
        "targetMultiplicity": target_multiplicity,
    }


def _inventory_relationship_conflicts(
    index: ScenarioIndex,
    fragments: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for use_case in index.use_cases:
        fragment = fragments.get(use_case.id) or {}
        for item in fragment.get("Relationships", []) or []:
            if isinstance(item, Mapping):
                pair = _relationship_pair(item)
                grouped.setdefault(f"relationship:{pair}", []).append(
                    {"useCaseId": use_case.id, "relationship": dict(item)}
                )
    return {
        key: variants
        for key, variants in grouped.items()
        if len(variants) > 1
        and len(
            {
                canonical_digest(
                    _canonical_relationship_contract(variant["relationship"])
                )
                for variant in variants
            }
        )
        > 1
    }


def _relationship_resolution_findings(
    key: str,
    variants: list[dict[str, Any]],
    relationship: Mapping[str, Any],
) -> list[str]:
    expected_pair = key.split(":", 1)[1]
    findings: list[str] = []
    if _relationship_pair(relationship) != expected_pair:
        findings.append(
            "relationship endpoints must remain the conflicting Entity pair"
        )
    if not str(relationship.get("sourceMultiplicity") or "") or not str(
        relationship.get("targetMultiplicity") or ""
    ):
        findings.append("both endpoint multiplicities are required")
    supported_types = {
        str(value["relationship"].get("type") or "") for value in variants
    }
    if str(relationship.get("type") or "") not in supported_types:
        findings.append("relationship type must be supported by a supplied variant")
    return findings


def _resolve_inventory_relationship_conflicts(
    *,
    conflicts: Mapping[str, list[dict[str, Any]]],
    run_dir: Path,
    budget: LogicalCallBudget,
    parallelism: int,
) -> dict[str, dict[str, Any]]:
    def resolve(key: str) -> tuple[str, dict[str, Any]]:
        variants = conflicts[key]
        input_digest = canonical_digest({"key": key, "variants": variants})
        checkpoint = (
            run_dir
            / "validated"
            / "inventory-relationship-resolutions"
            / f"{key.replace(':', '-').replace('|', '-')}.json"
        )
        if checkpoint.exists():
            stored = _read_json(checkpoint)
            if (
                isinstance(stored, Mapping)
                and stored.get("inputDigest") == input_digest
                and isinstance(stored.get("relationship"), Mapping)
                and not _relationship_resolution_findings(
                    key, variants, stored["relationship"]
                )
            ):
                print(
                    f'{{"event":"checkpoint.hit","stage":"inventoryRelationshipResolution","key":"{key}"}}',
                    flush=True,
                )
                return key, dict(stored["relationship"])
        base_messages = [
            {"role": "system", "content": _INVENTORY_RELATIONSHIP_CONFLICT_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"collisionKey": key, "variants": variants},
                    ensure_ascii=False,
                ),
            },
        ]
        previous: dict[str, Any] | None = None
        findings: list[str] = []
        seen: set[str] = set()
        for attempt in range(2):
            messages = list(base_messages)
            if previous is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "Repair only this one relationship resolution.",
                                "previousCandidate": previous,
                                "findings": findings,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            parsed = _invoke(
                budget=budget,
                operation=(
                    "ExecutableInventoryRelationshipResolution"
                    if attempt == 0
                    else "ExecutableInventoryRelationshipResolutionRepair"
                ),
                messages=messages,
                schema=InventoryRelationshipResolution,
                use_case_id=key,
                max_tokens=2048,
                reasoning_effort="low",
            )
            proposal = InventoryRelationshipResolution.model_validate(parsed)
            previous = proposal.model_dump(by_alias=True)
            relationship = dict(previous["relationship"])
            _write_json(
                run_dir
                / "attempts"
                / "inventory-relationship-resolutions"
                / f"{key.replace(':', '-').replace('|', '-')}-{attempt + 1}.json",
                previous,
            )
            digest = canonical_digest(relationship)
            if digest in seen:
                raise ExperimentFailure(
                    f"{key} repeated the same rejected relationship resolution"
                )
            seen.add(digest)
            findings = _relationship_resolution_findings(key, variants, relationship)
            if not findings:
                _write_json(
                    checkpoint,
                    {"inputDigest": input_digest, "relationship": relationship},
                )
                return key, relationship
        raise ExperimentFailure(
            f"{key} relationship resolution remained invalid: " + "; ".join(findings)
        )

    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(bind_context(resolve), key): key for key in conflicts
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, relationship = future.result()
                results[result_key] = relationship
            except Exception as error:  # noqa: BLE001 - aggregate resolution failures
                failures.append(f"{key}: {type(error).__name__}: {error}")
    if failures:
        raise ExperimentFailure(" | ".join(sorted(failures)))
    return results


def _merge_inventory_fragments(
    index: ScenarioIndex,
    fragments: Mapping[str, Mapping[str, Any]],
    *,
    resolutions: Mapping[str, Mapping[str, Any]] | None = None,
    relationship_resolutions: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge identical declarations and explicit validated conflict resolutions."""
    order = {use_case.id: position for position, use_case in enumerate(index.use_cases)}
    classes: dict[str, dict[str, Any]] = {}
    data_types: dict[str, dict[str, Any]] = {}
    relationships: dict[str, dict[str, Any]] = {}

    def merge_named(
        target: dict[str, dict[str, Any]],
        item: Mapping[str, Any],
        name_key: str,
        conflict_prefix: str,
    ) -> None:
        name = str(item.get(name_key) or "")
        candidate = dict(item)
        existing = target.get(name)
        if existing is None:
            target[name] = candidate
            return
        comparable_existing = {
            key: value for key, value in existing.items() if key != "useCaseIds"
        }
        comparable_candidate = {
            key: value for key, value in candidate.items() if key != "useCaseIds"
        }
        if canonical_digest(comparable_existing) != canonical_digest(
            comparable_candidate
        ):
            resolution = (resolutions or {}).get(f"{conflict_prefix}:{name}")
            if resolution is None:
                raise ExperimentFailure(
                    f"inventory fragment declaration conflict: {name}"
                )
            target[name] = dict(resolution)
            return
        existing["useCaseIds"] = sorted(
            set(existing.get("useCaseIds") or [])
            | set(candidate.get("useCaseIds") or []),
            key=lambda value: order.get(str(value), len(order)),
        )

    for use_case in index.use_cases:
        fragment = fragments.get(use_case.id)
        if fragment is None:
            raise ExperimentFailure(f"missing inventory fragment: {use_case.id}")
        for item in fragment.get("Classes", []) or []:
            if not isinstance(item, Mapping):
                raise ExperimentFailure(f"invalid class fragment item: {use_case.id}")
            scopes = set(item.get("useCaseIds") or [])
            if scopes != {use_case.id}:
                raise ExperimentFailure(
                    f"fragment scope must be exactly its use case: {use_case.id}"
                )
            merge_named(classes, item, "className", "class")
        for item in fragment.get("DataTypes", []) or []:
            if not isinstance(item, Mapping):
                raise ExperimentFailure(
                    f"invalid DataType fragment item: {use_case.id}"
                )
            merge_named(data_types, item, "name", "type")
        for item in fragment.get("Relationships", []) or []:
            if not isinstance(item, Mapping):
                raise ExperimentFailure(
                    f"invalid relationship fragment item: {use_case.id}"
                )
            pair = _relationship_pair(item)
            existing = relationships.get(pair)
            if existing is None:
                relationships[pair] = dict(item)
                continue
            if canonical_digest(
                _canonical_relationship_contract(existing)
            ) == canonical_digest(_canonical_relationship_contract(item)):
                continue
            resolution = (relationship_resolutions or {}).get(f"relationship:{pair}")
            if resolution is None:
                raise ExperimentFailure(
                    f"inventory relationship declaration conflict: {pair}"
                )
            relationships[pair] = dict(resolution)
    return {
        "Classes": [classes[name] for name in sorted(classes)],
        "DataTypes": [data_types[name] for name in sorted(data_types)],
        "Relationships": [relationships[key] for key in sorted(relationships)],
    }


def _compose_inventory(
    *,
    index: ScenarioIndex,
    fragments: Mapping[str, Mapping[str, Any]],
    run_dir: Path,
    budget: LogicalCallBudget,
    parallelism: int,
) -> dict[str, Any]:
    """Resolve cross-fragment conflicts and persist one deterministic inventory."""
    fragment_payload = {
        use_case.id: dict(fragments[use_case.id]) for use_case in index.use_cases
    }
    _write_json(
        run_dir / "validated" / "inventory-fragments.json", fragment_payload
    )
    resolutions = _resolve_inventory_conflicts(
        index=index,
        conflicts=_inventory_conflicts(index, fragment_payload),
        fragments=fragment_payload,
        run_dir=run_dir,
        budget=budget,
        parallelism=parallelism,
    )
    relationship_resolutions = _resolve_inventory_relationship_conflicts(
        conflicts=_inventory_relationship_conflicts(index, fragment_payload),
        run_dir=run_dir,
        budget=budget,
        parallelism=parallelism,
    )
    merged = _merge_inventory_fragments(
        index,
        fragment_payload,
        resolutions=resolutions,
        relationship_resolutions=relationship_resolutions,
    )
    accepted = AcceptedInventory.from_payload(merged)
    report = validate_inventory(accepted.as_payload(), index)
    inventory_model(accepted)
    findings = [*report.errors, *finding_text(report.findings)]
    if findings:
        raise ExperimentFailure(
            "merged inventory remained invalid: " + "; ".join(findings)
        )
    result = accepted.as_payload()
    _write_json(run_dir / "validated" / "inventory.json", result)
    _write_json(
        run_dir / "validated" / "inventory-merge.json",
        {
            "fragmentsDigest": canonical_digest(fragment_payload),
            "resolutionsDigest": canonical_digest(
                {
                    "items": resolutions,
                    "relationships": relationship_resolutions,
                }
            ),
            "inventoryDigest": canonical_digest(result),
        },
    )
    return result


def _use_case_inputs(
    index: ScenarioIndex, inventory: Mapping[str, Any], use_case: UseCase
) -> UseCaseInputs:
    groups = tuple(item for item in index.groups if item.use_case_id == use_case.id)
    allowed_steps = tuple(step.id for step in use_case.steps)
    scoped_classes = [
        item
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
        and use_case.id in (item.get("useCaseIds") or item.get("use_case_ids") or [])
    ]
    allowed_owners = tuple(str(item.get("className") or "") for item in scoped_classes)
    actor_concepts = _semantic_words(use_case.primary_actor)
    durable_entities = tuple(
        str(item.get("className") or "")
        for item in scoped_classes
        if item.get("stereotype") == "Entity"
        and _semantic_words(str(item.get("className") or "")) != actor_concepts
    )
    if not groups or not allowed_steps or not allowed_owners:
        raise ExperimentFailure(f"incomplete generation slice for {use_case.id}")
    return UseCaseInputs(
        use_case,
        groups,
        allowed_steps,
        allowed_owners,
        durable_entities,
    )


OPERATION_PROMPT = """
Design only the operation contracts for one use case. Return exactly the supplied JSON
schema. Do not return calls, bindings, relationships, commentary, or Markdown.

Use only fixedClasses. Every operation stepRef must be one of allowedStepIds and their
union must cover every allowedStepId. Copy each stepRef character-for-character from
allowedStepIds: never prefix it with a class name, replace ':' with '_', or otherwise
rewrite it. For each actorEntries item, one Boundary root
operation must cover its groupKey actor step and every actor interaction in
requiredStepRefs that no Control or Entity covers. At least one Control operation must
cover a main-flow requiredStepRef in that same group; an extension-only error handler
does not provide the root's required main-flow delegation. Do not repurpose an
extension/failure-only handler by attaching unrelated main steps to it. A separate
same-Boundary output method cannot be a child call, so fold those output stepRefs into
the group's Boundary root operation. Each required Entity listed in durableEntities must
own at least one operation that reads or changes its state. An Entity whose name merely
matches the primary actor is deliberately omitted from durableEntities: do not add a
receive-result, display-result, or authorization operation to that actor-shaped Entity
just to give every fixed class a method.

Keep each owner method name unique. Parameters and return types must use the listed
fixed types, built-in scalar types, or DataTypes declared in this response. Declare a
non-empty valueObject for structured actor input or output; use concrete fields only
when the scenario names them. Choose signatures that can form a Boundary -> Control ->
Entity call tree: a child cannot consume its synchronous parent's eventual return.
Downstream parameters must therefore be available from Boundary inputs or fields, or
from a completed earlier sibling result. Use case-specific names for local DataTypes
so unrelated fragments do not collide accidentally. For a query named ByX or ForX,
its parameters or declared valueObject fields must actually carry X. Do not copy the
same query signature onto two different Entity owners merely to make both participate.
When one query returns List<X> and a later query processes those items, make the later
query accept that exact List<X> and iterate internally. Do not demand an unavailable
single X or X identifier.

Before returning, simulate that source graph for every actor entry. A direct Control's
parameters must be a subset of its Boundary root sources (normally the same command
object). Each later operation parameter must match a Boundary parameter, a field of a
Boundary valueObject, or a completed earlier sibling result. Do not require a generated
identifier, a derived status, or a newly created Entity as a child-call parameter unless
an earlier sibling explicitly returns that exact type. Generate or resolve such values
inside the receiving operation, forward the actor command/field instead, or omit the
parameter when the receiver owns the state it changes.
""".strip()


OPERATION_PATCH_PROMPT = """
Return only a scoped compare-and-swap patch for the confirmed semantic findings.
Copy baseDigest and findingIds exactly. Each edit may ADD, REPLACE, or REMOVE one
operation under one fixed owner. Copy operationRef and expectedDigest from
operationTargets. For ADD, expectedDigest is the supplied digest for absence.
For ADD/REPLACE, replacement is one complete operation object; never return Classes,
DataTypes, calls, relationships, or a complete fragment. Do not edit anything not
needed by the confirmed findings. The patched fragment must still cover every allowed
step, use only fixed owners/types, and leave unrelated operations byte-for-byte intact.
Because this patch cannot add a DataType, every parameter and return type must already
be declared in currentFragment or global fixedClasses/fixedDataTypes. Never copy a
use-case-local DTO from another operation fragment.
""".strip()


OPERATION_SOURCE_PATCH_PROMPT = """
Repair only the listed sourceability obligations. Return exactly the supplied patch
schema, without commentary or Markdown. Copy baseDigest and every findingId exactly.
Each edit must use REPLACE and copy owner, operationRef, and expectedDigest from
operationTargets. Do not add or remove operations or DataTypes. Preserve the target's
method name, returnType, and stepRefs exactly; change only its parameters.

Use the exact finite sources in each finding. Same-typed identifiers with different
names are not interchangeable. A generated identifier such as entryId is not an actor
source. A direct Control may consume only Boundary inputs or their valueObject fields,
and an Entity child cannot consume its parent Control's eventual return. Prefer
forwarding the actor command or its matching field. Every existing parameter's business
concept must remain present in a replacement parameter name, declared type, or declared
valueObject field. Never delete or rename a parameter merely to evade binding. If the
fixed operation cannot be repaired under those constraints, do not fabricate a source.
When the only earlier producer returns List<X>, a later consumer that works across those
items should accept that exact List<X> and iterate internally; do not replace it with an
unavailable scalar ID or a single X.
Do not invent actor inputs, literals, implicit conversions, or undeclared types.
""".strip()


def _operation_prompt(
    inputs: UseCaseInputs,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    scoped_classes = [
        item
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
        and str(item.get("className") or "") in inputs.allowed_owners
    ]
    return {
        "useCase": {
            "id": inputs.use_case.id,
            "name": inputs.use_case.name,
            "primaryActor": inputs.use_case.primary_actor,
            "steps": [
                {
                    "id": step.id,
                    "subject": step.subject,
                    "sentence": step.sentence,
                    "branch": step.branch,
                    "condition": step.condition,
                }
                for step in inputs.use_case.steps
            ],
            "preconditions": inputs.use_case.specification.get("preconditions") or [],
            "successGuarantee": (
                inputs.use_case.specification.get("success_guarantee") or []
            ),
            "minimalGuarantee": (
                inputs.use_case.specification.get("minimal_guarantee") or []
            ),
        },
        "actorEntries": [
            {
                "groupKey": group.id,
                "actor": group.entry_actor,
                "requiredStepRefs": list(group.required_step_ids),
            }
            for group in inputs.groups
        ],
        "allowedStepIds": list(inputs.allowed_steps),
        "fixedClasses": scoped_classes,
        "fixedDataTypes": [
            item
            for item in inventory.get("DataTypes", []) or []
            if isinstance(item, Mapping)
            and inputs.use_case.id in (item.get("useCaseIds") or [])
        ],
        "durableEntities": list(inputs.durable_entities),
    }


def _operation_call_skeleton_findings(
    fragment: Mapping[str, Any],
    *,
    inputs: UseCaseInputs,
    inventory: Mapping[str, Any],
) -> list[str]:
    """Prove that every actor-entry group can form a BCE call-tree skeleton."""
    roles = {
        str(item.get("className") or ""): str(item.get("stereotype") or "")
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
    }
    operations: list[tuple[str, set[str]]] = []
    for class_set in fragment.get("Classes", []) or []:
        if not isinstance(class_set, Mapping):
            continue
        owner = str(class_set.get("className") or class_set.get("name") or "")
        role = str(class_set.get("stereotype") or roles.get(owner) or "").casefold()
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            operations.append(
                (
                    role,
                    {
                        str(item)
                        for item in operation.get("stepRefs", []) or []
                        if str(item)
                    },
                )
            )

    findings: list[str] = []
    branches = {step.id: step.branch for step in inputs.use_case.steps}
    for group in inputs.groups:
        required = set(group.required_step_ids)
        group_operations = [
            (role, refs) for role, refs in operations if refs & required
        ]
        boundary_only_steps = {
            step_ref
            for step_ref in required
            if any(step_ref in refs for _, refs in group_operations)
            and all(
                role == "boundary"
                for role, refs in group_operations
                if step_ref in refs
            )
        }
        root_required = set(boundary_only_steps)
        if group.actor_step:
            root_required.add(group.actor_step)
        boundary_roots = [
            refs
            for role, refs in group_operations
            if role == "boundary" and root_required <= refs
        ]
        if not boundary_roots:
            findings.append(
                f"{group.id}: no single Boundary root covers actor-entry and "
                "Boundary-only steps: "
                + ", ".join(sorted(root_required or required))
            )
        primary_flow_steps = {
            step_ref
            for step_ref in required
            if branches.get(step_ref) == "main"
        } or required
        if not any(
            role == "control" and refs & primary_flow_steps
            for role, refs in group_operations
        ):
            findings.append(
                f"{group.id}: no Control operation covers a main-flow step in the "
                "actor-entry group; add or use a main-flow Control without "
                "repurposing an extension-only handler: "
                + ", ".join(sorted(primary_flow_steps))
            )
    return findings


def _semantic_words(value: str) -> set[str]:
    words = {
        item.casefold()
        for item in re.findall(
            r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", value
        )
    }
    if words & {"seat", "seats", "capacity"}:
        words.update({"seat", "capacity"})
    return words - {
        "id",
        "value",
        "data",
        "input",
        "request",
        "details",
        "command",
    }


def _semantic_names_compatible(left: str, right: str) -> bool:
    left_words = _semantic_words(left)
    right_words = _semantic_words(right)
    if not left_words or not right_words:
        return left.casefold() == right.casefold()
    old_qualifiers = {"old", "current", "previous"}
    new_qualifiers = {"new", "target", "replacement"}
    if (left_words & old_qualifiers and right_words & new_qualifiers) or (
        left_words & new_qualifiers and right_words & old_qualifiers
    ):
        return False
    return bool(
        (left_words - old_qualifiers - new_qualifiers)
        & (right_words - old_qualifiers - new_qualifiers)
    )


def _parameter_semantic_words(
    parameter: Mapping[str, Any],
    data_types: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    parameter_type = canonical_type(parameter.get("type", ""))
    scalar_types = {
        "String",
        "UUID",
        "int",
        "long",
        "float",
        "double",
        "boolean",
        "LocalDate",
        "LocalDateTime",
    }
    values = [str(parameter.get("name") or "")]
    if parameter_type not in scalar_types:
        values.append(parameter_type)
        data_type = data_types.get(parameter_type)
        if data_type is not None:
            values.extend(
                str(field.get("name") or "")
                for field in data_type.get("fields", []) or []
                if isinstance(field, Mapping)
            )
    return _semantic_words(" ".join(values)) - {"list", "array"}


def _operation_signature_semantic_findings(
    fragment: Mapping[str, Any],
    *,
    inventory: Mapping[str, Any],
) -> list[str]:
    roles = {
        str(item.get("className") or ""): str(item.get("stereotype") or "")
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
    }
    data_types = {
        canonical_type(item.get("name", "")): item
        for source in (
            inventory.get("DataTypes", []) or [],
            fragment.get("DataTypes", []) or [],
        )
        for item in source
        if isinstance(item, Mapping)
    }
    query_owners: dict[tuple[str, tuple[str, ...], str], set[str]] = {}
    findings: list[str] = []
    for class_set in fragment.get("Classes", []) or []:
        if not isinstance(class_set, Mapping):
            continue
        owner = str(class_set.get("className") or class_set.get("name") or "")
        role = str(class_set.get("stereotype") or roles.get(owner) or "").casefold()
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            name = str(operation.get("name") or "")
            parameters = [
                item
                for item in operation.get("parameters", []) or []
                if isinstance(item, Mapping)
            ]
            parameter_semantics: set[str] = set()
            for parameter in parameters:
                parameter_semantics.update(
                    _parameter_semantic_words(parameter, data_types)
                )
            qualifier_match = re.search(r"(?:By|For)([A-Z].*)$", name)
            if qualifier_match is not None:
                qualifier_semantics = _semantic_words(qualifier_match.group(1))
                if (
                    qualifier_semantics
                    and not qualifier_semantics & parameter_semantics
                ):
                    findings.append(
                        f"{owner}::{name}: query qualifier "
                        f"{qualifier_match.group(1)} is absent from parameter names, "
                        "types, and declared fields"
                    )
            return_type = canonical_type(operation.get("returnType", "void"))
            if (
                role == "entity"
                and return_type != "void"
                and name.casefold().startswith(
                    ("get", "find", "fetch", "load", "retrieve")
                )
            ):
                signature = (
                    name.casefold(),
                    tuple(
                        canonical_type(parameter.get("type", ""))
                        for parameter in parameters
                    ),
                    return_type,
                )
                query_owners.setdefault(signature, set()).add(owner)
    for (name, parameter_types, return_type), owners in sorted(
        query_owners.items()
    ):
        if len(owners) < 2:
            continue
        findings.append(
            "duplicate Entity query contract has multiple owners: "
            f"{name}({','.join(parameter_types)}):{return_type} -> "
            + ", ".join(sorted(owners))
        )
    return findings


def _parameter_source_deficits(
    operation: Mapping[str, Any],
    available: list[tuple[str, str]],
) -> list[str]:
    """Name parameters that cannot receive distinct, semantically typed sources."""
    available_by_type: dict[str, list[str]] = {}
    for source_ref, type_name in available:
        available_by_type.setdefault(type_name, []).append(source_ref)

    def source_name(source_ref: str) -> str:
        return source_ref.rsplit("#", 1)[-1].rsplit(":", 1)[-1]

    scalar_types = {
        "String",
        "UUID",
        "int",
        "long",
        "float",
        "double",
        "boolean",
        "LocalDate",
        "LocalDateTime",
    }
    deficits: list[str] = []
    parameters_by_type: dict[str, list[Mapping[str, Any]]] = {}
    for parameter in operation.get("parameters", []) or []:
        if isinstance(parameter, Mapping):
            parameters_by_type.setdefault(
                canonical_type(parameter.get("type")), []
            ).append(parameter)
    for type_name, parameters in parameters_by_type.items():
        sources = list(available_by_type.get(type_name, []))
        unmatched: list[Mapping[str, Any]] = []
        for parameter in parameters:
            parameter_name = str(parameter.get("name") or "")
            exact_index = next(
                (
                    index
                    for index, source_ref in enumerate(sources)
                    if _semantic_names_compatible(
                        parameter_name, source_name(source_ref)
                    )
                ),
                None,
            )
            if exact_index is None:
                unmatched.append(parameter)
            else:
                sources.pop(exact_index)
        for parameter in unmatched:
            fallback_index = next(
                (
                    index
                    for index, source_ref in enumerate(sources)
                    if source_ref.startswith("call:")
                    or type_name not in scalar_types
                ),
                None,
            )
            if fallback_index is None:
                deficits.append(
                    f"{parameter.get('name')}:{type_name or '<invalid>'}"
                )
            else:
                sources.pop(fallback_index)
    return deficits


def _source_type_summary(available: list[tuple[str, str]]) -> str:
    counts: dict[str, int] = {}
    for _, type_name in available:
        counts[type_name] = counts.get(type_name, 0) + 1
    return ", ".join(
        f"{type_name} x{count}" if count > 1 else type_name
        for type_name, count in sorted(counts.items())
    ) or "none"


def _source_detail_summary(available: list[tuple[str, str]]) -> str:
    return ", ".join(
        f"{source_ref}:{type_name}" for source_ref, type_name in available
    ) or "none"


def _operation_sourceability_findings(
    fragment: Mapping[str, Any],
    *,
    inputs: UseCaseInputs,
    inventory: Mapping[str, Any],
) -> list[str]:
    """Reject operation signatures that cannot form a typed call tree later."""
    roles = {
        str(item.get("className") or ""): str(item.get("stereotype") or "")
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
    }
    operations: dict[str, dict[str, Any]] = {}
    for class_set in fragment.get("Classes", []) or []:
        if not isinstance(class_set, Mapping):
            continue
        owner = str(class_set.get("className") or class_set.get("name") or "")
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            parameters = ",".join(
                f"{parameter.get('name')}:{parameter.get('type')}"
                for parameter in operation.get("parameters", []) or []
                if isinstance(parameter, Mapping)
            )
            operation_ref = f"{owner}::{operation.get('name')}({parameters})"
            operations[operation_ref] = {
                **operation,
                "className": owner,
                "stereotype": roles.get(owner, ""),
            }
    data_types = {
        str(item.get("name") or ""): item
        for source in (inventory, fragment)
        for item in source.get("DataTypes", []) or []
        if isinstance(item, Mapping)
    }

    findings: list[str] = []
    for group_index, group in enumerate(inputs.groups, start=1):
        required = set(group.required_step_ids)
        relevant = {
            operation_ref: operation
            for operation_ref, operation in operations.items()
            if required & set(operation.get("stepRefs", []) or [])
        }
        roots = [
            operation_ref
            for operation_ref, operation in relevant.items()
            if str(operation.get("stereotype") or "").casefold() == "boundary"
            and (
                group.actor_step is None
                or group.actor_step in set(operation.get("stepRefs", []) or [])
            )
        ]
        feasible = False
        diagnostics: list[tuple[int, str]] = []
        for root_index, root_ref in enumerate(roots, start=1):
            root = CallChoice(
                callId=f"sourceability_root_{group_index}_{root_index}",
                receiverOperationId=root_ref,
                parentCallId=None,
                groupKey=group.id,
                guardRefs=[],
                exportResult=False,
            )
            initial_sources = _call_input_sources(
                root,
                operations=relevant,
                data_types=data_types,
            )
            controls = [
                operation_ref
                for operation_ref, operation in relevant.items()
                if str(operation.get("stereotype") or "").casefold() == "control"
            ]
            for anchor_index, anchor_ref in enumerate(controls, start=1):
                anchor_deficits = _parameter_source_deficits(
                    relevant[anchor_ref], initial_sources
                )
                if anchor_deficits:
                    diagnostics.append(
                        (
                            len(anchor_deficits),
                            f"Boundary root {root_ref} exposes source types "
                            f"[{_source_type_summary(initial_sources)}], but direct "
                            f"Control {anchor_ref} is blocked by parameters "
                            f"[{', '.join(anchor_deficits)}]. A direct Control cannot "
                            "consume a later child result; change those parameters to "
                            "the exact root sources ["
                            f"{_source_detail_summary(initial_sources)}], remove them, "
                            "or add a main-flow "
                            "Control whose inputs are root-sourceable.",
                        )
                    )
                    continue
                remaining = tuple(
                    CallChoice(
                        callId=f"sourceability_call_{group_index}_{call_index}",
                        receiverOperationId=operation_ref,
                        parentCallId=(
                            f"sourceability_anchor_{group_index}_{anchor_index}"
                        ),
                        groupKey=group.id,
                        guardRefs=[],
                        exportResult=False,
                    )
                    for call_index, (operation_ref, operation) in enumerate(
                        relevant.items(), start=1
                    )
                    if operation_ref not in {root_ref, anchor_ref}
                    and str(operation.get("stereotype") or "").casefold()
                    != "boundary"
                )
                if _dependency_ordered_calls(
                    remaining,
                    operations=relevant,
                    available=initial_sources,
                ) is not None:
                    feasible = True
                    break
                current_sources = list(initial_sources)
                pending = list(remaining)
                while pending:
                    progressed = False
                    for call in tuple(pending):
                        operation = relevant[call.receiver_operation_id]
                        if _parameter_source_deficits(operation, current_sources):
                            continue
                        pending.remove(call)
                        return_type = canonical_type(
                            operation.get("returnType", "void")
                        )
                        if return_type != "void":
                            current_sources.append(
                                (f"call:{call.call_id}.return", return_type)
                            )
                        progressed = True
                    if not progressed:
                        break
                blocked = [
                    f"{call.receiver_operation_id} missing "
                    f"[{', '.join(_parameter_source_deficits(relevant[call.receiver_operation_id], current_sources))}]"
                    for call in pending
                ]
                diagnostics.append(
                    (
                        sum(
                            len(
                                _parameter_source_deficits(
                                    relevant[call.receiver_operation_id],
                                    current_sources,
                                )
                            )
                            for call in pending
                        ),
                        f"With Boundary root {root_ref} and direct Control "
                        f"{anchor_ref}, later siblings are blocked: "
                        f"{'; '.join(blocked)}. Available source types after all "
                        f"sourceable earlier siblings are "
                        f"[{_source_type_summary(current_sources)}]; exact finite "
                        f"sources are [{_source_detail_summary(current_sources)}]. "
                        "The direct "
                        "Control's eventual return is unavailable to its own children; "
                        "forward one of those exact root fields/DTOs, omit a generated "
                        "or internally derived parameter, or provide a compatible "
                        "earlier sibling producer.",
                    )
                )
            if feasible:
                break
        if not feasible:
            signatures = ", ".join(sorted(relevant))
            hint = (
                min(diagnostics, key=lambda item: (item[0], item[1]))[1]
                if diagnostics
                else "No eligible Boundary-root/direct-Control pair exists."
            )
            findings.append(
                f"{group.id}: operation signatures cannot form a sourceable "
                "Boundary-Control tree from actor input fields and earlier sibling "
                "results; repair the incompatible consumer/producer types. "
                f"{hint} In-scope signatures: {signatures}"
            )
    return findings


def _validated_operation_fragment_for_inputs(
    fragment: Mapping[str, Any],
    *,
    context: OperationContext,
    inputs: UseCaseInputs,
    inventory: Mapping[str, Any],
) -> ValidatedOperationFragment:
    try:
        result = validated_operation_fragment(fragment, context)
    except OperationValidationError as error:
        detailed_source_findings = _operation_sourceability_findings(
            fragment,
            inputs=inputs,
            inventory=inventory,
        )
        raise OperationValidationError(
            list(dict.fromkeys((*error.findings, *detailed_source_findings)))
        ) from error
    findings = _operation_call_skeleton_findings(
        result.payload, inputs=inputs, inventory=inventory
    )
    findings.extend(
        _operation_signature_semantic_findings(
            result.payload,
            inventory=inventory,
        )
    )
    findings.extend(
        _operation_sourceability_findings(
            result.payload, inputs=inputs, inventory=inventory
        )
    )
    if findings:
        raise OperationValidationError(findings)
    return result


def _sourceability_patch_input(
    fragment: Mapping[str, Any],
    *,
    context: OperationContext,
    inputs: UseCaseInputs,
    inventory: Mapping[str, Any],
) -> tuple[ValidatedOperationFragment, dict[str, Any]] | None:
    """Build a CAS repair input only when sourceability is the remaining defect."""
    try:
        core = validated_operation_fragment(fragment, context)
    except OperationValidationError:
        return None
    skeleton_findings = _operation_call_skeleton_findings(
        core.payload, inputs=inputs, inventory=inventory
    )
    skeleton_findings.extend(
        _operation_signature_semantic_findings(
            core.payload,
            inventory=inventory,
        )
    )
    if skeleton_findings:
        return None
    source_findings = _operation_sourceability_findings(
        core.payload, inputs=inputs, inventory=inventory
    )
    if not source_findings:
        return None

    finding_ids = [
        f"sourceability-{canonical_digest(finding)[:16]}"
        for finding in source_findings
    ]
    targets: list[dict[str, Any]] = []
    for class_set in core.payload.get("Classes", []) or []:
        if not isinstance(class_set, Mapping):
            continue
        owner = str(class_set.get("className") or class_set.get("name") or "")
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            parameters = ",".join(
                f"{parameter.get('name')}:{parameter.get('type')}"
                for parameter in operation.get("parameters", []) or []
                if isinstance(parameter, Mapping)
            )
            operation_ref = f"{owner}::{operation.get('name')}({parameters})"
            blocked_markers = (
                f"Control {operation_ref} is blocked",
                f"{operation_ref} missing [",
            )
            if not any(
                marker in finding
                for finding in source_findings
                for marker in blocked_markers
            ):
                continue
            targets.append(
                {
                    "owner": owner,
                    "operationRef": operation_ref,
                    "expectedDigest": canonical_digest(operation),
                    "currentOperation": dict(operation),
                }
            )
    if not targets:
        return None
    return core, {
        "task": "Patch only the blocked operation signatures.",
        "baseDigest": canonical_digest(core.payload),
        "findingIds": finding_ids,
        "findings": source_findings,
        "operationTargets": targets,
    }


def _apply_sourceability_patch(
    *,
    current: ValidatedOperationFragment,
    patch_payload: Mapping[str, Any],
    repair_input: Mapping[str, Any],
) -> dict[str, Any]:
    patch = OperationFragmentPatch.model_validate(patch_payload)
    finding_ids = tuple(str(value) for value in repair_input["findingIds"])
    if set(patch.finding_ids) != set(finding_ids):
        raise OperationPatchError("source patch must cover every findingId exactly")
    targets = {
        str(item["operationRef"]): item
        for item in repair_input["operationTargets"]
        if isinstance(item, Mapping)
    }
    scalar_types = {
        "String",
        "UUID",
        "int",
        "long",
        "float",
        "double",
        "boolean",
        "LocalDate",
        "LocalDateTime",
    }
    data_types = {
        canonical_type(item.get("name", "")): item
        for item in current.payload.get("DataTypes", []) or []
        if isinstance(item, Mapping)
    }
    for edit in patch.edits:
        target = targets.get(edit.operation_ref)
        if target is None or edit.owner != target.get("owner"):
            raise OperationPatchError(
                f"source patch target is outside the blocked set: {edit.operation_ref}"
            )
        if edit.action is not OperationPatchAction.REPLACE:
            raise OperationPatchError("source patch edits must use REPLACE")
        replacement = edit.replacement
        current_operation = target.get("currentOperation")
        if replacement is None or not isinstance(current_operation, Mapping):
            raise OperationPatchError("source patch replacement is required")
        if replacement.name != str(current_operation.get("name") or ""):
            raise OperationPatchError("source patch must preserve the method name")
        if canonical_type(replacement.return_type) != canonical_type(
            current_operation.get("returnType", "void")
        ):
            raise OperationPatchError("source patch must preserve the return type")
        if tuple(replacement.step_refs) != tuple(
            str(value) for value in current_operation.get("stepRefs", []) or []
        ):
            raise OperationPatchError("source patch must preserve exact stepRefs")
        old_parameters = [
            value
            for value in current_operation.get("parameters", []) or []
            if isinstance(value, Mapping)
        ]
        replacement_parameters = [
            parameter.model_dump(by_alias=True)
            for parameter in replacement.parameters
        ]
        replacement_semantics: set[str] = set()
        for replacement_payload in replacement_parameters:
            replacement_semantics.update(
                _parameter_semantic_words(replacement_payload, data_types)
            )
        for old_parameter in old_parameters:
            old_parameter_semantics = _parameter_semantic_words(
                old_parameter, data_types
            )
            if (
                old_parameter_semantics
                and not old_parameter_semantics & replacement_semantics
            ):
                raise OperationPatchError(
                    "source patch cannot discard an existing parameter's semantic "
                    f"role: {edit.operation_ref} -> "
                    f"{old_parameter.get('name')}:{old_parameter.get('type')}"
                )
        operation_context = f"{edit.owner} {replacement.name}"
        prior_semantics: set[str] = set()
        for old_parameter in old_parameters:
            prior_semantics.update(
                _parameter_semantic_words(old_parameter, data_types)
            )
        for replacement_parameter, parameter_payload in zip(
            replacement.parameters, replacement_parameters, strict=True
        ):
            parameter_semantics = _parameter_semantic_words(
                parameter_payload, data_types
            )
            if canonical_type(replacement_parameter.type) not in scalar_types:
                if (
                    parameter_semantics & prior_semantics
                    or parameter_semantics & _semantic_words(operation_context)
                ):
                    continue
                raise OperationPatchError(
                    "source patch cannot introduce an unrelated structured "
                    f"parameter: {edit.operation_ref} -> "
                    f"{replacement_parameter.name}:{replacement_parameter.type}"
                )
            if any(
                _semantic_names_compatible(
                    replacement_parameter.name, str(old.get("name") or "")
                )
                for old in old_parameters
            ):
                continue
            if _semantic_names_compatible(
                replacement_parameter.name, operation_context
            ):
                continue
            raise OperationPatchError(
                "source patch cannot introduce an unrelated scalar parameter by "
                f"renaming an unavailable value: {edit.operation_ref} -> "
                f"{replacement_parameter.name}:{replacement_parameter.type}"
            )
    attempt = apply_operation_fragment_patch(
        current,
        patch,
        confirmed_finding_ids=finding_ids,
    )
    return attempt.after


def _operation_fragment(
    *,
    inputs: UseCaseInputs,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    run_dir: Path,
    budget: LogicalCallBudget,
) -> ValidatedOperationFragment:
    checkpoint = run_dir / "validated" / "operations" / f"{inputs.use_case.id}.json"
    context = OperationContext.from_payload(
        inputs.use_case.id,
        inventory,
        scenario=scenario,
        allowed_step_ids=inputs.allowed_steps,
        durable_entity_names=inputs.durable_entities,
        allowed_owner_names=inputs.allowed_owners,
    )
    previous: dict[str, Any] | None = None
    findings: list[str] = []
    if checkpoint.exists():
        result = ValidatedOperationFragment.model_validate(_read_json(checkpoint))
        try:
            revalidated = _validated_operation_fragment_for_inputs(
                result.payload,
                context=context,
                inputs=inputs,
                inventory=inventory,
            )
        except OperationValidationError as error:
            revalidated = None
            previous = dict(result.payload)
            findings = list(error.findings)
        if revalidated is not None:
            _write_json(checkpoint, revalidated.model_dump(by_alias=True))
            print(
                f'{{"event":"checkpoint.revalidated","stage":"operation","useCaseId":"{inputs.use_case.id}"}}',
                flush=True,
            )
            return revalidated
    attempt_root = run_dir / "attempts" / "operations"
    seen: set[str] = set()
    completed_attempts = 0
    for attempt_number in range(1, MAX_OPERATION_GENERATION_ATTEMPTS + 1):
        attempt_checkpoint = attempt_root / (
            f"{inputs.use_case.id}-{OPERATION_ATTEMPT_VERSION}-{attempt_number}.json"
        )
        if not attempt_checkpoint.exists():
            continue
        completed_attempts = attempt_number
        try:
            candidate = OperationFragment.model_validate(
                _read_json(attempt_checkpoint)
            ).model_dump(by_alias=True)
        except ValueError:
            continue
        seen.add(canonical_digest(candidate))
        previous = candidate
        try:
            promoted = _validated_operation_fragment_for_inputs(
                candidate,
                context=context,
                inputs=inputs,
                inventory=inventory,
            )
        except OperationValidationError as error:
            findings = list(error.findings)
            continue
        _write_json(checkpoint, promoted.model_dump(by_alias=True))
        print(
            f'{{"event":"checkpoint.promoted","stage":"operation",'
            f'"useCaseId":"{inputs.use_case.id}","attempt":{attempt_number}}}',
            flush=True,
        )
        return promoted
    base_messages = [
        {"role": "system", "content": OPERATION_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                _operation_prompt(inputs, inventory), ensure_ascii=False
            ),
        },
    ]
    source_patch_findings: list[str] = []
    allow_source_patch = True
    for attempt_number in range(
        completed_attempts + 1, MAX_OPERATION_GENERATION_ATTEMPTS + 1
    ):
        repairing = previous is not None
        source_repair = (
            _sourceability_patch_input(
                previous,
                context=context,
                inputs=inputs,
                inventory=inventory,
            )
            if previous is not None and allow_source_patch
            else None
        )
        if source_repair is not None:
            current, repair_input = source_repair
            if source_patch_findings:
                repair_input = {
                    **repair_input,
                    "previousPatchValidationFindings": source_patch_findings,
                }
            parsed_patch = _invoke(
                budget=budget,
                operation="ExecutableOperationSourceRepair",
                messages=[
                    {"role": "system", "content": OPERATION_SOURCE_PATCH_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(repair_input, ensure_ascii=False),
                    },
                ],
                schema=OperationFragmentPatch,
                use_case_id=inputs.use_case.id,
                max_tokens=3072,
                reasoning_effort="medium",
            )
            patch_payload = OperationFragmentPatch.model_validate(
                parsed_patch
            ).model_dump(by_alias=True)
            _write_immutable_json(
                run_dir
                / "attempts"
                / "operation-source-patches"
                / (
                    f"{inputs.use_case.id}-{OPERATION_ATTEMPT_VERSION}-"
                    f"{attempt_number}.json"
                ),
                patch_payload,
            )
            try:
                previous = _apply_sourceability_patch(
                    current=current,
                    patch_payload=patch_payload,
                    repair_input=repair_input,
                )
            except OperationPatchError as error:
                source_patch_findings = [str(error)]
                findings = [f"sourceability patch rejected: {error}", *findings]
                allow_source_patch = False
                continue
            source_patch_findings = []
        else:
            messages = list(base_messages)
            if previous is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": (
                                    "Repair only this operation fragment and return it "
                                    "in full. Make the smallest change that resolves "
                                    "every finding. Preserve every existing DataType, "
                                    "operation, stepRef, actor entry, and sourceable "
                                    "parameter contract that is not named by a finding. "
                                    "Never trade a satisfied invariant for a new "
                                    "validation defect. Treat each finding as an "
                                    "actionable contract: use its listed available "
                                    "sources and exact allowedStepIds."
                                ),
                                "previousCandidate": previous,
                                "findings": findings,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            parsed = _invoke(
                budget=budget,
                operation=(
                    "ExecutableOperationsRepair"
                    if repairing
                    else "ExecutableOperations"
                ),
                messages=messages,
                schema=OperationFragment,
                use_case_id=inputs.use_case.id,
                max_tokens=4096,
                reasoning_effort="medium" if repairing else "low",
            )
            previous = OperationFragment.model_validate(parsed).model_dump(
                by_alias=True
            )
            allow_source_patch = True
            source_patch_findings = []
        _write_immutable_json(
            attempt_root
            / (
                f"{inputs.use_case.id}-{OPERATION_ATTEMPT_VERSION}-"
                f"{attempt_number}.json"
            ),
            previous,
        )
        digest = canonical_digest(previous)
        if digest in seen:
            findings = [
                "The candidate repeats a previously rejected operation fragment. "
                "Return a changed candidate that applies the smallest signature edits "
                "requested by the prior findings.",
                *findings,
            ]
            continue
        seen.add(digest)
        try:
            result = _validated_operation_fragment_for_inputs(
                previous,
                context=context,
                inputs=inputs,
                inventory=inventory,
            )
        except OperationValidationError as error:
            findings = list(error.findings)
            continue
        _write_json(checkpoint, result.model_dump(by_alias=True))
        return result
    raise ExperimentFailure(
        f"{inputs.use_case.id} operation remained invalid after "
        f"{MAX_OPERATION_GENERATION_ATTEMPTS} local attempts: "
        + "; ".join(findings)
    )


LOCAL_SEMANTICS_GENERATION_VERSION = "local-semantics-split-v6"

LOCAL_OBLIGATIONS_PROMPT = """
Classify the fixed scenario sources for exactly one use case. Return exactly one
decision for every supplied sourceRef and no others. Copy sourceRef exactly and choose
kind only from that source's allowedKinds. Use PRECONDITION for a supplied precondition
that depends on actor identity, authorization context, or another fact not represented
by durable Entity state. Use INTERACTION for actor/system exchanges, OBSERVATION for
reading durable Entity state, STATE_TRANSITION only when the scenario explicitly
requires durable state to change, and OUTCOME for a delivered success or
business-failure result. OBSERVATION and STATE_TRANSITION must copy every directly
observed or changed state from that source's allowedStateRefs into stateRefs. A single
scenario source may change several Entity states; include each one. PRECONDITION,
INTERACTION, and OUTCOME must use an empty stateRefs list. Do not create obligation IDs
or operation contracts. Expected text and conditions are copied deterministically from
the scenario, so do not return them.
""".strip()

LOCAL_ENTITY_SEMANTICS_PROMPT = """
Define state effects only for the supplied Entity-owned operations. Return exactly one
decision for every entityOperationRef and no others. Copy operationRef and stateRef
exactly. Follow requiredResponsibility, but do not return it. For QUERY return no
effects. Its observations are assembled deterministically from scenario obligations and
the Entity owner. For MUTATE cover every requiredTransitionStateRef with state-changing
effects. Every effect must target state owned by the operation's Entity owner. Use
UPDATE when state changes but the scenario supplies no exact replacement value or
numeric delta; never invent placeholder, empty, or default values. SET requires an
explicit scenario-grounded value; INCREMENT/DECREMENT require a positive delta;
UPDATE/CREATE/DELETE use neither value nor delta. Do not classify Boundary or Control
operations, create IDs, map realizes/outcomes, or invent delegation; those fields are
assembled deterministically.
""".strip()


def _available_state_refs(inventory: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for class_item in inventory.get("Classes", []) or []:
        if not isinstance(class_item, Mapping):
            continue
        owner = str(class_item.get("className") or class_item.get("name") or "")
        role = str(class_item.get("stereotype") or class_item.get("kind") or "")
        if role.casefold() != "entity":
            continue
        result.append({"stateRef": owner, "executionOwner": owner})
        for field in class_item.get("fields", []) or []:
            field_name = (
                str(field.get("name") or "").strip()
                if isinstance(field, Mapping)
                else str(field).partition(":")[0].strip()
            )
            if field_name:
                result.append(
                    {
                        "stateRef": f"{owner}.{field_name}",
                        "executionOwner": owner,
                    }
                )
    return result


def _fragment_operation_refs(
    fragment: ValidatedOperationFragment,
) -> list[str]:
    references: list[str] = []
    for class_set in fragment.payload.get("Classes", []) or []:
        if not isinstance(class_set, Mapping):
            continue
        owner = str(class_set.get("className") or class_set.get("name") or "")
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            parameters = ",".join(
                f"{parameter.get('name')}:{parameter.get('type')}"
                for parameter in operation.get("parameters", []) or []
                if isinstance(parameter, Mapping)
            )
            references.append(f"{owner}::{operation.get('name')}({parameters})")
    return sorted(references)


def _scenario_source_descriptors(inputs: UseCaseInputs) -> list[dict[str, Any]]:
    result = [
        {
            "sourceRef": step.id,
            "subject": step.subject,
            "sentence": step.sentence,
            "branch": step.branch,
            "condition": step.condition or None,
        }
        for step in inputs.use_case.steps
    ]
    raw_preconditions = inputs.use_case.specification.get("preconditions") or []
    precondition_values = (
        list(raw_preconditions.values())
        if isinstance(raw_preconditions, Mapping)
        else list(raw_preconditions)
    )
    normalized_preconditions = [
        str(value).strip() for value in precondition_values if str(value).strip()
    ]
    result.extend(
        {
            "sourceRef": reference,
            "subject": "precondition",
            "sentence": sentence,
            "branch": "precondition",
            "condition": sentence,
        }
        for reference, sentence in zip(
            inputs.use_case.precondition_refs,
            normalized_preconditions,
            strict=True,
        )
    )
    return result


def _fragment_operation_entries(
    fragment: ValidatedOperationFragment,
    inventory: Mapping[str, Any],
) -> list[dict[str, Any]]:
    roles = {
        str(item.get("className") or item.get("name") or ""): str(
            item.get("stereotype") or item.get("kind") or ""
        )
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
    }
    result: list[dict[str, Any]] = []
    for class_set in fragment.payload.get("Classes", []) or []:
        if not isinstance(class_set, Mapping):
            continue
        owner = str(class_set.get("className") or class_set.get("name") or "")
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            parameters = ",".join(
                f"{parameter.get('name')}:{parameter.get('type')}"
                for parameter in operation.get("parameters", []) or []
                if isinstance(parameter, Mapping)
            )
            result.append(
                {
                    "operationRef": (
                        f"{owner}::{operation.get('name')}({parameters})"
                    ),
                    "owner": owner,
                    "ownerRole": roles.get(owner, ""),
                    "stepRefs": list(operation.get("stepRefs", []) or []),
                }
            )
    return sorted(result, key=lambda item: str(item["operationRef"]))


def _obligation_source_contracts(
    *,
    sources: list[dict[str, Any]],
    available_states: list[dict[str, str]],
    operation_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach finite kind/state choices to each scenario source."""
    states_by_owner: dict[str, list[str]] = {}
    for state in available_states:
        states_by_owner.setdefault(state["executionOwner"], []).append(
            state["stateRef"]
        )
    entity_owners_by_source: dict[str, set[str]] = {}
    for entry in operation_entries:
        if str(entry.get("ownerRole") or "").casefold() != "entity":
            continue
        for step_ref in entry.get("stepRefs", []) or []:
            entity_owners_by_source.setdefault(str(step_ref), set()).add(
                str(entry["owner"])
            )

    def words(value: str) -> set[str]:
        parts = re.findall(
            r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+",
            value.replace("_", " "),
        )
        normalized: set[str] = set()
        for part in parts:
            word = part.casefold()
            if len(word) <= 1:
                continue
            if len(word) > 4 and word.endswith("ies"):
                word = f"{word[:-3]}y"
            elif (
                len(word) > 3
                and word.endswith("s")
                and not word.endswith(("ss", "us", "is"))
            ):
                word = word[:-1]
            normalized.add(word)
        return normalized

    def state_concepts(value: str) -> set[str]:
        concepts = words(value)
        if concepts & {"seat", "capacity"}:
            concepts.update({"seat", "capacity"})
        return concepts

    def precondition_state_refs(source: Mapping[str, Any]) -> list[str]:
        source_words = words(
            f"{source.get('sentence') or ''} {source.get('condition') or ''}"
        )
        candidates: set[str] = set()
        for owner, owner_states in states_by_owner.items():
            if words(owner) <= source_words:
                candidates.add(owner)
                continue
            for state_ref in owner_states:
                if state_ref == owner:
                    continue
                field_words = words(state_ref.partition(".")[2])
                if field_words and field_words <= source_words:
                    candidates.add(state_ref)
        return sorted(candidates)

    def operation_state_refs(
        source: Mapping[str, Any], owners: set[str]
    ) -> list[str]:
        source_ref = str(source["sourceRef"])
        sentence = str(source.get("sentence") or "")
        candidates: set[str] = set()
        for entry in operation_entries:
            owner = str(entry.get("owner") or "")
            if owner not in owners or source_ref not in set(
                entry.get("stepRefs", []) or []
            ):
                continue
            operation_name = str(entry.get("operationRef") or "").partition("::")[
                2
            ].partition("(")[0]
            operation_words = words(operation_name)
            operation_concepts = operation_words - {
                "create",
                "delete",
                "remove",
                "add",
                "save",
                "record",
                "store",
                "persist",
                "set",
                "update",
                "adjust",
                "change",
                "load",
                "find",
                "get",
                "read",
            }
            operation_concepts = state_concepts(" ".join(operation_concepts))
            matching_concepts = operation_concepts or (
                state_concepts(sentence)
                - {
                    "create",
                    "delete",
                    "remove",
                    "add",
                    "save",
                    "record",
                    "store",
                    "persist",
                    "set",
                    "update",
                    "adjust",
                    "change",
                }
            )
            matched_fields = {
                state_ref
                for state_ref in states_by_owner.get(owner, [])
                if state_ref != owner
                and state_concepts(state_ref.partition(".")[2])
                <= matching_concepts
            }
            owns_lifecycle = bool(
                operation_words
                & {
                    "create",
                    "delete",
                    "remove",
                    "add",
                    "save",
                    "record",
                    "store",
                    "persist",
                }
            )
            if owns_lifecycle or not matched_fields:
                candidates.add(owner)
            candidates.update(matched_fields)
        return sorted(candidates)

    result: list[dict[str, Any]] = []
    for source in sources:
        source_ref = str(source["sourceRef"])
        is_precondition = str(source.get("branch") or "") == "precondition"
        if is_precondition:
            allowed_state_refs = precondition_state_refs(source)
            allowed_kinds = [ObligationKind.PRECONDITION.value]
            if allowed_state_refs:
                allowed_kinds.append(ObligationKind.OBSERVATION.value)
        else:
            owners = entity_owners_by_source.get(source_ref, set())
            allowed_state_refs = operation_state_refs(source, owners)
            allowed_kinds = [
                ObligationKind.INTERACTION.value,
                ObligationKind.OUTCOME.value,
            ]
            if allowed_state_refs:
                allowed_kinds.extend(
                    [
                        ObligationKind.OBSERVATION.value,
                        ObligationKind.STATE_TRANSITION.value,
                    ]
                )
        result.append(
            {
                **source,
                "allowedKinds": allowed_kinds,
                "allowedStateRefs": allowed_state_refs,
            }
        )
    return result


def _obligations_from_plan(
    plan: ScenarioObligationPlan,
    *,
    sources: list[dict[str, Any]],
    available_states: list[dict[str, str]],
    operation_entries: list[dict[str, Any]],
) -> tuple[tuple[ScenarioObligation, ...] | None, list[str]]:
    source_contracts = _obligation_source_contracts(
        sources=sources,
        available_states=available_states,
        operation_entries=operation_entries,
    )
    source_contract_by_ref = {
        str(item["sourceRef"]): item for item in source_contracts
    }
    expected_refs = [str(item["sourceRef"]) for item in sources]
    decisions = {item.source_ref: item for item in plan.decisions}
    findings: list[str] = []
    if len(decisions) != len(plan.decisions):
        findings.append("sourceRef decisions must be unique")
    missing = set(expected_refs) - set(decisions)
    unknown = set(decisions) - set(expected_refs)
    if missing:
        findings.append("missing sourceRefs: " + ", ".join(sorted(missing)))
    if unknown:
        findings.append("unknown sourceRefs: " + ", ".join(sorted(unknown)))
    state_refs = {item["stateRef"] for item in available_states}
    state_owner_by_ref = {
        item["stateRef"]: item["executionOwner"] for item in available_states
    }
    entity_owners_by_source: dict[str, set[str]] = {}
    for entry in operation_entries:
        if str(entry.get("ownerRole") or "").casefold() != "entity":
            continue
        for step_ref in entry.get("stepRefs", []) or []:
            entity_owners_by_source.setdefault(str(step_ref), set()).add(
                str(entry["owner"])
            )
    entity_sources = {
        source_ref
        for source_ref, owners in entity_owners_by_source.items()
        if owners
    }
    obligations: list[ScenarioObligation] = []
    by_source = {str(item["sourceRef"]): item for item in sources}
    for index, source_ref in enumerate(expected_refs, start=1):
        decision = decisions.get(source_ref)
        if decision is None:
            continue
        source_contract = source_contract_by_ref[source_ref]
        allowed_kinds = set(source_contract["allowedKinds"])
        if decision.kind.value not in allowed_kinds:
            findings.append(
                f"{source_ref}: kind must copy allowedKinds: {decision.kind.value}"
            )
        if decision.kind in {
            ObligationKind.OBSERVATION,
            ObligationKind.STATE_TRANSITION,
        }:
            decision_state_refs = decision.state_refs
            invalid_state_refs = set(decision_state_refs) - state_refs
            if not decision_state_refs:
                findings.append(
                    f"{source_ref}: {decision.kind.value} requires stateRefs"
                )
            if len(set(decision_state_refs)) != len(decision_state_refs):
                findings.append(f"{source_ref}: stateRefs must be unique")
            if invalid_state_refs:
                findings.append(
                    f"{source_ref}: stateRefs must copy available Entity states: "
                    + ", ".join(sorted(invalid_state_refs))
                )
            disallowed_state_refs = set(decision_state_refs) - set(
                source_contract["allowedStateRefs"]
            )
            if disallowed_state_refs:
                findings.append(
                    f"{source_ref}: stateRefs must copy this source's "
                    "allowedStateRefs: "
                    + ", ".join(sorted(disallowed_state_refs))
                )
            if (
                decision.kind is ObligationKind.STATE_TRANSITION
                and source_ref not in entity_sources
            ):
                findings.append(
                    f"{source_ref}: STATE_TRANSITION has no Entity operation at "
                    "the same scenario source"
                )
            if decision.kind is ObligationKind.STATE_TRANSITION:
                unsupported_state_refs = {
                    state_ref
                    for state_ref in decision_state_refs
                    if state_ref in state_owner_by_ref
                    and state_owner_by_ref[state_ref]
                    not in entity_owners_by_source.get(source_ref, set())
                }
                if unsupported_state_refs:
                    findings.append(
                        f"{source_ref}: no same-source Entity operation owns: "
                        + ", ".join(sorted(unsupported_state_refs))
                    )
        elif decision.state_refs:
            findings.append(
                f"{source_ref}: {decision.kind.value} must use empty stateRefs"
            )
        source = by_source[source_ref]
        grounded_expected = str(
            source.get("sentence")
            or source.get("condition")
            or source_ref
        )
        grounded_condition = source.get("condition") or None
        if decision.kind in {
            ObligationKind.OBSERVATION,
            ObligationKind.STATE_TRANSITION,
        }:
            for state_index, state_ref in enumerate(
                decision.state_refs, start=1
            ):
                suffix = (
                    f".S{state_index}" if len(decision.state_refs) > 1 else ""
                )
                obligations.append(
                    ScenarioObligation(
                        obligationId=f"O{index}{suffix}",
                        sourceRefs=(source_ref,),
                        kind=decision.kind,
                        expected=(
                            f"{grounded_expected} Atomic state: {state_ref}."
                            if suffix
                            else grounded_expected
                        ),
                        stateRef=state_ref,
                        condition=grounded_condition,
                    )
                )
        else:
            obligations.append(
                ScenarioObligation(
                    obligationId=f"O{index}",
                    sourceRefs=(source_ref,),
                    kind=decision.kind,
                    expected=grounded_expected,
                    stateRef=None,
                    condition=grounded_condition,
                )
            )
    return (tuple(obligations) if not findings else None), findings


def _local_obligations(
    *,
    inputs: UseCaseInputs,
    fragment: ValidatedOperationFragment,
    inventory: Mapping[str, Any],
    run_dir: Path,
    budget: LogicalCallBudget,
) -> tuple[ScenarioObligation, ...]:
    sources = _scenario_source_descriptors(inputs)
    available_states = _available_state_refs(inventory)
    operation_entries = _fragment_operation_entries(fragment, inventory)
    source_contracts = _obligation_source_contracts(
        sources=sources,
        available_states=available_states,
        operation_entries=operation_entries,
    )
    input_payload = {
        "generationVersion": LOCAL_SEMANTICS_GENERATION_VERSION,
        "useCaseId": inputs.use_case.id,
        "sources": source_contracts,
        "entityOperations": [
            item
            for item in operation_entries
            if str(item.get("ownerRole") or "").casefold() == "entity"
        ],
    }
    input_digest = canonical_digest(input_payload)
    checkpoint = (
        run_dir
        / "validated"
        / "local-obligations"
        / f"{inputs.use_case.id}.json"
    )
    if checkpoint.exists():
        stored = _read_json(checkpoint)
        try:
            plan = ScenarioObligationPlan.model_validate(stored.get("plan"))
            obligations, stored_findings = _obligations_from_plan(
                plan,
                sources=sources,
                available_states=available_states,
                operation_entries=operation_entries,
            )
        except (AttributeError, ValueError):
            obligations = None
            stored_findings = ["stored obligation plan is invalid"]
        if (
            isinstance(stored, Mapping)
            and stored.get("inputDigest") == input_digest
            and obligations is not None
            and not stored_findings
        ):
            print(
                f'{{"event":"checkpoint.hit","stage":"localObligations",'
                f'"useCaseId":"{inputs.use_case.id}"}}',
                flush=True,
            )
            return obligations

    previous: dict[str, Any] | None = None
    findings: list[str] = []
    seen: set[str] = set()
    for attempt in range(2):
        messages = [
            {"role": "system", "content": LOCAL_OBLIGATIONS_PROMPT},
            {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False)},
        ]
        if previous is not None:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Repair only this obligation classification plan.",
                            "previousCandidate": previous,
                            "deterministicFindings": findings,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        parsed = _invoke(
            budget=budget,
            operation=(
                "ExecutableLocalObligations"
                if attempt == 0
                else "ExecutableLocalObligationsRepair"
            ),
            messages=messages,
            schema=ScenarioObligationPlan,
            use_case_id=inputs.use_case.id,
            max_tokens=3072,
            reasoning_effort="low",
        )
        plan = ScenarioObligationPlan.model_validate(parsed)
        previous = plan.model_dump(by_alias=True)
        digest = canonical_digest(previous)
        _write_immutable_json(
            run_dir
            / "attempts"
            / "local-obligations"
            / f"{inputs.use_case.id}-{input_digest[:12]}-{attempt + 1}.json",
            previous,
        )
        if digest in seen:
            findings = ["candidate repeats a rejected obligation plan"]
            continue
        seen.add(digest)
        obligations, findings = _obligations_from_plan(
            plan,
            sources=sources,
            available_states=available_states,
            operation_entries=operation_entries,
        )
        if obligations is not None:
            _write_json(
                checkpoint,
                {"inputDigest": input_digest, "plan": previous},
            )
            return obligations
    raise ExperimentFailure(
        f"{inputs.use_case.id} local obligation plan remained invalid: "
        + "; ".join(findings)
    )


def _assemble_local_semantic_contract(
    *,
    inputs: UseCaseInputs,
    obligations: tuple[ScenarioObligation, ...],
    operation_entries: list[dict[str, Any]],
    plan: EntityOperationSemanticPlan,
) -> LocalSemanticContract:
    decisions = {item.operation_ref: item for item in plan.operations}
    expanded_obligations = list(obligations)
    transition_bases = {
        source_ref: obligation
        for obligation in obligations
        if obligation.kind is ObligationKind.STATE_TRANSITION
        for source_ref in obligation.source_refs
    }
    existing_transition_states = {
        (source_ref, obligation.state_ref)
        for obligation in obligations
        if obligation.kind is ObligationKind.STATE_TRANSITION
        for source_ref in obligation.source_refs
    }
    atomic_states_by_source: dict[str, set[str]] = {}
    for entry in operation_entries:
        decision = decisions.get(str(entry["operationRef"]))
        if decision is None:
            continue
        for source_ref in entry.get("stepRefs", []) or []:
            source = str(source_ref)
            if source not in transition_bases:
                continue
            atomic_states_by_source.setdefault(source, set()).update(
                effect.state_ref for effect in decision.effects
            )
    for source_ref, state_refs in sorted(atomic_states_by_source.items()):
        base = transition_bases[source_ref]
        missing_states = sorted(
            state_ref
            for state_ref in state_refs
            if (source_ref, state_ref) not in existing_transition_states
        )
        for atomic_index, state_ref in enumerate(missing_states, start=1):
            expanded_obligations.append(
                ScenarioObligation(
                    obligationId=f"{base.obligation_id}.A{atomic_index}",
                    sourceRefs=(source_ref,),
                    kind=ObligationKind.STATE_TRANSITION,
                    expected=f"{base.expected} Atomic state change: {state_ref}.",
                    stateRef=state_ref,
                    condition=base.condition,
                )
            )
    obligations = tuple(expanded_obligations)
    obligation_ids_by_source: dict[str, list[str]] = {}
    for obligation in obligations:
        for source_ref in obligation.source_refs:
            obligation_ids_by_source.setdefault(source_ref, []).append(
                obligation.obligation_id
            )
    precondition_ids = tuple(
        obligation.obligation_id
        for obligation in obligations
        if any(":precondition:" in ref for ref in obligation.source_refs)
    )
    semantics: list[OperationSemantics] = []
    effect_number = 0
    first_coordinate = True
    for entry in operation_entries:
        operation_ref = str(entry["operationRef"])
        realizes = tuple(
            dict.fromkeys(
                obligation_id
                for ref in entry.get("stepRefs", []) or []
                if str(ref) in obligation_ids_by_source
                for obligation_id in obligation_ids_by_source[str(ref)]
            )
        )
        role = str(entry.get("ownerRole") or "").casefold()
        if role != "entity":
            if first_coordinate:
                realizes = tuple(dict.fromkeys((*realizes, *precondition_ids)))
                first_coordinate = False
            semantics.append(
                OperationSemantics(
                    operationRef=operation_ref,
                    responsibility=OperationResponsibility.COORDINATE,
                    realizes=realizes,
                    observes=(),
                    effects=(),
                    delegates=(),
                    outcomes=realizes,
                )
            )
            continue
        decision = decisions[operation_ref]
        step_refs = set(entry.get("stepRefs", []) or [])
        relevant_obligations = [
            obligation
            for obligation in obligations
            if set(obligation.source_refs) & step_refs
        ]
        owner = str(entry["owner"])
        owns_transition = any(
            obligation.kind is ObligationKind.STATE_TRANSITION
            and obligation.state_ref is not None
            and (
                obligation.state_ref == owner
                or obligation.state_ref.startswith(f"{owner}.")
            )
            for obligation in relevant_obligations
        )
        deterministic_observations = tuple(
            dict.fromkeys(
                obligation.state_ref
                for obligation in relevant_obligations
                if obligation.kind is ObligationKind.OBSERVATION
                and obligation.state_ref is not None
            )
        )
        if not owns_transition and not deterministic_observations:
            deterministic_observations = (owner,)
        effects: list[StateEffect] = []
        for effect in decision.effects:
            effect_number += 1
            effect_operation = StateEffectOperation(effect.operation)
            effects.append(
                StateEffect(
                    effectId=f"E{effect_number}",
                    stateRef=effect.state_ref,
                    operation=effect_operation,
                    executionOwner=str(entry["owner"]),
                    value=(
                        effect.value
                        if effect_operation is StateEffectOperation.SET
                        else None
                    ),
                    delta=(
                        effect.delta
                        if effect_operation
                        in {
                            StateEffectOperation.INCREMENT,
                            StateEffectOperation.DECREMENT,
                        }
                        else None
                    ),
                    condition=effect.condition,
                )
            )
        semantics.append(
            OperationSemantics(
                operationRef=operation_ref,
                responsibility=(
                    OperationResponsibility.MUTATE
                    if owns_transition
                    else OperationResponsibility.QUERY
                ),
                realizes=realizes,
                observes=(
                    () if owns_transition else deterministic_observations
                ),
                effects=tuple(effects),
                delegates=(),
                outcomes=realizes,
            )
        )
    return LocalSemanticContract(
        useCaseId=inputs.use_case.id,
        obligations=obligations,
        operations=tuple(semantics),
        assumptions=(
            "Boundary and Control operations coordinate without owning durable state.",
            f"Generation contract: {LOCAL_SEMANTICS_GENERATION_VERSION}",
        ),
    )


def _local_entity_semantics(
    *,
    inputs: UseCaseInputs,
    fragment: ValidatedOperationFragment,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    obligations: tuple[ScenarioObligation, ...],
    run_dir: Path,
    budget: LogicalCallBudget,
) -> ValidatedLocalSemanticContract:
    operation_entries = _fragment_operation_entries(fragment, inventory)
    entity_entries = [
        entry
        for entry in operation_entries
        if str(entry.get("ownerRole") or "").casefold() == "entity"
    ]
    available_states = _available_state_refs(inventory)
    states_by_owner: dict[str, list[str]] = {}
    for state in available_states:
        states_by_owner.setdefault(state["executionOwner"], []).append(
            state["stateRef"]
        )
    obligation_payload = [item.model_dump(by_alias=True) for item in obligations]

    def entity_operation_payload(entry: dict[str, Any]) -> dict[str, Any]:
        step_refs = set(entry.get("stepRefs", []) or [])
        relevant = [
            obligation
            for obligation in obligations
            if set(obligation.source_refs) & step_refs
        ]
        transition_states = sorted(
            {
                obligation.state_ref
                for obligation in relevant
                if obligation.kind is ObligationKind.STATE_TRANSITION
                and obligation.state_ref
            }
        )
        observation_states = sorted(
            {
                obligation.state_ref
                for obligation in relevant
                if obligation.kind is ObligationKind.OBSERVATION
                and obligation.state_ref
            }
        )
        owned_states = set(states_by_owner.get(str(entry["owner"]), []))
        owned_transition_states = set(transition_states) & owned_states
        return {
            **entry,
            "ownedStateRefs": sorted(owned_states),
            "requiredResponsibility": (
                OperationResponsibility.MUTATE.value
                if owned_transition_states
                else OperationResponsibility.QUERY.value
            ),
            "requiredTransitionStateRefs": sorted(owned_transition_states),
            "requiredObservationStateRefs": observation_states,
            "relevantObligations": [
                obligation
                for obligation in obligation_payload
                if set(obligation["sourceRefs"]) & step_refs
            ],
        }

    input_payload = {
        "generationVersion": LOCAL_SEMANTICS_GENERATION_VERSION,
        "useCaseId": inputs.use_case.id,
        "entityOperations": [
            entity_operation_payload(entry) for entry in entity_entries
        ],
    }
    input_digest = canonical_digest(input_payload)
    checkpoint = (
        run_dir
        / "validated"
        / "local-entity-semantics"
        / f"{inputs.use_case.id}.json"
    )

    def validate_plan(
        plan: EntityOperationSemanticPlan,
    ) -> tuple[ValidatedLocalSemanticContract | None, list[str]]:
        expected_refs = {str(item["operationRef"]) for item in entity_entries}
        decisions = {item.operation_ref: item for item in plan.operations}
        plan_findings: list[str] = []
        if len(decisions) != len(plan.operations):
            plan_findings.append("entity operation decisions must be unique")
        missing = expected_refs - set(decisions)
        unknown = set(decisions) - expected_refs
        if missing:
            plan_findings.append(
                "missing entityOperationRefs: " + ", ".join(sorted(missing))
            )
        if unknown:
            plan_findings.append(
                "unknown entityOperationRefs: " + ", ".join(sorted(unknown))
            )
        entries_by_ref = {
            str(item["operationRef"]): item for item in entity_entries
        }
        for operation_ref, decision in decisions.items():
            entry = entries_by_ref.get(operation_ref)
            if entry is None:
                continue
            owned_states = set(states_by_owner.get(str(entry["owner"]), []))
            step_refs = set(entry.get("stepRefs", []) or [])
            relevant_obligations = [
                obligation
                for obligation in obligations
                if set(obligation.source_refs) & step_refs
            ]
            required_transition_states = {
                obligation.state_ref
                for obligation in relevant_obligations
                if obligation.kind is ObligationKind.STATE_TRANSITION
                and obligation.state_ref
            }
            owned_required_transition_states = (
                required_transition_states & owned_states
            )
            required_responsibility = (
                OperationResponsibility.MUTATE
                if owned_required_transition_states
                else OperationResponsibility.QUERY
            )
            if required_responsibility is OperationResponsibility.QUERY:
                if decision.effects:
                    plan_findings.append(
                        f"{operation_ref}: QUERY requires no effects"
                    )
            else:
                if not decision.effects:
                    plan_findings.append(
                        f"{operation_ref}: MUTATE requires effects"
                    )
            wrong_effect_states = {
                effect.state_ref
                for effect in decision.effects
                if effect.state_ref not in owned_states
            }
            if wrong_effect_states:
                plan_findings.append(
                    f"{operation_ref}: effects target state not owned by "
                    f"{entry['owner']}: "
                    + ", ".join(sorted(wrong_effect_states))
                )
            actual_effect_states = {
                effect.state_ref for effect in decision.effects
            }
            missing_effect_states = (
                owned_required_transition_states - actual_effect_states
            )
            if missing_effect_states:
                plan_findings.append(
                    f"{operation_ref}: missing required transition effects: "
                    + ", ".join(sorted(missing_effect_states))
                )
            extra_effect_states = (
                actual_effect_states - owned_required_transition_states
            )
            if owned_required_transition_states and extra_effect_states:
                plan_findings.append(
                    f"{operation_ref}: unexpected effects beyond required states: "
                    + ", ".join(sorted(extra_effect_states))
                )
            grounding_text = " ".join(
                f"{obligation.expected} {obligation.condition or ''}"
                for obligation in relevant_obligations
            ).casefold()
            for effect in decision.effects:
                effect_operation = StateEffectOperation(effect.operation)
                if effect_operation is StateEffectOperation.SET:
                    if effect.value is None or not isinstance(
                        effect.value, (str, int, float, bool)
                    ):
                        plan_findings.append(
                            f"{operation_ref}#{effect.state_ref}: SET requires a "
                            "scalar scenario-grounded value; use UPDATE when the "
                            "runtime replacement is unknown"
                        )
                    elif str(effect.value).casefold() not in grounding_text:
                        plan_findings.append(
                            f"{operation_ref}#{effect.state_ref}: SET value "
                            f"{effect.value!r} is not present in the relevant "
                            "scenario obligations; use UPDATE instead of inventing "
                            "a placeholder or default"
                        )
                elif effect_operation in {
                    StateEffectOperation.INCREMENT,
                    StateEffectOperation.DECREMENT,
                } and (
                    not isinstance(effect.delta, (int, float))
                    or isinstance(effect.delta, bool)
                    or effect.delta <= 0
                ):
                    plan_findings.append(
                        f"{operation_ref}#{effect.state_ref}: "
                        f"{effect_operation.value} requires a positive numeric delta"
                    )
        if plan_findings:
            return None, plan_findings
        try:
            contract = _assemble_local_semantic_contract(
                inputs=inputs,
                obligations=obligations,
                operation_entries=operation_entries,
                plan=plan,
            )
            validated = validated_local_semantic_contract(
                contract,
                fragment=fragment,
                scenario=scenario,
                inventory=inventory,
            )
        except (ValueError, LocalSemanticValidationError) as error:
            if isinstance(error, LocalSemanticValidationError):
                return None, list(error.findings)
            return None, [str(error)]
        return validated, []

    if checkpoint.exists():
        stored = _read_json(checkpoint)
        try:
            stored_plan = EntityOperationSemanticPlan.model_validate(
                stored.get("plan")
            )
            validated, stored_findings = validate_plan(stored_plan)
        except (AttributeError, ValueError):
            validated = None
            stored_findings = ["stored entity semantic plan is invalid"]
        if (
            isinstance(stored, Mapping)
            and stored.get("inputDigest") == input_digest
            and validated is not None
            and not stored_findings
        ):
            print(
                f'{{"event":"checkpoint.hit","stage":"localEntitySemantics",'
                f'"useCaseId":"{inputs.use_case.id}"}}',
                flush=True,
            )
            return validated

    if not entity_entries:
        empty_plan = EntityOperationSemanticPlan(operations=[])
        validated, empty_findings = validate_plan(empty_plan)
        if validated is None:
            raise ExperimentFailure(
                f"{inputs.use_case.id} has no Entity operation for its local "
                "semantic contract: "
                + "; ".join(empty_findings)
            )
        return validated

    previous: dict[str, Any] | None = None
    findings: list[str] = []
    seen: set[str] = set()
    for attempt in range(3):
        messages = [
            {"role": "system", "content": LOCAL_ENTITY_SEMANTICS_PROMPT},
            {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False)},
        ]
        if previous is not None:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Repair only this Entity operation semantic plan.",
                            "previousCandidate": previous,
                            "deterministicFindings": findings,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        parsed = _invoke(
            budget=budget,
            operation=(
                "ExecutableLocalEntitySemantics"
                if attempt == 0
                else "ExecutableLocalEntitySemanticsRepair"
            ),
            messages=messages,
            schema=EntityOperationSemanticPlan,
            use_case_id=inputs.use_case.id,
            max_tokens=3072,
            reasoning_effort="low",
        )
        plan = EntityOperationSemanticPlan.model_validate(parsed)
        previous = plan.model_dump(by_alias=True)
        digest = canonical_digest(previous)
        _write_immutable_json(
            run_dir
            / "attempts"
            / "local-entity-semantics"
            / f"{inputs.use_case.id}-{input_digest[:12]}-{attempt + 1}.json",
            previous,
        )
        if digest in seen:
            findings = ["candidate repeats a rejected Entity semantic plan"]
            continue
        seen.add(digest)
        validated, findings = validate_plan(plan)
        if validated is not None:
            _write_json(
                checkpoint,
                {"inputDigest": input_digest, "plan": previous},
            )
            return validated
    raise ExperimentFailure(
        f"{inputs.use_case.id} local Entity semantic plan remained invalid: "
        + "; ".join(findings)
    )


def _local_semantic_contract(
    *,
    inputs: UseCaseInputs,
    fragment: ValidatedOperationFragment,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    run_dir: Path,
    budget: LogicalCallBudget,
) -> ValidatedLocalSemanticContract:
    checkpoint = (
        run_dir / "validated" / "local-semantics" / f"{inputs.use_case.id}.json"
    )
    if checkpoint.exists():
        try:
            stored = ValidatedLocalSemanticContract.model_validate(
                _read_json(checkpoint)
            )
            if (
                stored.provenance.validator_version
                != "executable-behavior.local-semantics.v3"
            ):
                raise ValueError("local semantic checkpoint predates split generation")
            if (
                f"Generation contract: {LOCAL_SEMANTICS_GENERATION_VERSION}"
                not in stored.contract.assumptions
            ):
                raise ValueError("local semantic checkpoint generation contract changed")
            revalidated = validated_local_semantic_contract(
                stored.contract,
                fragment=fragment,
                scenario=scenario,
                inventory=inventory,
            )
        except (ValueError, LocalSemanticValidationError):
            revalidated = None
        if revalidated is not None:
            _write_json(checkpoint, revalidated.model_dump(by_alias=True))
            print(
                f'{{"event":"checkpoint.revalidated","stage":"localSemantics",'
                f'"useCaseId":"{inputs.use_case.id}"}}',
                flush=True,
            )
            return revalidated

    obligations = _local_obligations(
        inputs=inputs,
        fragment=fragment,
        inventory=inventory,
        run_dir=run_dir,
        budget=budget,
    )
    result = _local_entity_semantics(
        inputs=inputs,
        fragment=fragment,
        scenario=scenario,
        inventory=inventory,
        obligations=obligations,
        run_dir=run_dir,
        budget=budget,
    )
    _write_json(checkpoint, result.model_dump(by_alias=True))
    return result


def _local_operation_seal(
    *,
    inputs: UseCaseInputs,
    fragment: ValidatedOperationFragment,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    run_dir: Path,
    budget: LogicalCallBudget,
) -> tuple[ValidatedOperationFragment, LocallySealedOperationFragment]:
    working_fragment = fragment
    checkpoint_key = f"local-behavior-{inputs.use_case.id}"
    for local_cycle in range(2):
        semantics = _local_semantic_contract(
            inputs=inputs,
            fragment=working_fragment,
            scenario=scenario,
            inventory=inventory,
            run_dir=run_dir,
            budget=budget,
        )
        subject = local_review_subject(working_fragment, semantics)
        evidence_index = local_review_evidence_index(working_fragment, semantics)
        raw_review = _semantic_review(
            stage=SemanticReviewStage.BEHAVIOR,
            subject=subject,
            allowed_owner_ids=(inputs.use_case.id,),
            run_dir=run_dir,
            budget=budget,
            evidence_index_override=evidence_index,
            checkpoint_key=checkpoint_key,
            category_override=BEHAVIOR_CATEGORIES
            - frozenset(
                {
                    ReviewCategory.SHARED_CONTRACT_REGRESSION,
                    ReviewCategory.ENTITY_STATE_OWNERSHIP,
                    ReviewCategory.MIXED_RESPONSIBILITY,
                    ReviewCategory.BCE_OWNER,
                    ReviewCategory.SCENARIO_PATH_OMISSION,
                    ReviewCategory.PARAMETER_SEMANTICS,
                }
            ),
        )
        review = _adjudicate_semantic_review(
            review=raw_review,
            subject=subject,
            allowed_owner_ids=(inputs.use_case.id,),
            run_dir=run_dir,
            budget=budget,
            evidence_index_override=evidence_index,
            checkpoint_key=checkpoint_key,
        )
        if review.unresolved_findings:
            raise ExperimentFailure(
                f"{inputs.use_case.id} local semantic review is unresolved: "
                + ", ".join(
                    semantic_finding_id(item) for item in review.unresolved_findings
                )
            )
        if not review.blocking_findings:
            seal = seal_local_operation_fragment(
                working_fragment, semantics, review
            )
            _write_json(
                run_dir
                / "validated"
                / "local-seals"
                / f"{inputs.use_case.id}.json",
                seal.model_dump(by_alias=True),
            )
            return working_fragment, seal
        if any(
            item.owner_stage is not ReviewOwnerStage.OPERATION_FRAGMENT
            for item in review.blocking_findings
        ):
            raise ExperimentFailure(
                f"{inputs.use_case.id} local review selected a non-local repair owner"
            )
        if local_cycle == 1:
            raise ExperimentFailure(
                f"{inputs.use_case.id} local semantic repair remained defective: "
                + ", ".join(
                    semantic_finding_id(item) for item in review.blocking_findings
                )
            )
        repaired = _repair_behavior_review(
            review=review,
            operations={inputs.use_case.id: working_fragment},
            inputs=(inputs,),
            scenario=scenario,
            inventory=inventory,
            run_dir=run_dir,
            budget=budget,
            parallelism=1,
        )[inputs.use_case.id]
        _record_patched_findings(
            run_dir=run_dir,
            checkpoint_key=checkpoint_key,
            review=review,
            candidate_digest=canonical_digest(repaired.payload),
        )
        working_fragment = repaired
    raise AssertionError("local semantic repair loop did not terminate")


_OPERATION_CONFLICT_PROMPT = """
Resolve exactly one shared-class operation signature collision. Return only one method
name, parameters, and returnType. Copy the supplied method name exactly. The signature
must express the same owner responsibility across every supplied use case and must use
only types available to every variant. Prefer a minimal built-in scalar contract over a
use-case-local DTO. Do not return stepRefs, operations other than this one, calls,
bindings, classes, or commentary.
""".strip()


def _operation_conflicts(
    operations: Mapping[str, ValidatedOperationFragment],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for use_case_id, fragment in operations.items():
        for class_set in fragment.payload.get("Classes", []) or []:
            if not isinstance(class_set, Mapping):
                continue
            owner = str(class_set.get("className") or "")
            for operation in class_set.get("operations", []) or []:
                if not isinstance(operation, Mapping):
                    continue
                name = str(operation.get("name") or "")
                grouped.setdefault(f"{owner}.{name}", []).append(
                    {"useCaseId": use_case_id, "operation": dict(operation)}
                )
    return {
        key: variants
        for key, variants in grouped.items()
        if len(variants) > 1
        and len(
            {
                canonical_digest(
                    {
                        "parameters": value["operation"].get("parameters") or [],
                        "returnType": value["operation"].get("returnType") or "void",
                    }
                )
                for value in variants
            }
        )
        > 1
    }


def _apply_operation_signature(
    key: str,
    signature: Mapping[str, Any],
    fragment: ValidatedOperationFragment,
    inputs: UseCaseInputs,
    *,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> ValidatedOperationFragment:
    owner, operation_name = key.rsplit(".", 1)
    payload = deepcopy(fragment.payload)
    changed = 0
    for class_set in payload.get("Classes", []) or []:
        if not isinstance(class_set, dict) or class_set.get("className") != owner:
            continue
        for operation in class_set.get("operations", []) or []:
            if isinstance(operation, dict) and operation.get("name") == operation_name:
                operation["parameters"] = deepcopy(signature.get("parameters") or [])
                operation["returnType"] = str(signature.get("returnType") or "")
                changed += 1
    if changed != 1 or str(signature.get("name") or "") != operation_name:
        raise OperationValidationError(
            [f"{key}: resolution must replace exactly one operation"]
        )
    context = OperationContext.from_payload(
        inputs.use_case.id,
        inventory,
        scenario=scenario,
        allowed_step_ids=inputs.allowed_steps,
        durable_entity_names=inputs.durable_entities,
        allowed_owner_names=inputs.allowed_owners,
    )
    return _validated_operation_fragment_for_inputs(
        payload,
        context=context,
        inputs=inputs,
        inventory=inventory,
    )


def _reconcile_operation_conflicts(
    *,
    operations: Mapping[str, ValidatedOperationFragment],
    inputs: Iterable[UseCaseInputs],
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    run_dir: Path,
    budget: LogicalCallBudget,
    parallelism: int,
) -> dict[str, ValidatedOperationFragment]:
    conflicts = _operation_conflicts(operations)
    if not conflicts:
        return dict(operations)
    inputs_by_id = {item.use_case.id: item for item in inputs}
    scenario_by_id = {
        str(item.get("id") or ""): item
        for item in scenario.get("useCases", []) or []
        if isinstance(item, Mapping)
    }

    def validate_signature(
        key: str,
        signature: Mapping[str, Any],
    ) -> list[str]:
        findings: list[str] = []
        expected_name = key.rsplit(".", 1)[1]
        if str(signature.get("name") or "") != expected_name:
            findings.append(f"method name must remain {expected_name}")
        for variant in conflicts[key]:
            use_case_id = str(variant["useCaseId"])
            try:
                _apply_operation_signature(
                    key,
                    signature,
                    operations[use_case_id],
                    inputs_by_id[use_case_id],
                    scenario=scenario,
                    inventory=inventory,
                )
            except OperationValidationError as error:
                findings.extend(
                    f"{use_case_id}: {finding}" for finding in error.findings
                )
        return findings

    def resolve(key: str) -> tuple[str, dict[str, Any]]:
        variants = conflicts[key]
        input_digest = canonical_digest(
            {"key": key, "variants": variants, "inventory": inventory}
        )
        checkpoint = (
            run_dir
            / "validated"
            / "operation-resolutions"
            / f"{key.replace('.', '-')}.json"
        )
        if checkpoint.exists():
            stored = _read_json(checkpoint)
            if (
                isinstance(stored, Mapping)
                and stored.get("inputDigest") == input_digest
                and isinstance(stored.get("signature"), Mapping)
                and not validate_signature(key, stored["signature"])
            ):
                print(
                    f'{{"event":"checkpoint.hit","stage":"operationResolution","key":"{key}"}}',
                    flush=True,
                )
                return key, dict(stored["signature"])
        use_case_ids = [str(value["useCaseId"]) for value in variants]
        base_messages = [
            {"role": "system", "content": _OPERATION_CONFLICT_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "collisionKey": key,
                        "useCases": [
                            scenario_by_id[use_case_id] for use_case_id in use_case_ids
                        ],
                        "variants": variants,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        previous: dict[str, Any] | None = None
        findings: list[str] = []
        seen: set[str] = set()
        for attempt in range(2):
            messages = list(base_messages)
            if previous is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "Repair only this shared operation signature.",
                                "previousCandidate": previous,
                                "findings": findings,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            parsed = _invoke(
                budget=budget,
                operation=(
                    "ExecutableOperationResolution"
                    if attempt == 0
                    else "ExecutableOperationResolutionRepair"
                ),
                messages=messages,
                schema=OperationSignatureResolution,
                use_case_id=key,
                max_tokens=2048,
                reasoning_effort="low",
            )
            signature = OperationSignatureResolution.model_validate(parsed).model_dump(
                by_alias=True
            )
            previous = signature
            _write_json(
                run_dir
                / "attempts"
                / "operation-resolutions"
                / f"{key.replace('.', '-')}-{attempt + 1}.json",
                signature,
            )
            digest = canonical_digest(signature)
            if digest in seen:
                raise ExperimentFailure(
                    f"{key} repeated the same rejected operation resolution"
                )
            seen.add(digest)
            findings = validate_signature(key, signature)
            if not findings:
                _write_json(
                    checkpoint,
                    {"inputDigest": input_digest, "signature": signature},
                )
                return key, signature
        raise ExperimentFailure(
            f"{key} operation resolution remained invalid: " + "; ".join(findings)
        )

    signatures: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(bind_context(resolve), key): key for key in conflicts
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                resolved_key, signature = future.result()
                signatures[resolved_key] = signature
            except Exception as error:  # noqa: BLE001 - aggregate resolution failures
                failures.append(f"{key}: {type(error).__name__}: {error}")
    if failures:
        raise ExperimentFailure(" | ".join(sorted(failures)))

    result = dict(operations)
    for key in sorted(signatures):
        for variant in conflicts[key]:
            use_case_id = str(variant["useCaseId"])
            result[use_case_id] = _apply_operation_signature(
                key,
                signatures[key],
                result[use_case_id],
                inputs_by_id[use_case_id],
                scenario=scenario,
                inventory=inventory,
            )
    for use_case_id, fragment in result.items():
        _write_json(
            run_dir / "validated" / "operations" / f"{use_case_id}.json",
            fragment.model_dump(by_alias=True),
        )
    return result


CALL_PROMPT = """
Choose a complete ordered call forest for exactly one use case. Return only the supplied
JSON schema. Select receiverOperationId only from receiverOperations; never invent an
operation, class, parameter, or type.

Emit exactly one Boundary root for each actor entry, in actorEntries order. Use its exact
groupKey. Every root must directly call a Control. A Boundary calls only Control; Control
calls Control or Entity; Entity calls only Entity. A parentCallId must name an earlier call
in the same group. Every emitted operation must own at least one required step in its group,
and the calls of each group must cover every requiredStepRef. callId values are stable unique
identifiers. guardRefs must use supplied step or branch IDs; use [] when no guard is needed.
Set exportResult true only when a later actor-entry group needs that completed result.
Emit every operation in requiredEffectOperationIds exactly once; step coverage by a
different operation does not execute a declared state effect.

Every call parameter must have a finite typed source. Boundary-root parameters and
their fields in structuredTypes are actor inputs. A non-void call result is available
only to a later call that is not its descendant: when a consumer needs a producer's
result, place producer and consumer as ordered siblings under an earlier Control. If a
required type is not an actor input, emit a compatible producer operation first.

The output list is a preorder tree, not a linear pipeline. Never attach a Control call below
an Entity call. When work returns to coordination after an Entity call, attach that later
Control to an earlier Control or the root, and use sibling branches for multiple Entity
calls. Emit no additional operation on the actor-entry Boundary; its root call represents
both actor input and output steps already present in that operation's stepRefs.
""".strip()


def _call_payload(
    inputs: UseCaseInputs,
    catalog: ValidatedCatalogDraft,
    *,
    required_effect_operation_refs: tuple[str, ...] = (),
) -> dict[str, Any]:
    operations = catalog_operations(catalog.payload)
    required = {ref for group in inputs.groups for ref in group.required_step_ids}
    receiver_operations = [
        value
        for _, value in sorted(operations.items())
        if required & set(value.get("stepRefs") or [])
    ]
    used_types = {
        str(parameter.get("type") or "")
        for operation in receiver_operations
        for parameter in operation.get("parameters", []) or []
        if isinstance(parameter, Mapping)
    }
    used_types.update(
        str(operation.get("returnType") or "")
        for operation in receiver_operations
    )
    return {
        "useCaseId": inputs.use_case.id,
        "actorEntries": [
            {
                "groupKey": item.id,
                "actor": item.entry_actor,
                "requiredStepRefs": list(item.required_step_ids),
            }
            for item in inputs.groups
        ],
        "steps": [
            {
                "id": step.id,
                "sentence": step.sentence,
                "branch": step.branch,
                "condition": step.condition,
            }
            for step in inputs.use_case.steps
        ],
        "requiredEffectOperationIds": list(required_effect_operation_refs),
        "structuredTypes": [
            item
            for item in catalog.payload.get("DataTypes", []) or []
            if isinstance(item, Mapping)
            and str(item.get("name") or "") in used_types
        ],
        "receiverOperations": receiver_operations,
    }


def _validate_required_call_operations(
    calls: ValidatedCallStructure,
    *,
    required_operation_refs: tuple[str, ...],
) -> None:
    called = {
        str(item.get("receiverOperationId") or "")
        for item in calls.payload.get("calls", []) or []
        if isinstance(item, Mapping)
    }
    missing = set(required_operation_refs) - called
    if missing:
        raise CallValidationError(
            "call structure omits required state-effect operations: "
            + ", ".join(sorted(missing))
        )


def _required_effect_operations(
    seal: LocallySealedOperationFragment,
) -> tuple[str, ...]:
    return tuple(
        semantic.operation_ref
        for semantic in seal.semantics.contract.operations
        if semantic.responsibility is OperationResponsibility.MUTATE
    )


def _call_input_sources(
    root: CallChoice,
    *,
    operations: Mapping[str, Mapping[str, Any]],
    data_types: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, str]]:
    operation = operations[root.receiver_operation_id]
    sources: list[tuple[str, str]] = []
    for parameter in operation.get("parameters", []) or []:
        if not isinstance(parameter, Mapping):
            continue
        name = str(parameter.get("name") or "")
        type_name = canonical_type(parameter.get("type"))
        sources.append((f"actor:{root.group_key}:{name}", type_name))
        data_type = data_types.get(type_name)
        if not isinstance(data_type, Mapping):
            continue
        for raw_field in data_type.get("fields", []) or []:
            field_name, field_type = _field_type(raw_field)
            if field_name and field_type:
                sources.append(
                    (
                        f"actor:{root.group_key}:{name}#{field_name}",
                        canonical_type(field_type),
                    )
                )
    return sources


def _parameters_have_distinct_sources(
    operation: Mapping[str, Any],
    available: list[tuple[str, str]],
) -> bool:
    return not _parameter_source_deficits(operation, available)


def _dependency_ordered_calls(
    calls: tuple[CallChoice, ...],
    *,
    operations: Mapping[str, Mapping[str, Any]],
    available: list[tuple[str, str]],
) -> tuple[CallChoice, ...] | None:
    """Find a finite producer-before-consumer order for one shallow Control tree."""

    def visit(
        remaining: tuple[CallChoice, ...],
        current_sources: list[tuple[str, str]],
    ) -> tuple[CallChoice, ...] | None:
        if not remaining:
            return ()
        for index, call in enumerate(remaining):
            operation = operations[call.receiver_operation_id]
            if not _parameters_have_distinct_sources(operation, current_sources):
                continue
            next_sources = list(current_sources)
            return_type = canonical_type(operation.get("returnType", "void"))
            if return_type != "void":
                next_sources.append((f"call:{call.call_id}.return", return_type))
            suffix = visit(
                (*remaining[:index], *remaining[index + 1 :]),
                next_sources,
            )
            if suffix is not None:
                return (call, *suffix)
        return None

    return visit(calls, list(available))


def _compile_call_plan(
    plan: CallChoicePlan,
    *,
    inputs: UseCaseInputs,
    catalog: ValidatedCatalogDraft,
) -> CallChoicePlan:
    """Compile LLM operation choices into a legal shallow BCE call forest."""
    operations = catalog_operations(catalog.payload)
    operation_aliases: dict[str, list[str]] = {}
    for operation_id in operations:
        operation_aliases.setdefault(operation_id.partition("(")[0], []).append(
            operation_id
        )
    normalized_calls: list[CallChoice] = []
    for call in plan.calls:
        receiver = call.receiver_operation_id
        candidates = operation_aliases.get(receiver, [])
        if receiver not in operations and len(candidates) == 1:
            call = call.model_copy(
                update={"receiver_operation_id": candidates[0]}
            )
        normalized_calls.append(call)
    plan = CallChoicePlan(calls=normalized_calls)
    data_types = {
        str(item.get("name") or ""): item
        for item in catalog.payload.get("DataTypes", []) or []
        if isinstance(item, Mapping)
    }
    unknown = sorted(
        {
            call.receiver_operation_id
            for call in plan.calls
            if call.receiver_operation_id not in operations
        }
    )
    if unknown:
        raise CallValidationError("call plan uses unknown operations: " + ", ".join(unknown))
    known_groups = {group.id for group in inputs.groups}
    unknown_groups = sorted(
        {call.group_key for call in plan.calls if call.group_key not in known_groups}
    )
    if unknown_groups:
        raise CallValidationError("call plan uses unknown groups: " + ", ".join(unknown_groups))

    compiled: list[CallChoice] = []
    exported_sources: list[tuple[str, str]] = []
    used_call_ids = {call.call_id for call in plan.calls}
    for group_index, group in enumerate(inputs.groups, start=1):
        group_calls = tuple(call for call in plan.calls if call.group_key == group.id)
        boundaries = tuple(
            call
            for call in group_calls
            if str(
                operations[call.receiver_operation_id].get("stereotype") or ""
            ).casefold()
            == "boundary"
        )
        if not boundaries:
            required = set(group.required_step_ids)
            boundary_options = [
                operation_id
                for operation_id, operation in operations.items()
                if str(operation.get("stereotype") or "").casefold()
                == "boundary"
                and required & set(operation.get("stepRefs") or [])
                and (
                    group.actor_step is None
                    or group.actor_step in set(operation.get("stepRefs") or [])
                )
            ]
            if len(boundary_options) == 1:
                auto_id = f"auto_root_{group_index}"
                while auto_id in used_call_ids:
                    auto_id += "_"
                used_call_ids.add(auto_id)
                auto_root = CallChoice(
                    callId=auto_id,
                    receiverOperationId=boundary_options[0],
                    parentCallId=None,
                    groupKey=group.id,
                    guardRefs=[],
                    exportResult=False,
                )
                group_calls = (auto_root, *group_calls)
                boundaries = (auto_root,)
        if len(boundaries) != 1:
            raise CallValidationError(
                f"{group.id} must select exactly one Boundary operation; selected "
                f"{len(boundaries)}"
            )
        root = boundaries[0].model_copy(
            update={"parent_call_id": None, "group_key": group.id}
        )
        non_roots = tuple(call for call in group_calls if call is not boundaries[0])
        controls = tuple(
            call
            for call in non_roots
            if str(
                operations[call.receiver_operation_id].get("stereotype") or ""
            ).casefold()
            == "control"
        )
        if not controls:
            raise CallValidationError(f"{group.id} requires at least one Control call")
        if any(
            str(operations[call.receiver_operation_id].get("stereotype") or "").casefold()
            == "boundary"
            for call in non_roots
        ):
            raise CallValidationError(
                f"{group.id} may not select an additional Boundary operation"
            )

        initial_sources = [
            *exported_sources,
            *_call_input_sources(
                root,
                operations=operations,
                data_types=data_types,
            ),
        ]
        chosen: tuple[CallChoice, tuple[CallChoice, ...]] | None = None
        for anchor_candidate in controls:
            anchor_operation = operations[anchor_candidate.receiver_operation_id]
            if not _parameters_have_distinct_sources(
                anchor_operation, initial_sources
            ):
                continue
            anchor = anchor_candidate.model_copy(
                update={"parent_call_id": root.call_id, "group_key": group.id}
            )
            remaining = tuple(
                call.model_copy(
                    update={"parent_call_id": anchor.call_id, "group_key": group.id}
                )
                for call in non_roots
                if call is not anchor_candidate
            )
            ordered = _dependency_ordered_calls(
                remaining,
                operations=operations,
                available=initial_sources,
            )
            if ordered is not None:
                chosen = anchor, ordered
                break
        if chosen is None:
            raise CallValidationError(
                f"{group.id} operation choices cannot be ordered into a sourceable "
                "Boundary-Control tree; add compatible producer operations"
            )
        anchor, ordered = chosen
        group_compiled = (root, anchor, *ordered)
        compiled.extend(group_compiled)
        for call in group_compiled:
            if not call.export_result:
                continue
            return_type = canonical_type(
                operations[call.receiver_operation_id].get("returnType", "void")
            )
            if return_type != "void":
                exported_sources.append((f"call:{call.call_id}.return", return_type))
    return CallChoicePlan(calls=compiled)


def _actor_entries_from_calls(
    inputs: UseCaseInputs,
    proposals: tuple[CallProposal, ...],
    catalog: ValidatedCatalogDraft,
) -> tuple[ActorEntry, ...]:
    roots = [item for item in proposals if item.parent_call_instance_key is None]
    if len(roots) != len(inputs.groups):
        raise CallValidationError("root calls must match actor entries")
    operations = catalog_operations(catalog.payload)
    return tuple(
        ActorEntry(
            group_key=group.id,
            actor=str(group.entry_actor or inputs.use_case.primary_actor),
            boundary_class=str(
                operations.get(root.operation_key, {}).get("className") or ""
            ),
            required_step_refs=group.required_step_ids,
        )
        for group, root in zip(inputs.groups, roots, strict=True)
    )


def _call_structure(
    *,
    inputs: UseCaseInputs,
    scenario: Mapping[str, Any],
    catalog: ValidatedCatalogDraft,
    run_dir: Path,
    budget: LogicalCallBudget,
    required_effect_operation_refs: tuple[str, ...] = (),
    extra_finding: str = "",
    force_repair: bool = False,
) -> tuple[ValidatedCallStructure, tuple[ActorEntry, ...]]:
    checkpoint = run_dir / "validated" / "calls" / f"{inputs.use_case.id}.json"
    if checkpoint.exists() and not force_repair:
        result = ValidatedCallStructure.model_validate(_read_json(checkpoint))
        entries = tuple(
            ActorEntry(
                str(item.get("groupKey") or ""),
                str(item.get("actor") or ""),
                str(item.get("boundaryClass") or ""),
                tuple(str(ref) for ref in item.get("requiredStepRefs") or []),
            )
            for item in result.payload.get("actorEntries", [])
            if isinstance(item, Mapping)
        )
        proposals = tuple(
            CallProposal(
                str(item.get("receiverOperationId") or ""),
                str(item.get("callId") or ""),
                (str(item.get("parentCallId")) if item.get("parentCallId") else None),
                str(item.get("groupKey") or ""),
                tuple(str(ref) for ref in item.get("guardRefs") or []),
                bool(item.get("exportResult") or False),
            )
            for item in result.payload.get("calls", [])
            if isinstance(item, Mapping)
        )
        try:
            revalidated = validated_call_structure(
                catalog,
                scenario=scenario,
                use_case_id=inputs.use_case.id,
                proposals=proposals,
                actor_entries=entries,
            )
            _validate_required_call_operations(
                revalidated,
                required_operation_refs=required_effect_operation_refs,
            )
            _validate_call_binding_feasibility(
                revalidated,
                scenario=scenario,
                catalog=catalog,
            )
        except CallValidationError:
            revalidated = None
        if revalidated is not None:
            _write_json(checkpoint, revalidated.model_dump(by_alias=True))
            print(
                f'{{"event":"checkpoint.revalidated","stage":"calls","useCaseId":"{inputs.use_case.id}"}}',
                flush=True,
            )
            return revalidated, entries

    payload = _call_payload(
        inputs,
        catalog,
        required_effect_operation_refs=required_effect_operation_refs,
    )
    base_messages = [
        {"role": "system", "content": CALL_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    previous: dict[str, Any] | None = None
    findings = [extra_finding] if extra_finding else []
    seen: set[str] = set()
    for attempt in range(2):
        messages = list(base_messages)
        if previous is not None or findings:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Repair only the call structure and return it in full.",
                            "previousCandidate": previous,
                            "findings": findings,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        parsed = _invoke(
            budget=budget,
            operation="ExecutableCalls" if not findings else "ExecutableCallsRepair",
            messages=messages,
            schema=CallChoicePlan,
            use_case_id=inputs.use_case.id,
            max_tokens=4096,
            reasoning_effort="low",
        )
        plan = CallChoicePlan.model_validate(parsed)
        previous = plan.model_dump(by_alias=True)
        _write_json(
            run_dir / "attempts" / "calls" / f"{inputs.use_case.id}-{attempt + 1}.json",
            previous,
        )
        digest = canonical_digest(previous)
        if digest in seen:
            raise ExperimentFailure(
                f"{inputs.use_case.id} repeated the same rejected call candidate"
            )
        seen.add(digest)
        try:
            compiled_plan = _compile_call_plan(
                plan,
                inputs=inputs,
                catalog=catalog,
            )
            proposals = tuple(
                CallProposal(
                    item.receiver_operation_id,
                    item.call_id,
                    item.parent_call_id,
                    item.group_key,
                    tuple(item.guard_refs),
                    item.export_result,
                )
                for item in compiled_plan.calls
            )
            entries = _actor_entries_from_calls(inputs, proposals, catalog)
            result = validated_call_structure(
                catalog,
                scenario=scenario,
                use_case_id=inputs.use_case.id,
                proposals=proposals,
                actor_entries=entries,
            )
            _validate_required_call_operations(
                result,
                required_operation_refs=required_effect_operation_refs,
            )
            _validate_call_binding_feasibility(
                result,
                scenario=scenario,
                catalog=catalog,
            )
        except CallValidationError as error:
            findings = [str(error)]
            continue
        _write_json(checkpoint, result.model_dump(by_alias=True))
        return result, entries
    raise ExperimentFailure(
        f"{inputs.use_case.id} call structure remained invalid: " + "; ".join(findings)
    )


def _field_type(raw: Any) -> tuple[str, str]:
    if isinstance(raw, Mapping):
        return (
            str(raw.get("name") or "").strip(),
            str(raw.get("type") or "").strip(),
        )
    name, separator, type_name = str(raw).partition(":")
    return (name.strip(), type_name.strip()) if separator else ("", "")


def _actor_sources(
    calls: ValidatedCallStructure,
    catalog: ValidatedCatalogDraft,
) -> tuple[Source, ...]:
    operations = catalog_operations(catalog.payload)
    types = {
        str(item.get("name") or ""): item
        for item in catalog.payload.get("DataTypes", []) or []
        if isinstance(item, Mapping)
    }
    sources: list[Source] = []
    for call in calls.payload.get("calls", []) or []:
        if not isinstance(call, Mapping) or call.get("parentCallId") is not None:
            continue
        operation = operations.get(str(call.get("receiverOperationId") or ""), {})
        group = str(call.get("groupKey") or "")
        for parameter in operation.get("parameters", []) or []:
            if not isinstance(parameter, Mapping):
                continue
            parameter_name = str(parameter.get("name") or "")
            type_name = str(parameter.get("type") or "")
            root_ref = f"actor:{group}:{parameter_name}"
            sources.append(Source(root_ref, type_name, scope=group))
            data_type = types.get(type_name)
            if not isinstance(data_type, Mapping):
                continue
            for raw_field in data_type.get("fields", []) or []:
                field_name, field_type = _field_type(raw_field)
                if field_name and field_type:
                    sources.append(
                        Source(
                            f"{root_ref}#{field_name}",
                            field_type,
                            kind="derived",
                            scope=group,
                        )
                    )
    return tuple(sources)


def _validate_call_binding_feasibility(
    calls: ValidatedCallStructure,
    *,
    scenario: Mapping[str, Any],
    catalog: ValidatedCatalogDraft,
) -> None:
    """Reject a call topology before checkpointing when an argument has no source."""
    try:
        targets = binding_candidate_sets(
            catalog,
            calls,
            scenario=scenario,
            sources=_actor_sources(calls, catalog),
        )
    except BindingSelectionError as error:
        raise CallValidationError(f"binding source graph is invalid: {error}") from error
    missing = [
        (
            f"{target.call_id}#{target.parameter_name}:"
            f"{target.parameter.get('type', '')}"
        )
        for target in targets
        if not target.candidates
    ]
    if missing:
        raise CallValidationError(
            "call parameters have no finite typed source: "
            + ", ".join(missing)
            + "; add an earlier compatible producer, or make existing producers "
            "and consumers ordered siblings under a Control because an ancestor's "
            "eventual return is unavailable to its descendant"
        )
    targets_by_call_type: dict[tuple[str, str], list[Any]] = {}
    for target in targets:
        key = (target.call_id, canonical_type(target.parameter.get("type")))
        targets_by_call_type.setdefault(key, []).append(target)
    insufficient = [
        f"{call_id}:{type_name} needs {len(group)} distinct sources but has "
        f"{len({candidate.source.source_ref for item in group for candidate in item.candidates})}"
        for (call_id, type_name), group in targets_by_call_type.items()
        if len(
            {
                candidate.source.source_ref
                for item in group
                for candidate in item.candidates
            }
        )
        < len(group)
    ]
    if insufficient:
        raise CallValidationError(
            "call parameters lack distinct finite sources: " + "; ".join(insufficient)
        )


BINDING_PROMPT = """
Select sources only for the supplied ambiguous argument targets. Return exactly one
selection for every target and no others. sourceRef must be copied from that target's
finiteCandidates. Prefer the source whose name and provenance match the parameter's
meaning; producerOperations maps each call-result source to the operation that produced
it. Distinct parameters of the same call must select distinct sourceRef values. Do not
invent conversions, values, calls, or new sources. Return JSON only.
""".strip()


def _binding_plan(
    *,
    inputs: UseCaseInputs,
    scenario: Mapping[str, Any],
    catalog: ValidatedCatalogDraft,
    calls: ValidatedCallStructure,
    run_dir: Path,
    budget: LogicalCallBudget,
) -> tuple[ValidatedBindingPlan, tuple[Source, ...]]:
    checkpoint = run_dir / "validated" / "bindings" / f"{inputs.use_case.id}.json"
    sources = _actor_sources(calls, catalog)
    if checkpoint.exists():
        result = ValidatedBindingPlan.model_validate(_read_json(checkpoint))
        cached_selections = {
            (str(item.get("callId") or ""), str(item.get("parameter") or "")): str(
                item.get("sourceRef") or ""
            )
            for item in result.payload.get("bindings", [])
            if isinstance(item, Mapping)
        }
        try:
            revalidated = validated_binding_plan(
                catalog,
                calls,
                scenario=scenario,
                sources=sources,
                selections=cached_selections,
            )
        except BindingSelectionError:
            revalidated = None
        if revalidated is not None:
            _write_json(checkpoint, revalidated.model_dump(by_alias=True))
            print(
                f'{{"event":"checkpoint.revalidated","stage":"bindings","useCaseId":"{inputs.use_case.id}"}}',
                flush=True,
            )
            return revalidated, sources

    targets = binding_candidate_sets(
        catalog,
        calls,
        scenario=scenario,
        sources=sources,
    )
    missing = [
        f"{item.call_id}#{item.parameter_name}"
        for item in targets
        if not item.candidates
    ]
    if missing:
        raise BindingSelectionError("no finite source for: " + ", ".join(missing))
    ambiguous = [item for item in targets if len(item.candidates) > 1]
    selections: dict[tuple[str, str], str] = {}
    if ambiguous:
        producer_operations = {
            str(call.get("callId") or ""):
            str(call.get("receiverOperationId") or "")
            for call in calls.payload.get("calls", []) or []
            if isinstance(call, Mapping)
        }
        prompt_payload = {
            "useCaseId": inputs.use_case.id,
            "producerOperations": producer_operations,
            "targets": [
                {
                    "callId": item.call_id,
                    "parameter": item.parameter_name,
                    "parameterType": item.parameter.get("type"),
                    "finiteCandidates": [
                        candidate.source.as_payload() for candidate in item.candidates
                    ],
                }
                for item in ambiguous
            ],
        }
        parsed = _invoke(
            budget=budget,
            operation="ExecutableBindings",
            messages=[
                {"role": "system", "content": BINDING_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(prompt_payload, ensure_ascii=False),
                },
            ],
            schema=BindingChoicePlan,
            use_case_id=inputs.use_case.id,
            max_tokens=4096,
            reasoning_effort="low",
        )
        _write_json(
            run_dir / "attempts" / "bindings" / f"{inputs.use_case.id}-1.json",
            parsed,
        )
        plan = BindingChoicePlan.model_validate(parsed)
        for item in plan.selections:
            key = (item.call_id, item.parameter)
            if key in selections:
                raise BindingSelectionError("LLM returned a duplicate binding target")
            selections[key] = item.source_ref
        expected = {(item.call_id, item.parameter_name) for item in ambiguous}
        if set(selections) != expected:
            raise BindingSelectionError(
                "LLM did not cover exactly the ambiguous targets"
            )
    result = validated_binding_plan(
        catalog,
        calls,
        scenario=scenario,
        sources=sources,
        selections=selections,
    )
    _write_json(checkpoint, result.model_dump(by_alias=True))
    return result, sources


def _parallel(
    inputs: Iterable[UseCaseInputs],
    operation: Callable[[UseCaseInputs], Any],
    *,
    parallelism: int,
) -> dict[str, Any]:
    values = tuple(inputs)
    results: dict[str, Any] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(bind_context(operation), item): item.use_case.id
            for item in values
        }
        for future in as_completed(futures):
            use_case_id = futures[future]
            try:
                results[use_case_id] = future.result()
            except Exception as error:  # noqa: BLE001 - aggregate every UC failure
                failures.append(f"{use_case_id}: {type(error).__name__}: {error}")
    if failures:
        raise ExperimentFailure(" | ".join(sorted(failures)))
    return results


_SEMANTIC_REVIEW_PROMPT = """
Review the supplied design for semantic defects only. Return findings, never a patch,
replacement model, or commentary. For every finding, copy ruleId from allowedRules for
its category. Set predicateKey to a concise stable UPPER_SNAKE_CASE description of the
violated predicate. Copy ownerIds, obligationIds, targetRefs, and evidence from the
supplied finite values. Every target/evidence item is a typed {kind, ref} pair and must
be copied exactly from evidenceIndex. targetRefs name the concrete item that would be
repaired; evidence contains the facts supporting the claim. expected and observed
must state a falsifiable predicate, not general design advice.

ownerIds are repair-owner IDs, not class/type/relationship references. Copy them only
from the list under allowedOwners for the selected ownerStage. A class:* value appearing
in targetRefs or evidence is not thereby an allowed owner. When an inventory duplicate
crosses owner stages, select one sufficient repair owner: prefer the inventoryFragment
that can canonicalize its unique item to the established shared concept. Keep both
duplicate items in targetRefs/evidence, but do not name an owner from the other stage.

Do not report JSON/schema shape, duplicate IDs, type closure, reference coverage, call
ordering, BCE call direction, binding availability, or provenance. Deterministic
validators own those checks. Use HIGH only when the current design would express the
wrong domain meaning or omit required behavior. Use MEDIUM or LOW for non-blocking
quality observations. Set decision to REVISE if and only if at least one finding is
HIGH. Return at most 12 findings. Repetition of the same canonical class/type/reference
across fragment provenance is intentional merge input, not a semantic duplicate; a
duplicate finding must cite two distinct canonical refs. If an inventory defect belongs
to a merged shared item produced by a class/type/relationship collision, target its
inventoryResolution owner instead of an individual inventoryFragment. Use
subject.ownerScopes to select the owner that actually declares the defective class,
type, relationship, or operation; do not infer ownership from a related scenario step
alone.

For behavior review, examine all operations in the cited operationFragment before
claiming that a scenario path or Entity state transition is missing. A Control may
coordinate a transition while the durable Entity owns the state-changing operation.
When that Entity operation covers the required stepRef, do not report the transition
as absent or as Control-owned merely because the Control operation coordinates it.
Boundary and Control operations are correctly classified COORDINATE even when their
names contain request, query, retrieve, create, update, or delete; only Entity-owned
operations may directly claim durable observations or effects. COORDINATE may return
a result DTO. Entity QUERY is the correct owner of Entity state observation. A
precondition does not require a standalone operation unless an explicit scenario step
performs that action. Never report ENTITY_STATE_OWNERSHIP when the supplied contract
already passed deterministic effect-owner validation. Treat realization as contribution,
not direct state ownership: only observes/effects prove direct state access. A
Boundary-to-Control coordination handoff is not a semantic duplicate merely because
both operations contribute to the same use-case intent. More generally, operations
from different BCE roles are layering, not semantic duplicates, even when they share
an intent or return type. Within one local use-case slice,
SEMANTIC_DUPLICATE_OPERATION must cite at least two operations on the same class owner
whose responsibilities and contracts are substitutable. Operations on different
Entities act on different owned state and are not substitutes. Copy each operation
owner's role from subject.ownerRoles; never infer it from a method name.
SHARED_CONTRACT_REGRESSION applies only when the same exact canonical operation ref is
declared by multiple operationFragment owners and their integrated contract regressed.
Different operation names, DTOs, or use-case responsibilities are not a shared-contract
regression merely because they belong to the same Boundary class.
""".strip()


_SEMANTIC_ADJUDICATION_PROMPT = """
Independently adjudicate every reported HIGH semantic finding against the complete
subject and the exact typed evidence index. Return one result for every supplied
findingId and no others. CONFIRMED means the cited evidence proves the precise
predicate. REFUTED means the complete subject contradicts it. UNRESOLVED means the
available contract lacks enough information to decide. Copy evidence entries exactly
from evidenceIndex and explain the decisive fact briefly.

Do not infer execution, state ownership, delegation, or a missing transition from an
operation name or a scenario step alone. Inspect every relevant operation and explicit
effect/obligation contract in the subject. A reviewer mistake is REFUTED, not a design
defect. Insufficient evidence is UNRESOLVED, not CONFIRMED.
Apply subject.responsibilityPolicy, subject.preconditionPolicy, and
subject.coordinationHandoffPolicy as decisive contract rules whenever they are present.
Apply subject.inventoryRolePolicy when present. Use-case-specific Boundary or Control
classes with different stated responsibilities are not semantic duplicates merely
because their names share a domain word; report possible consolidation only as
non-blocking OVER_FRAGMENTED_ROLE.
For SEMANTIC_DUPLICATE_OPERATION, operations in different subject.ownerRoles are
decisively REFUTED because they represent a BCE handoff rather than substitutes.
In a local use-case review, operations on different class owners are also decisively
REFUTED; different Entities own different state even when both are QUERY operations.
For SHARED_CONTRACT_REGRESSION, distinct canonical operation refs are decisively
REFUTED; only the same exact operation ref shared by multiple fragments is comparable.
""".strip()


def _review_allowed_owner_groups(
    stage: SemanticReviewStage,
    allowed_owner_ids: tuple[str, ...],
) -> dict[str, list[str]]:
    if stage is SemanticReviewStage.INVENTORY:
        return {
            "inventoryFragment": [
                value for value in allowed_owner_ids if ":" not in value
            ],
            "inventoryResolution": [
                value for value in allowed_owner_ids if ":" in value
            ],
        }
    return {"operationFragment": list(allowed_owner_ids)}


def _validate_review_owner_contract(
    proposal: SemanticReviewProposal,
    *,
    allowed_owner_groups: Mapping[str, list[str]],
    evidence_index: tuple[ReviewEvidenceRecord, ...],
) -> None:
    """Bind each finding to an actionable owner in exactly one repair stage."""
    allowed_by_owner_stage = {
        ReviewOwnerStage.INVENTORY_FRAGMENT: set(
            allowed_owner_groups.get("inventoryFragment", [])
        ),
        ReviewOwnerStage.INVENTORY_RESOLUTION: set(
            allowed_owner_groups.get("inventoryResolution", [])
        ),
        ReviewOwnerStage.OPERATION_FRAGMENT: set(
            allowed_owner_groups.get("operationFragment", [])
        ),
        ReviewOwnerStage.INTEGRATION_RESOLUTION: set(
            allowed_owner_groups.get("integrationResolution", [])
        ),
    }
    evidence_owners = {
        (record.kind, record.ref): set(record.owner_ids) for record in evidence_index
    }
    fragment_owners = allowed_by_owner_stage[ReviewOwnerStage.INVENTORY_FRAGMENT]
    resolution_owners = allowed_by_owner_stage[
        ReviewOwnerStage.INVENTORY_RESOLUTION
    ]
    for finding in proposal.findings:
        wrong_stage_owners = set(finding.owner_ids) - allowed_by_owner_stage[
            finding.owner_stage
        ]
        if wrong_stage_owners:
            raise ValueError(
                f"{finding.owner_stage.value} review names owners from a different "
                "stage: "
                + ", ".join(sorted(wrong_stage_owners))
            )
        target_resolution_owners = {
            owner
            for target in finding.target_refs
            for owner in evidence_owners.get((target.kind, target.ref), set())
            if owner in resolution_owners
        }
        if (
            finding.category is not ReviewCategory.SEMANTIC_DUPLICATE
            and target_resolution_owners
            and (
                finding.owner_stage is not ReviewOwnerStage.INVENTORY_RESOLUTION
                or not set(finding.owner_ids).issubset(
                    target_resolution_owners
                )
            )
        ):
            raise ValueError(
                "a defect in a resolved inventory item must target its "
                "inventoryResolution owner: "
                + ", ".join(sorted(target_resolution_owners))
            )
        if finding.category is not ReviewCategory.SEMANTIC_DUPLICATE:
            continue
        target_owner_sets = [
            evidence_owners.get((target.kind, target.ref), set())
            for target in finding.target_refs
        ]
        has_resolved_shared_target = any(
            owners & resolution_owners for owners in target_owner_sets
        )
        canonicalizable_fragment_owners = {
            owner
            for owners in target_owner_sets
            if not owners & resolution_owners
            for owner in owners & fragment_owners
        }
        if (
            has_resolved_shared_target
            and canonicalizable_fragment_owners
            and (
                finding.owner_stage is not ReviewOwnerStage.INVENTORY_FRAGMENT
                or not set(finding.owner_ids).issubset(
                    canonicalizable_fragment_owners
                )
            )
        ):
            raise ValueError(
                "cross-stage semantic duplicate must target only its canonicalizable "
                "inventoryFragment owners: "
                + ", ".join(sorted(canonicalizable_fragment_owners))
            )


def _normalize_review_owner_routing(
    proposal: SemanticReviewProposal,
    *,
    allowed_owner_groups: Mapping[str, list[str]],
    evidence_index: tuple[ReviewEvidenceRecord, ...],
) -> dict[str, Any]:
    """Route resolved inventory targets without asking the reviewer to decide it."""
    payload = proposal.model_dump(by_alias=True)
    resolution_owners = set(allowed_owner_groups.get("inventoryResolution", []))
    evidence_owners = {
        (record.kind, record.ref): set(record.owner_ids) for record in evidence_index
    }
    for finding, finding_payload in zip(
        proposal.findings, payload.get("findings", []), strict=True
    ):
        if finding.category is ReviewCategory.SEMANTIC_DUPLICATE:
            continue
        target_resolution_owners = {
            owner
            for target in finding.target_refs
            for owner in evidence_owners.get((target.kind, target.ref), set())
            if owner in resolution_owners
        }
        if target_resolution_owners:
            finding_payload["ownerStage"] = (
                ReviewOwnerStage.INVENTORY_RESOLUTION.value
            )
            finding_payload["ownerIds"] = sorted(target_resolution_owners)
    return payload


def _validate_local_review_contract(
    proposal: SemanticReviewProposal,
    *,
    subject: Mapping[str, Any],
) -> None:
    """Reject local-review claims that contradict deterministic BCE layering."""
    if not str(subject.get("reviewRubricVersion") or "").startswith(
        "local-behavior-"
    ):
        return
    raw_owner_roles = subject.get("ownerRoles")
    if not isinstance(raw_owner_roles, Mapping):
        raise TypeError("local review subject requires ownerRoles")
    owner_roles = {
        str(owner): str(role) for owner, role in raw_owner_roles.items()
    }
    for finding in proposal.findings:
        if finding.category is not ReviewCategory.SEMANTIC_DUPLICATE_OPERATION:
            continue
        operation_refs = tuple(
            target.ref
            for target in finding.target_refs
            if target.kind is ReviewEvidenceKind.OPERATION
        )
        if len(set(operation_refs)) < 2:
            raise ValueError(
                "local semantic duplicate must cite at least two distinct operations"
            )
        missing_owners = sorted(
            {
                operation_ref.partition("::")[0]
                for operation_ref in operation_refs
                if operation_ref.partition("::")[0] not in owner_roles
            }
        )
        if missing_owners:
            raise ValueError(
                "local semantic duplicate names operation owners without BCE roles: "
                + ", ".join(missing_owners)
            )
        roles = {
            owner_roles[operation_ref.partition("::")[0]]
            for operation_ref in operation_refs
        }
        if len(roles) != 1:
            raise ValueError(
                "local semantic duplicate must compare operations in the same BCE "
                "role; cross-role operations are a coordination handoff"
            )
        operation_owners = {
            operation_ref.partition("::")[0] for operation_ref in operation_refs
        }
        if len(operation_owners) != 1:
            raise ValueError(
                "local semantic duplicate must compare operations on the same class "
                "owner; different owners have distinct responsibilities or state"
            )


def _validate_inventory_review_contract(
    proposal: SemanticReviewProposal,
    *,
    subject: Mapping[str, Any],
) -> None:
    """Keep use-case-specific BCE role overlap out of blocking duplicate claims."""
    inventory = subject.get("inventory")
    if not isinstance(inventory, Mapping):
        return
    roles = {
        f"class:{item.get('className')}": str(item.get("stereotype") or "")
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
    }
    for finding in proposal.findings:
        if finding.category is not ReviewCategory.SEMANTIC_DUPLICATE:
            continue
        class_targets = {
            target.ref
            for target in finding.target_refs
            if target.kind is ReviewEvidenceKind.CLASS
        }
        target_roles = {roles.get(reference, "") for reference in class_targets}
        if class_targets and target_roles <= {"Boundary", "Control"}:
            raise ValueError(
                "Boundary/Control overlap belongs to non-blocking "
                "OVER_FRAGMENTED_ROLE, not SEMANTIC_DUPLICATE"
            )


def _validate_behavior_review_contract(
    proposal: SemanticReviewProposal,
    *,
    subject: Mapping[str, Any],
) -> None:
    """Keep global review limited to a genuinely shared canonical contract."""
    owner_scopes = subject.get("ownerScopes")
    if not isinstance(owner_scopes, Mapping):
        return
    operation_owners: dict[str, set[str]] = {}
    for scoped_owner, references in owner_scopes.items():
        owner_stage, separator, owner_id = str(scoped_owner).partition(":")
        if separator and owner_stage == "operationFragment" and isinstance(
            references, list
        ):
            for reference in references:
                operation_owners.setdefault(str(reference), set()).add(owner_id)
    for finding in proposal.findings:
        if finding.category is not ReviewCategory.SHARED_CONTRACT_REGRESSION:
            continue
        operation_targets = {
            target.ref
            for target in finding.target_refs
            if target.kind is ReviewEvidenceKind.OPERATION
        }
        if len(operation_targets) != 1:
            raise ValueError(
                "shared contract regression must target one exact canonical "
                "operation ref, not distinct operations"
            )
        operation_ref = next(iter(operation_targets))
        if len(operation_owners.get(operation_ref, set())) < 2:
            raise ValueError(
                "shared contract regression target is not declared by multiple "
                f"operationFragment owners: {operation_ref}"
            )


def _review_evidence_kind(reference: str) -> ReviewEvidenceKind:
    if reference.startswith("class:"):
        return ReviewEvidenceKind.CLASS
    if reference.startswith("type:"):
        return ReviewEvidenceKind.DATA_TYPE
    if reference.startswith("relationship:"):
        return ReviewEvidenceKind.RELATIONSHIP
    if reference.startswith("obligation:"):
        return ReviewEvidenceKind.OBLIGATION
    if reference.startswith("effect:"):
        return ReviewEvidenceKind.STATE_EFFECT
    if "::" in reference:
        return ReviewEvidenceKind.OPERATION
    return ReviewEvidenceKind.OUTCOME


def _review_evidence_index(
    subject: Mapping[str, Any],
    *,
    allowed_owner_ids: tuple[str, ...],
) -> tuple[ReviewEvidenceRecord, ...]:
    """Build the only evidence namespace a semantic reviewer may cite."""
    allowed = set(allowed_owner_ids)
    owners_by_ref: dict[tuple[ReviewEvidenceKind, str], set[str]] = {}

    def add(kind: ReviewEvidenceKind, reference: str, owners: Iterable[str]) -> None:
        normalized = reference.strip()
        record_owners = {owner for owner in owners if owner in allowed}
        if not normalized or not record_owners:
            return
        owners_by_ref.setdefault((kind, normalized), set()).update(record_owners)

    owner_scopes = subject.get("ownerScopes") or {}
    if isinstance(owner_scopes, Mapping):
        for scoped_owner, references in owner_scopes.items():
            _, separator, owner_id = str(scoped_owner).partition(":")
            if not separator or owner_id not in allowed:
                continue
            if not isinstance(references, Iterable) or isinstance(
                references, (str, bytes, Mapping)
            ):
                continue
            for value in references:
                reference = str(value).strip()
                add(_review_evidence_kind(reference), reference, (owner_id,))

    scenario = subject.get("scenario") or {}
    if isinstance(scenario, Mapping):
        use_cases = scenario.get("useCases") or scenario.get("use_cases") or ()
        if isinstance(use_cases, Iterable) and not isinstance(
            use_cases, (str, bytes, Mapping)
        ):
            for use_case in use_cases:
                if not isinstance(use_case, Mapping):
                    continue
                owner_id = str(use_case.get("id") or use_case.get("useCaseId") or "")
                for step in use_case.get("steps") or ():
                    if isinstance(step, Mapping):
                        reference = str(step.get("id") or step.get("stepId") or "")
                        add(ReviewEvidenceKind.STEP, reference, (owner_id,))
                for key in (
                    "preconditions",
                    "successGuarantees",
                    "minimalGuarantees",
                    "outcomes",
                ):
                    for value in use_case.get(key) or ():
                        reference = (
                            str(value.get("id") or value.get("ref") or "")
                            if isinstance(value, Mapping)
                            else str(value)
                        )
                        add(ReviewEvidenceKind.OUTCOME, reference, (owner_id,))

    for contract in subject.get("localContracts") or ():
        if not isinstance(contract, Mapping):
            continue
        owner_id = str(contract.get("useCaseId") or "")
        for obligation in contract.get("obligations") or ():
            if isinstance(obligation, Mapping):
                reference = str(
                    obligation.get("obligationId") or obligation.get("id") or ""
                )
                add(ReviewEvidenceKind.OBLIGATION, reference, (owner_id,))
        for operation in contract.get("operations") or ():
            if not isinstance(operation, Mapping):
                continue
            for effect in operation.get("effects") or ():
                if isinstance(effect, Mapping):
                    reference = str(effect.get("effectId") or effect.get("id") or "")
                    add(ReviewEvidenceKind.STATE_EFFECT, reference, (owner_id,))

    return tuple(
        ReviewEvidenceRecord(kind=kind, ref=reference, ownerIds=tuple(sorted(owners)))
        for (kind, reference), owners in sorted(
            owners_by_ref.items(), key=lambda item: (item[0][0].value, item[0][1])
        )
    )


def _behavior_review_subject(
    *,
    scenario: Mapping[str, Any],
    catalog: ValidatedCatalogDraft,
    operations: Mapping[str, ValidatedOperationFragment],
    seals: Mapping[str, LocallySealedOperationFragment] | None = None,
) -> dict[str, Any]:
    owner_scopes: dict[str, list[str]] = {}
    for use_case_id, fragment in sorted(operations.items()):
        references: list[str] = []
        for class_set in fragment.payload.get("Classes", []) or []:
            if not isinstance(class_set, Mapping):
                continue
            owner = str(class_set.get("className") or class_set.get("name") or "")
            for operation in class_set.get("operations", []) or []:
                if not isinstance(operation, Mapping):
                    continue
                parameters = ",".join(
                    f"{parameter.get('name')}:{parameter.get('type')}"
                    for parameter in operation.get("parameters", []) or []
                    if isinstance(parameter, Mapping)
                )
                references.append(
                    f"{owner}::{operation.get('name')}({parameters})"
                )
        owner_scopes[f"operationFragment:{use_case_id}"] = sorted(references)
    return {
        "reviewRubricVersion": "behavior-v3-shared-integration",
        "scenario": scenario,
        "catalog": catalog.payload,
        "ownerScopes": owner_scopes,
        "localContracts": [
            seal.semantics.contract.model_dump(by_alias=True)
            for _, seal in sorted((seals or {}).items())
        ],
        "localSealDigests": {
            use_case_id: seal.provenance.digest
            for use_case_id, seal in sorted((seals or {}).items())
        },
    }


def _semantic_review(
    *,
    stage: SemanticReviewStage,
    subject: Mapping[str, Any],
    allowed_owner_ids: tuple[str, ...],
    run_dir: Path,
    budget: LogicalCallBudget,
    evidence_index_override: tuple[ReviewEvidenceRecord, ...] | None = None,
    checkpoint_key: str | None = None,
    category_override: frozenset[Any] | None = None,
) -> ValidatedSemanticReview:
    evidence_index = (
        evidence_index_override
        if evidence_index_override is not None
        else _review_evidence_index(subject, allowed_owner_ids=allowed_owner_ids)
    )
    allowed_owner_groups = _review_allowed_owner_groups(stage, allowed_owner_ids)
    checkpoint_name = checkpoint_key or stage.value.lower()
    checkpoint = run_dir / "validated" / "reviews" / f"{checkpoint_name}.json"
    if checkpoint.exists():
        try:
            stored = ValidatedSemanticReview.model_validate(_read_json(checkpoint))
            if (
                stored.subject_digest != canonical_digest(subject)
                or stored.provenance.validator_version != REVIEW_VALIDATOR_VERSION
                or stored.provenance.input_digests.get("allowedOwnerIds")
                != canonical_digest(allowed_owner_ids)
            ):
                raise ValueError("semantic review checkpoint inputs changed")
            revalidated = validated_semantic_review(
                stored.review,
                stage=stage,
                subject=subject,
                allowed_owner_ids=allowed_owner_ids,
                evidence_index=evidence_index,
            )
            _validate_review_owner_contract(
                stored.review,
                allowed_owner_groups=allowed_owner_groups,
                evidence_index=evidence_index,
            )
            _validate_local_review_contract(stored.review, subject=subject)
            _validate_inventory_review_contract(stored.review, subject=subject)
            _validate_behavior_review_contract(stored.review, subject=subject)
            if category_override is not None and any(
                item.category not in category_override
                for item in revalidated.review.findings
            ):
                raise ValueError("semantic review checkpoint uses a disallowed category")
        except ValueError:
            revalidated = None
        if revalidated is not None:
            _write_json(checkpoint, revalidated.model_dump(by_alias=True))
            print(
                json.dumps(
                    {
                        "event": "checkpoint.revalidated",
                        "stage": f"{stage.value.lower()}Review",
                    }
                ),
                flush=True,
            )
            return revalidated

    categories = category_override or (
        INVENTORY_CATEGORIES
        if stage is SemanticReviewStage.INVENTORY
        else BEHAVIOR_CATEGORIES
    )
    if categories == frozenset({ReviewCategory.SHARED_CONTRACT_REGRESSION}):
        criteria = [
            "cross-fragment shared operation contract regression",
            "a global merge contradicts a locally sealed obligation or effect",
            "integration loses or changes a locally sealed operation responsibility",
        ]
    else:
        criteria = (
            [
                description
                for category, description in (
                    (
                        ReviewCategory.SEMANTIC_DUPLICATE,
                        "same concept represented by different names",
                    ),
                    (
                        ReviewCategory.OVER_FRAGMENTED_ROLE,
                        "unnecessary per-use-case Boundary or Control fragmentation",
                    ),
                    (
                        ReviewCategory.ENTITY_LIFECYCLE,
                        "Entity lifetime and durability fitness",
                    ),
                    (
                        ReviewCategory.ENTITY_STRUCTURE,
                        "Entity fields and identifier coherence",
                    ),
                    (
                        ReviewCategory.RELATIONSHIP_SEMANTICS,
                        "relationship meaning and multiplicity",
                    ),
                    (
                        ReviewCategory.MISSED_SHARED_CONCEPT,
                        "a reusable concept missed across use cases",
                    ),
                )
                if category in categories
            ]
            if stage is SemanticReviewStage.INVENTORY
            else [
                description
                for category, description in (
                    (
                        ReviewCategory.SCENARIO_INTENT,
                        "operation contracts match scenario intent",
                    ),
                    (
                        ReviewCategory.BCE_OWNER,
                        "operation is owned by the right BCE class",
                    ),
                    (
                        ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
                        "same operation meaning hidden under different names",
                    ),
                    (
                        ReviewCategory.MIXED_RESPONSIBILITY,
                        "same operation name mixes responsibilities",
                    ),
                    (
                        ReviewCategory.PARAMETER_SEMANTICS,
                        "parameter meaning matches the consumed domain value",
                    ),
                    (
                        ReviewCategory.SCENARIO_PATH_OMISSION,
                        "success, business failure, or extension behavior was omitted",
                    ),
                    (
                        ReviewCategory.ENTITY_STATE_OWNERSHIP,
                        "Entity state change is owned by the Entity rather than a Control",
                    ),
                )
                if category in categories
            ]
        )
    payload = {
        "reviewStage": stage.value,
        "allowedOwners": allowed_owner_groups,
        "allowedCategories": sorted(item.value for item in categories),
        "allowedRules": {
            item.value: f"SEMANTIC.{item.value}" for item in sorted(categories)
        },
        "criteria": criteria,
        "evidenceIndex": [
            item.model_dump(by_alias=True) for item in evidence_index
        ],
        "subject": subject,
    }
    proposal_type = (
        InventorySemanticReviewProposal
        if stage is SemanticReviewStage.INVENTORY
        else BehaviorSemanticReviewProposal
    )

    def validate_candidate(
        candidate: SemanticReviewProposal,
    ) -> ValidatedSemanticReview:
        normalized = proposal_type.model_validate(
            _normalize_review_owner_routing(
                candidate,
                allowed_owner_groups=allowed_owner_groups,
                evidence_index=evidence_index,
            )
        )
        disallowed_categories = {
            item.category
            for item in normalized.findings
            if item.category not in categories
        }
        if disallowed_categories:
            raise ValueError(
                "semantic review used disallowed categories: "
                + ", ".join(sorted(item.value for item in disallowed_categories))
            )
        _validate_review_owner_contract(
            normalized,
            allowed_owner_groups=allowed_owner_groups,
            evidence_index=evidence_index,
        )
        _validate_local_review_contract(normalized, subject=subject)
        _validate_inventory_review_contract(normalized, subject=subject)
        _validate_behavior_review_contract(normalized, subject=subject)
        return validated_semantic_review(
            normalized,
            stage=stage,
            subject=subject,
            allowed_owner_ids=allowed_owner_ids,
            evidence_index=evidence_index,
        )

    subject_digest = canonical_digest(subject)
    review_attempt_root = run_dir / "attempts" / "reviews"
    for attempt_checkpoint in sorted(
        review_attempt_root.glob(
            f"{checkpoint_name}-{subject_digest[:12]}-proposal-*.json"
        )
    ):
        try:
            promoted = validate_candidate(
                proposal_type.model_validate(_read_json(attempt_checkpoint))
            )
        except ValueError:
            continue
        _write_json(checkpoint, promoted.model_dump(by_alias=True))
        print(
            json.dumps(
                {
                    "event": "checkpoint.promoted",
                    "stage": f"{stage.value.lower()}Review",
                    "source": attempt_checkpoint.name,
                }
            ),
            flush=True,
        )
        return promoted

    base_messages = [
        {"role": "system", "content": _SEMANTIC_REVIEW_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    previous: dict[str, Any] | None = None
    validation_error: str | None = None
    for attempt in range(2):
        messages = list(base_messages)
        if previous is not None:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": (
                                "Repair only the semantic-review contract violation. "
                                "Return the complete review proposal. Drop a finding "
                                "if it cannot be grounded using the finite allowed "
                                "owners and evidence; do not invent an owner or ref."
                            ),
                            "previousCandidate": previous,
                            "validationError": validation_error,
                            "allowedOwners": allowed_owner_groups,
                            "evidenceIndex": payload["evidenceIndex"],
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        parsed = _invoke(
            budget=budget,
            operation=(
                f"Executable{stage.value.title()}SemanticReview"
                if attempt == 0
                else f"Executable{stage.value.title()}SemanticReviewContractRepair"
            ),
            messages=messages,
            schema=proposal_type,
            use_case_id=f"review:{stage.value.lower()}",
            max_tokens=4096,
            reasoning_effort="low",
        )
        proposal = proposal_type.model_validate(parsed)
        previous = proposal.model_dump(by_alias=True)
        proposal_digest = canonical_digest(previous)
        _write_immutable_json(
            review_attempt_root
            / (
                f"{checkpoint_name}-{subject_digest[:12]}-"
                f"proposal-{proposal_digest[:12]}.json"
            ),
            previous,
        )
        try:
            result = validate_candidate(proposal)
        except ValueError as error:
            validation_error = str(error)
            if attempt == 0:
                continue
            raise ExperimentFailure(
                f"{checkpoint_name} semantic review contract remained invalid after "
                f"one repair: {validation_error}"
            ) from error
        _write_json(checkpoint, result.model_dump(by_alias=True))
        return result
    raise AssertionError("semantic review attempt loop did not return or raise")


def _adjudicate_semantic_review(
    *,
    review: ValidatedSemanticReview,
    subject: Mapping[str, Any],
    allowed_owner_ids: tuple[str, ...],
    run_dir: Path,
    budget: LogicalCallBudget,
    evidence_index_override: tuple[ReviewEvidenceRecord, ...] | None = None,
    checkpoint_key: str | None = None,
) -> AdjudicatedSemanticReview:
    evidence_index = (
        evidence_index_override
        if evidence_index_override is not None
        else _review_evidence_index(subject, allowed_owner_ids=allowed_owner_ids)
    )
    checkpoint_name = checkpoint_key or review.stage.value.lower()
    checkpoint = (
        run_dir
        / "validated"
        / "review-adjudications"
        / f"{checkpoint_name}.json"
    )
    if checkpoint.exists():
        try:
            stored = AdjudicatedSemanticReview.model_validate(_read_json(checkpoint))
            if stored.provenance.digest != review.provenance.digest:
                raise ValueError("adjudication checkpoint review changed")
            revalidated = validated_review_adjudication(
                SemanticReviewAdjudicationProposal(findings=stored.adjudications),
                review=review,
                subject=subject,
                evidence_index=evidence_index,
            )
        except ValueError:
            revalidated = None
        if revalidated is not None:
            _write_json(checkpoint, revalidated.model_dump(by_alias=True))
            _record_adjudicated_findings(
                run_dir=run_dir,
                checkpoint_key=checkpoint_name,
                review=revalidated,
            )
            print(
                json.dumps(
                    {
                        "event": "checkpoint.revalidated",
                        "stage": f"{review.stage.value.lower()}ReviewAdjudication",
                    }
                ),
                flush=True,
            )
            return revalidated

    reported = review.reported_high_findings
    if reported:
        payload = {
            "reviewStage": review.stage.value,
            "subject": subject,
            "evidenceIndex": [
                item.model_dump(by_alias=True) for item in evidence_index
            ],
            "reportedHighFindings": [
                {
                    "findingId": semantic_finding_id(item),
                    "finding": item.model_dump(by_alias=True),
                }
                for item in reported
            ],
        }
        parsed = _invoke(
            budget=budget,
            operation=f"Executable{review.stage.value.title()}SemanticAdjudication",
            messages=[
                {"role": "system", "content": _SEMANTIC_ADJUDICATION_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            schema=SemanticReviewAdjudicationProposal,
            use_case_id=f"adjudication:{review.stage.value.lower()}",
            max_tokens=4096,
            reasoning_effort="low",
        )
        proposal = SemanticReviewAdjudicationProposal.model_validate(parsed)
    else:
        proposal = SemanticReviewAdjudicationProposal(findings=())

    result = validated_review_adjudication(
        proposal,
        review=review,
        subject=subject,
        evidence_index=evidence_index,
    )
    attempt_payload = {
        "reviewProvenance": review.provenance.digest,
        "proposal": proposal.model_dump(by_alias=True),
    }
    _write_immutable_json(
        run_dir
        / "attempts"
        / "review-adjudications"
        / (
            f"{checkpoint_name}-{review.subject_digest[:12]}-"
            f"{review.provenance.digest[:12]}.json"
        ),
        attempt_payload,
    )
    _write_json(checkpoint, result.model_dump(by_alias=True))
    _record_adjudicated_findings(
        run_dir=run_dir,
        checkpoint_key=checkpoint_name,
        review=result,
    )
    return result


def _blocking_findings_by_owner(
    review: AdjudicatedSemanticReview,
    *,
    owner_stage: ReviewOwnerStage | None = None,
) -> dict[str, list[SemanticReviewFinding]]:
    result: dict[str, list[SemanticReviewFinding]] = {}
    for finding in review.blocking_findings:
        if owner_stage is not None and finding.owner_stage is not owner_stage:
            continue
        for owner_id in finding.owner_ids:
            result.setdefault(owner_id, []).append(finding)
    return result


def _blocking_review_owner_keys(review: AdjudicatedSemanticReview) -> set[str]:
    return {
        f"{finding.owner_stage.value}:{owner_id}"
        for finding in review.blocking_findings
        for owner_id in finding.owner_ids
    }


def _review_repair_cycle_path(
    run_dir: Path, stage: SemanticReviewStage
) -> Path:
    return run_dir / "validated" / "review-repairs" / f"{stage.value.lower()}-cycle.json"


def _review_repair_state(
    *,
    run_dir: Path,
    stage: SemanticReviewStage,
) -> tuple[dict[str, int], set[str]]:
    checkpoint = _review_repair_cycle_path(run_dir, stage)
    stored = _read_json(checkpoint) if checkpoint.exists() else {}
    repaired_owner_ids = {
        str(value)
        for value in (
            stored.get("repairedOwnerIds", [])
            if isinstance(stored, Mapping)
            else []
        )
    }
    raw_counts = (
        stored.get("repairCountsByOwner", {}) if isinstance(stored, Mapping) else {}
    )
    if not isinstance(raw_counts, Mapping):
        raw_counts = {}
    repair_counts = {
        str(key): max(0, int(value))
        for key, value in raw_counts.items()
    }
    for owner_id in repaired_owner_ids:
        repair_counts.setdefault(owner_id, 1)
    repair_dir = run_dir / "validated" / "review-repairs" / stage.value.lower()
    if repair_dir.exists():
        legacy_owner_stage = (
            ReviewOwnerStage.INVENTORY_FRAGMENT
            if stage is SemanticReviewStage.INVENTORY
            else ReviewOwnerStage.OPERATION_FRAGMENT
        )
        for path in repair_dir.glob("*.json"):
            repair_counts.setdefault(f"{legacy_owner_stage.value}:{path.stem}", 1)
    seen = {
        str(value)
        for value in (
            stored.get("seenBlockingFindingDigests", [])
            if isinstance(stored, Mapping)
            else []
        )
    }
    if isinstance(stored, Mapping) and stored.get("inputBlockingFindingDigest"):
        seen.add(str(stored["inputBlockingFindingDigest"]))
    return repair_counts, seen


def _review_repair_owners(
    *,
    run_dir: Path,
    review: AdjudicatedSemanticReview,
) -> set[str]:
    repair_counts, seen = _review_repair_state(run_dir=run_dir, stage=review.stage)
    finding_digest = blocking_finding_digest(review)
    if finding_digest in seen:
        raise ExperimentFailure(
            f"{review.stage.value.lower()} semantic review repeated the same HIGH findings"
        )
    owners = _blocking_review_owner_keys(review)
    exhausted_owners = {
        owner_id
        for owner_id in owners
        if repair_counts.get(owner_id, 0) >= MAX_SEMANTIC_REPAIRS_PER_OWNER
    }
    if exhausted_owners:
        raise ExperimentFailure(
            f"{review.stage.value.lower()} semantic repair already exhausted for owners: "
            + ", ".join(sorted(exhausted_owners))
        )
    return owners


def _record_review_repair_cycle(
    *,
    run_dir: Path,
    initial_review: AdjudicatedSemanticReview,
    output_subject: Mapping[str, Any],
    repaired_owner_ids: set[str],
) -> None:
    _record_patched_findings(
        run_dir=run_dir,
        checkpoint_key=initial_review.stage.value.lower(),
        review=initial_review,
        candidate_digest=canonical_digest(output_subject),
    )
    repair_counts, seen = _review_repair_state(
        run_dir=run_dir, stage=initial_review.stage
    )
    for owner_id in repaired_owner_ids:
        repair_counts[owner_id] = repair_counts.get(owner_id, 0) + 1
    seen.add(blocking_finding_digest(initial_review))
    _write_json(
        _review_repair_cycle_path(run_dir, initial_review.stage),
        {
            "repairedOwnerIds": sorted(repair_counts),
            "repairCountsByOwner": dict(sorted(repair_counts.items())),
            "seenBlockingFindingDigests": sorted(seen),
            "lastInputSubjectDigest": initial_review.subject_digest,
            "lastOutputSubjectDigest": canonical_digest(output_subject),
        },
    )


def _inventory_review_owner_ids(
    index: ScenarioIndex,
    run_dir: Path,
) -> tuple[str, ...]:
    stored = _read_json(run_dir / "validated" / "inventory-fragments.json")
    if not isinstance(stored, Mapping):
        raise ExperimentFailure("inventory review has no fragment checkpoint")
    fragments = {
        str(key): value for key, value in stored.items() if isinstance(value, Mapping)
    }
    resolution_ids = {
        *_inventory_conflicts(index, fragments),
        *_inventory_relationship_conflicts(index, fragments),
    }
    return (
        *(item.id for item in index.use_cases),
        *sorted(resolution_ids),
    )


def _inventory_review_subject(
    *,
    index: ScenarioIndex,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    stored = _read_json(run_dir / "validated" / "inventory-fragments.json")
    if not isinstance(stored, Mapping):
        raise ExperimentFailure("inventory review has no fragment provenance")
    owner_scopes: dict[str, list[str]] = {}
    for use_case in index.use_cases:
        fragment = stored.get(use_case.id)
        if not isinstance(fragment, Mapping):
            raise ExperimentFailure(
                f"inventory review has no fragment provenance for {use_case.id}"
            )
        refs = [
            f"class:{item.get('className')}"
            for item in fragment.get("Classes", []) or []
            if isinstance(item, Mapping)
        ]
        refs.extend(
            f"type:{item.get('name')}"
            for item in fragment.get("DataTypes", []) or []
            if isinstance(item, Mapping)
        )
        refs.extend(
            "relationship:" + _relationship_pair(item)
            for item in fragment.get("Relationships", []) or []
            if isinstance(item, Mapping)
        )
        owner_scopes[f"inventoryFragment:{use_case.id}"] = sorted(set(refs))
    fragments = {
        str(key): value for key, value in stored.items() if isinstance(value, Mapping)
    }
    for key in _inventory_conflicts(index, fragments):
        owner_scopes[f"inventoryResolution:{key}"] = [key]
    for key in _inventory_relationship_conflicts(index, fragments):
        owner_scopes[f"inventoryResolution:{key}"] = [key]
    return {
        "inventoryRolePolicy": (
            "Boundary and Control classes may remain use-case-specific. Shared domain "
            "words do not prove semantic duplication; different stated coordination "
            "responsibilities are distinct. Possible consolidation is only a "
            "non-blocking OVER_FRAGMENTED_ROLE observation."
        ),
        "scenario": scenario,
        "inventory": inventory,
        "ownerScopes": owner_scopes,
    }


def _repair_inventory_resolutions(
    *,
    index: ScenarioIndex,
    fragments: Mapping[str, Mapping[str, Any]],
    inventory: Mapping[str, Any],
    review: AdjudicatedSemanticReview,
    run_dir: Path,
    budget: LogicalCallBudget,
) -> None:
    resolution_findings = _blocking_findings_by_owner(
        review, owner_stage=ReviewOwnerStage.INVENTORY_RESOLUTION
    )
    if not resolution_findings:
        return
    item_conflicts = _inventory_conflicts(index, fragments)
    relationship_conflicts = _inventory_relationship_conflicts(index, fragments)
    declared_names = sorted(
        {
            str(item.get("className") or item.get("name") or "")
            for fragment in fragments.values()
            for collection in ("Classes", "DataTypes")
            for item in fragment.get(collection, []) or []
            if isinstance(item, Mapping)
        }
    )

    for owner_id, findings in sorted(resolution_findings.items()):
        finding_payload = [item.model_dump(by_alias=True) for item in findings]
        if owner_id.startswith("relationship:"):
            variants = relationship_conflicts.get(owner_id)
            if variants is None:
                continue
            checkpoint = (
                run_dir
                / "validated"
                / "inventory-relationship-resolutions"
                / f"{owner_id.replace(':', '-').replace('|', '-')}.json"
            )
            stored = _read_json(checkpoint)
            current = stored.get("relationship") if isinstance(stored, Mapping) else None
            if not isinstance(current, Mapping):
                raise ExperimentFailure(f"missing inventory resolution: {owner_id}")
            parsed = _invoke(
                budget=budget,
                operation="ExecutableInventoryResolutionSemanticRepair",
                messages=[
                    {
                        "role": "system",
                        "content": _INVENTORY_RELATIONSHIP_CONFLICT_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "Repair this merged relationship for the HIGH semantic findings.",
                                "ownerId": owner_id,
                                "variants": variants,
                                "currentResolution": current,
                                "globalInventory": inventory,
                                "reviewFindings": finding_payload,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                schema=InventoryRelationshipResolution,
                use_case_id=owner_id,
                max_tokens=2048,
                reasoning_effort="low",
            )
            relationship_proposal = InventoryRelationshipResolution.model_validate(parsed)
            relationship = relationship_proposal.relationship.model_dump(by_alias=True)
            if canonical_digest(relationship) == canonical_digest(current):
                raise ExperimentFailure(
                    f"{owner_id} semantic repair repeated the rejected resolution"
                )
            local_findings = _relationship_resolution_findings(
                owner_id, variants, relationship
            )
            if local_findings:
                raise ExperimentFailure(
                    f"{owner_id} semantic repair is invalid: "
                    + "; ".join(local_findings)
                )
            _write_json(
                checkpoint,
                {
                    "inputDigest": canonical_digest(
                        {"key": owner_id, "variants": variants}
                    ),
                    "relationship": relationship,
                },
            )
            attempt = {"relationship": relationship}
        else:
            variants = item_conflicts.get(owner_id)
            if variants is None:
                continue
            checkpoint = (
                run_dir
                / "validated"
                / "inventory-resolutions"
                / f"{owner_id.replace(':', '-')}.json"
            )
            stored = _read_json(checkpoint)
            current = stored.get("item") if isinstance(stored, Mapping) else None
            if not isinstance(current, Mapping):
                raise ExperimentFailure(f"missing inventory resolution: {owner_id}")
            parsed = _invoke(
                budget=budget,
                operation="ExecutableInventoryResolutionSemanticRepair",
                messages=[
                    {"role": "system", "content": _INVENTORY_CONFLICT_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "Repair this merged item for the HIGH semantic findings.",
                                "ownerId": owner_id,
                                "variants": variants,
                                "globalDeclaredNames": declared_names,
                                "currentResolution": current,
                                "globalInventory": inventory,
                                "reviewFindings": finding_payload,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                schema=InventoryConflictResolution,
                use_case_id=owner_id,
                max_tokens=4096,
                reasoning_effort="low",
            )
            item_proposal = InventoryConflictResolution.model_validate(parsed)
            item = _normalized_resolution_item(item_proposal.item)
            if canonical_digest(item) == canonical_digest(current):
                raise ExperimentFailure(
                    f"{owner_id} semantic repair repeated the rejected resolution"
                )
            local_findings = _resolution_findings(owner_id, variants, item)
            if local_findings:
                raise ExperimentFailure(
                    f"{owner_id} semantic repair is invalid: "
                    + "; ".join(local_findings)
                )
            _write_json(
                checkpoint,
                {
                    "inputDigest": canonical_digest(
                        {
                            "key": owner_id,
                            "variants": variants,
                            "declaredNames": declared_names,
                        }
                    ),
                    "item": item,
                },
            )
            attempt = {"item": item}
        _write_json(
            run_dir
            / "attempts"
            / "review-repairs"
            / f"inventory-resolution-{owner_id.replace(':', '-').replace('|', '-')}.json",
            attempt,
        )


def _repair_inventory_review(
    *,
    index: ScenarioIndex,
    inventory: Mapping[str, Any],
    review: AdjudicatedSemanticReview,
    run_dir: Path,
    budget: LogicalCallBudget,
    parallelism: int,
) -> dict[str, Any]:
    """Repair each inventory owner once, then rebuild every merge decision."""
    stored_fragments = _read_json(
        run_dir / "validated" / "inventory-fragments.json"
    )
    if not isinstance(stored_fragments, Mapping):
        raise ExperimentFailure("inventory semantic repair has no fragment checkpoint")
    fragments = {
        use_case.id: dict(stored_fragments[use_case.id])
        for use_case in index.use_cases
        if isinstance(stored_fragments.get(use_case.id), Mapping)
    }
    if len(fragments) != len(index.use_cases):
        raise ExperimentFailure("inventory semantic repair has incomplete fragments")
    use_cases = {item.id: item for item in index.use_cases}

    fragment_findings = _blocking_findings_by_owner(
        review, owner_stage=ReviewOwnerStage.INVENTORY_FRAGMENT
    )
    for owner_id, findings in sorted(fragment_findings.items()):
        use_case = use_cases[owner_id]
        current = fragments[owner_id]
        finding_payload = [item.model_dump(by_alias=True) for item in findings]
        repair_input = {
            "reviewSubjectDigest": review.subject_digest,
            "blockingFindingDigest": canonical_digest(finding_payload),
            "currentFragment": current,
        }
        input_digest = canonical_digest(repair_input)
        checkpoint = (
            run_dir
            / "validated"
            / "review-repairs"
            / "inventory"
            / f"{owner_id}.json"
        )
        if checkpoint.exists():
            stored = _read_json(checkpoint)
            candidate = stored.get("fragment") if isinstance(stored, Mapping) else None
            if (
                isinstance(stored, Mapping)
                and stored.get("inputDigest") == input_digest
                and isinstance(candidate, Mapping)
                and not _inventory_fragment_findings(index, use_case, candidate)
            ):
                fragments[owner_id] = dict(candidate)
                print(
                    f'{{"event":"checkpoint.hit","stage":"inventorySemanticRepair","useCaseId":"{owner_id}"}}',
                    flush=True,
                )
                continue

        base_messages = [
            {"role": "system", "content": _INVENTORY_FRAGMENT_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": (
                            "Repair this one inventory fragment for the HIGH semantic "
                            "findings. For a cross-fragment semantic duplicate, "
                            "canonicalize this owner's duplicate item to the "
                            "established shared concept named in targetRefs while "
                            "preserving this use case's local facts. Keep this "
                            "fragment type-closed: every referenced class or data type "
                            "must be declared locally. Return the complete "
                            "InventoryProposal."
                        ),
                        "useCaseSlice": _inventory_fragment_payload(index, use_case),
                        "currentFragment": current,
                        "globalInventory": inventory,
                        "reviewFindings": finding_payload,
                        "immutableUseCaseId": owner_id,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        previous: dict[str, Any] | None = None
        local_findings: list[str] = []
        accepted_fragment: dict[str, Any] | None = None
        seen = {canonical_digest(current)}
        for attempt in range(2):
            messages = list(base_messages)
            if previous is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": (
                                    "Repair only these deterministic local validation "
                                    "errors. Preserve the confirmed semantic repair "
                                    "and return the complete InventoryProposal."
                                ),
                                "previousCandidate": previous,
                                "localValidationFindings": local_findings,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            parsed = _invoke(
                budget=budget,
                operation=(
                    "ExecutableInventorySemanticRepair"
                    if attempt == 0
                    else "ExecutableInventorySemanticRepairLocalCorrection"
                ),
                messages=messages,
                schema=InventoryProposal,
                use_case_id=owner_id,
                max_tokens=4096,
                reasoning_effort="low",
            )
            proposal = InventoryProposal.model_validate(parsed)
            previous = proposal.model_dump(by_alias=True)
            fragment = normalize_inventory(proposal).as_payload()
            digest = canonical_digest(fragment)
            _write_immutable_json(
                run_dir
                / "attempts"
                / "review-repairs"
                / f"inventory-{owner_id}-proposal-{digest[:12]}.json",
                previous,
            )
            if digest in seen:
                local_findings = [
                    "candidate repeats a previously rejected fragment"
                ]
            else:
                seen.add(digest)
                local_findings = _inventory_fragment_findings(
                    index, use_case, fragment
                )
            if not local_findings:
                accepted_fragment = fragment
                break
        if accepted_fragment is None:
            raise ExperimentFailure(
                f"{owner_id} inventory semantic repair remained locally invalid "
                "after one correction: "
                + "; ".join(local_findings)
            )
        fragment = accepted_fragment
        fragments[owner_id] = fragment
        _write_json(
            run_dir / "validated" / "inventory-fragments" / f"{owner_id}.json",
            fragment,
        )
        _write_json(
            checkpoint,
            {"inputDigest": input_digest, "fragment": fragment},
        )

    _repair_inventory_resolutions(
        index=index,
        fragments=fragments,
        inventory=inventory,
        review=review,
        run_dir=run_dir,
        budget=budget,
    )
    return _compose_inventory(
        index=index,
        fragments=fragments,
        run_dir=run_dir,
        budget=budget,
        parallelism=parallelism,
    )


def _repair_behavior_review(
    *,
    review: AdjudicatedSemanticReview,
    operations: Mapping[str, ValidatedOperationFragment],
    inputs: tuple[UseCaseInputs, ...],
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    run_dir: Path,
    budget: LogicalCallBudget,
    parallelism: int,
) -> dict[str, ValidatedOperationFragment]:
    """Repair each operation fragment once and re-run catalog conflict handling."""
    result = dict(operations)
    inputs_by_id = {item.use_case.id: item for item in inputs}
    operation_findings = _blocking_findings_by_owner(
        review, owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT
    )
    for owner_id, findings in sorted(operation_findings.items()):
        item = inputs_by_id[owner_id]
        current = result[owner_id]
        finding_payload = [value.model_dump(by_alias=True) for value in findings]
        repair_input = {
            "reviewSubjectDigest": review.subject_digest,
            "blockingFindingDigest": canonical_digest(finding_payload),
            "currentFragment": current.payload,
        }
        input_digest = canonical_digest(repair_input)
        checkpoint = (
            run_dir
            / "validated"
            / "review-repairs"
            / "behavior"
            / f"{owner_id}.json"
        )
        context = OperationContext.from_payload(
            owner_id,
            inventory,
            scenario=scenario,
            allowed_step_ids=item.allowed_steps,
            durable_entity_names=item.durable_entities,
            allowed_owner_names=item.allowed_owners,
        )
        if checkpoint.exists():
            stored = _read_json(checkpoint)
            checkpoint_candidate = (
                stored.get("fragment") if isinstance(stored, Mapping) else None
            )
            try:
                reused = (
                    _validated_operation_fragment_for_inputs(
                        checkpoint_candidate,
                        context=context,
                        inputs=item,
                        inventory=inventory,
                    )
                    if isinstance(checkpoint_candidate, Mapping)
                    and stored.get("inputDigest") == input_digest
                    else None
                )
            except OperationValidationError:
                reused = None
            if reused is not None:
                result[owner_id] = reused
                print(
                    f'{{"event":"checkpoint.hit","stage":"behaviorSemanticRepair","useCaseId":"{owner_id}"}}',
                    flush=True,
                )
                continue

        confirmed_ids = tuple(semantic_finding_id(value) for value in findings)
        base_payload = {
            "task": (
                "Patch only the operations responsible for these confirmed "
                "semantic findings."
            ),
            "generationInput": _operation_prompt(item, inventory),
            "baseDigest": canonical_digest(current.payload),
            "confirmedFindingIds": list(confirmed_ids),
            "existingOperationTargets": [
                {
                    "owner": str(
                        class_set.get("className") or class_set.get("name") or ""
                    ),
                    "operationRef": (
                        f"{class_set.get('className') or class_set.get('name')}"
                        f"::{operation.get('name')}("
                        + ",".join(
                            f"{parameter.get('name')}:{parameter.get('type')}"
                            for parameter in operation.get("parameters", []) or []
                            if isinstance(parameter, Mapping)
                        )
                        + ")"
                    ),
                    "expectedDigest": canonical_digest(operation),
                }
                for class_set in current.payload.get("Classes", []) or []
                if isinstance(class_set, Mapping)
                for operation in class_set.get("operations", []) or []
                if isinstance(operation, Mapping)
            ],
            "absenceExpectedDigest": canonical_digest(None),
            "currentFragment": current.payload,
            "reviewFindings": finding_payload,
            "immutableUseCaseId": owner_id,
        }
        previous_patch: dict[str, Any] | None = None
        local_findings: list[str] = []
        seen_patches: set[str] = set()
        repaired: ValidatedOperationFragment | None = None
        candidate: dict[str, Any] | None = None
        for correction_attempt in range(2):
            messages = [
                {"role": "system", "content": OPERATION_PATCH_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(base_payload, ensure_ascii=False),
                },
            ]
            if previous_patch is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": (
                                    "Correct only these deterministic local patch "
                                    "errors. Preserve the confirmed semantic fix."
                                ),
                                "previousPatch": previous_patch,
                                "localValidationFindings": local_findings,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            parsed = _invoke(
                budget=budget,
                operation=(
                    "ExecutableBehaviorSemanticRepair"
                    if correction_attempt == 0
                    else "ExecutableBehaviorSemanticRepairLocalCorrection"
                ),
                messages=messages,
                schema=OperationFragmentPatch,
                use_case_id=owner_id,
                max_tokens=4096,
                reasoning_effort="low",
            )
            patch = OperationFragmentPatch.model_validate(parsed)
            previous_patch = patch.model_dump(by_alias=True)
            patch_digest = canonical_digest(previous_patch)
            if patch_digest in seen_patches:
                local_findings = ["patch repeats a previously rejected candidate"]
                continue
            seen_patches.add(patch_digest)
            if set(patch.finding_ids) != set(confirmed_ids):
                local_findings = [
                    "patch must cover every confirmed finding ID exactly"
                ]
                continue
            try:
                patch_attempt = apply_operation_fragment_patch(
                    current,
                    patch,
                    confirmed_finding_ids=confirmed_ids,
                )
                candidate = patch_attempt.after
                repaired = _validated_operation_fragment_for_inputs(
                    candidate,
                    context=context,
                    inputs=item,
                    inventory=inventory,
                )
            except OperationPatchError as error:
                local_findings = [str(error)]
                patch_attempt = None
            except OperationValidationError as error:
                local_findings = list(error.findings)
                repaired = None
            _write_immutable_json(
                run_dir
                / "attempts"
                / "review-repairs"
                / f"behavior-{owner_id}-{input_digest[:12]}-{patch_digest[:12]}.json",
                {
                    "patch": previous_patch,
                    "attempt": (
                        patch_attempt.model_dump(by_alias=True)
                        if patch_attempt is not None
                        else None
                    ),
                    "localValidationFindings": local_findings,
                },
            )
            if repaired is not None:
                break
        if repaired is None or candidate is None:
            raise ExperimentFailure(
                f"{owner_id} behavior semantic repair remained locally invalid "
                "after one correction: "
                + "; ".join(local_findings)
            )
        result[owner_id] = repaired
        _write_json(
            run_dir / "validated" / "operations" / f"{owner_id}.json",
            repaired.model_dump(by_alias=True),
        )
        _write_json(
            checkpoint,
            {"inputDigest": input_digest, "fragment": candidate},
        )

    return _reconcile_operation_conflicts(
        operations=result,
        inputs=inputs,
        scenario=scenario,
        inventory=inventory,
        run_dir=run_dir,
        budget=budget,
        parallelism=parallelism,
    )


def run(
    app_id: str, run_dir: Path, *, parallelism: int, max_calls: int
) -> dict[str, Any]:
    run_dir = _inside_experiment_root(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    index = _load_index(app_id)
    scenario = _scenario_payload(index)
    connection = build_llm_connection()
    manifest = {
        "schemaVersion": "easydep-executable-behavior-llm-experiment/v3",
        "semanticReviewVersion": REVIEW_VALIDATOR_VERSION,
        "appId": app_id,
        "scenarioDigest": canonical_digest(scenario),
        "provider": connection.provider,
        "model": connection.model,
        "parallelism": parallelism,
        "maxLogicalCalls": max_calls,
        "writesProductionState": False,
    }
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        previous_manifest = _read_json(manifest_path)
        identity_keys = (
            "schemaVersion",
            "appId",
            "scenarioDigest",
            "provider",
            "model",
        )
        if any(previous_manifest.get(key) != manifest[key] for key in identity_keys):
            raise ExperimentFailure(
                "existing experiment manifest does not match this run"
            )
        previous_review_version = previous_manifest.get("semanticReviewVersion")
        if previous_review_version not in {
            None,
            "executable-behavior.semantic-review.v1",
            REVIEW_VALIDATOR_VERSION,
        }:
            raise ExperimentFailure(
                "existing experiment semantic review version does not match this run"
            )
    _write_json(manifest_path, manifest)
    _write_json(run_dir / "scenario.json", scenario)

    budget = LogicalCallBudget(max_calls, run_name=run_dir.name)
    prior_timings = (
        _read_json(run_dir / "llm-timings.json")
        if (run_dir / "llm-timings.json").exists()
        else []
    )
    settings.llm_capture_response_content = False
    started = perf_counter()
    summary: dict[str, Any] = {**manifest, "status": "RUNNING"}
    timings: list[dict[str, Any]] = []
    inventory_review: AdjudicatedSemanticReview | None = None
    behavior_review: AdjudicatedSemanticReview | None = None
    semantic_repair_owners: set[str] = set()
    try:
        with capture_llm_timings() as timings:
            inventory = _inventory(
                index,
                run_dir,
                budget,
                parallelism=parallelism,
            )
            _write_json(run_dir / "validated" / "inventory.json", inventory)
            owner_ids = tuple(item.id for item in index.use_cases)
            inventory_owner_ids = _inventory_review_owner_ids(index, run_dir)
            inventory_subject = _inventory_review_subject(
                index=index,
                scenario=scenario,
                inventory=inventory,
                run_dir=run_dir,
            )
            raw_inventory_review = _semantic_review(
                stage=SemanticReviewStage.INVENTORY,
                subject=inventory_subject,
                allowed_owner_ids=inventory_owner_ids,
                run_dir=run_dir,
                budget=budget,
                category_override=INVENTORY_REVIEW_CATEGORIES,
            )
            inventory_review = _adjudicate_semantic_review(
                review=raw_inventory_review,
                subject=inventory_subject,
                allowed_owner_ids=inventory_owner_ids,
                run_dir=run_dir,
                budget=budget,
            )
            if inventory_review.unresolved_findings:
                raise ExperimentFailure(
                    "inventory semantic adjudication is unresolved: "
                    + ", ".join(
                        semantic_finding_id(item)
                        for item in inventory_review.unresolved_findings
                    )
                )
            while inventory_review.blocking_findings:
                repair_owners = _review_repair_owners(
                    run_dir=run_dir,
                    review=inventory_review,
                )
                semantic_repair_owners.update(repair_owners)
                inventory = _repair_inventory_review(
                    index=index,
                    inventory=inventory,
                    review=inventory_review,
                    run_dir=run_dir,
                    budget=budget,
                    parallelism=parallelism,
                )
                inventory_subject = _inventory_review_subject(
                    index=index,
                    scenario=scenario,
                    inventory=inventory,
                    run_dir=run_dir,
                )
                _record_review_repair_cycle(
                    run_dir=run_dir,
                    initial_review=inventory_review,
                    output_subject=inventory_subject,
                    repaired_owner_ids=repair_owners,
                )
                inventory_owner_ids = _inventory_review_owner_ids(index, run_dir)
                raw_inventory_review = _semantic_review(
                    stage=SemanticReviewStage.INVENTORY,
                    subject=inventory_subject,
                    allowed_owner_ids=inventory_owner_ids,
                    run_dir=run_dir,
                    budget=budget,
                    category_override=INVENTORY_REVIEW_CATEGORIES,
                )
                inventory_review = _adjudicate_semantic_review(
                    review=raw_inventory_review,
                    subject=inventory_subject,
                    allowed_owner_ids=inventory_owner_ids,
                    run_dir=run_dir,
                    budget=budget,
                )
                if inventory_review.unresolved_findings:
                    raise ExperimentFailure(
                        "inventory semantic adjudication is unresolved: "
                        + ", ".join(
                            semantic_finding_id(item)
                            for item in inventory_review.unresolved_findings
                        )
                    )
            use_case_inputs = tuple(
                _use_case_inputs(index, inventory, item) for item in index.use_cases
            )
            operations = _parallel(
                use_case_inputs,
                lambda item: _operation_fragment(
                    inputs=item,
                    scenario=scenario,
                    inventory=inventory,
                    run_dir=run_dir,
                    budget=budget,
                ),
                parallelism=parallelism,
            )
            local_results = _parallel(
                use_case_inputs,
                lambda item: _local_operation_seal(
                    inputs=item,
                    fragment=operations[item.use_case.id],
                    scenario=scenario,
                    inventory=inventory,
                    run_dir=run_dir,
                    budget=budget,
                ),
                parallelism=parallelism,
            )
            operations = {
                use_case_id: value[0]
                for use_case_id, value in local_results.items()
            }
            seals = {
                use_case_id: value[1]
                for use_case_id, value in local_results.items()
            }
            pre_integration_operations = dict(operations)
            operations = _reconcile_operation_conflicts(
                operations=operations,
                inputs=use_case_inputs,
                scenario=scenario,
                inventory=inventory,
                run_dir=run_dir,
                budget=budget,
                parallelism=parallelism,
            )
            escaped_owner_ids = {
                use_case_id
                for use_case_id, operation in operations.items()
                if canonical_digest(operation.payload)
                != canonical_digest(pre_integration_operations[use_case_id].payload)
            }
            if escaped_owner_ids:
                escape_payload = {
                    use_case_id: {
                        "reason": "LOCAL_CONTRACT_ESCAPE",
                        "beforeDigest": canonical_digest(
                            pre_integration_operations[use_case_id].payload
                        ),
                        "afterDigest": canonical_digest(
                            operations[use_case_id].payload
                        ),
                    }
                    for use_case_id in sorted(escaped_owner_ids)
                }
                _write_immutable_json(
                    run_dir
                    / "attempts"
                    / "local-contract-escapes"
                    / f"{canonical_digest(escape_payload)[:16]}.json",
                    escape_payload,
                )
                local_results = _parallel(
                    use_case_inputs,
                    lambda item: _local_operation_seal(
                        inputs=item,
                        fragment=operations[item.use_case.id],
                        scenario=scenario,
                        inventory=inventory,
                        run_dir=run_dir,
                        budget=budget,
                    ),
                    parallelism=parallelism,
                )
                operations = {
                    use_case_id: value[0]
                    for use_case_id, value in local_results.items()
                }
                seals = {
                    use_case_id: value[1]
                    for use_case_id, value in local_results.items()
                }
            catalog_result: CatalogResult = assemble_sealed_catalog(
                (seals[item.use_case.id] for item in use_case_inputs),
                inventory=inventory,
                scenario=scenario,
            )
            catalog = catalog_result.validated
            behavior_subject = _behavior_review_subject(
                scenario=scenario,
                catalog=catalog,
                operations=operations,
                seals=seals,
            )
            raw_behavior_review = _semantic_review(
                stage=SemanticReviewStage.BEHAVIOR,
                subject=behavior_subject,
                allowed_owner_ids=owner_ids,
                run_dir=run_dir,
                budget=budget,
                category_override=frozenset(
                    {ReviewCategory.SHARED_CONTRACT_REGRESSION}
                ),
            )
            behavior_review = _adjudicate_semantic_review(
                review=raw_behavior_review,
                subject=behavior_subject,
                allowed_owner_ids=owner_ids,
                run_dir=run_dir,
                budget=budget,
            )
            if behavior_review.unresolved_findings:
                raise ExperimentFailure(
                    "behavior semantic adjudication is unresolved: "
                    + ", ".join(
                        semantic_finding_id(item)
                        for item in behavior_review.unresolved_findings
                    )
                )
            while behavior_review.blocking_findings:
                repair_owners = _review_repair_owners(
                    run_dir=run_dir,
                    review=behavior_review,
                )
                semantic_repair_owners.update(repair_owners)
                operations = _repair_behavior_review(
                    review=behavior_review,
                    operations=operations,
                    inputs=use_case_inputs,
                    scenario=scenario,
                    inventory=inventory,
                    run_dir=run_dir,
                    budget=budget,
                    parallelism=parallelism,
                )
                local_results = _parallel(
                    use_case_inputs,
                    lambda item: _local_operation_seal(
                        inputs=item,
                        fragment=operations[item.use_case.id],
                        scenario=scenario,
                        inventory=inventory,
                        run_dir=run_dir,
                        budget=budget,
                    ),
                    parallelism=parallelism,
                )
                operations = {
                    use_case_id: value[0]
                    for use_case_id, value in local_results.items()
                }
                seals = {
                    use_case_id: value[1]
                    for use_case_id, value in local_results.items()
                }
                catalog_result = assemble_sealed_catalog(
                    (seals[item.use_case.id] for item in use_case_inputs),
                    inventory=inventory,
                    scenario=scenario,
                )
                catalog = catalog_result.validated
                behavior_subject = _behavior_review_subject(
                    scenario=scenario,
                    catalog=catalog,
                    operations=operations,
                    seals=seals,
                )
                _record_review_repair_cycle(
                    run_dir=run_dir,
                    initial_review=behavior_review,
                    output_subject=behavior_subject,
                    repaired_owner_ids=repair_owners,
                )
                raw_behavior_review = _semantic_review(
                    stage=SemanticReviewStage.BEHAVIOR,
                    subject=behavior_subject,
                    allowed_owner_ids=owner_ids,
                    run_dir=run_dir,
                    budget=budget,
                    category_override=frozenset(
                        {ReviewCategory.SHARED_CONTRACT_REGRESSION}
                    ),
                )
                behavior_review = _adjudicate_semantic_review(
                    review=raw_behavior_review,
                    subject=behavior_subject,
                    allowed_owner_ids=owner_ids,
                    run_dir=run_dir,
                    budget=budget,
                )
                if behavior_review.unresolved_findings:
                    raise ExperimentFailure(
                        "behavior semantic adjudication is unresolved: "
                        + ", ".join(
                            semantic_finding_id(item)
                            for item in behavior_review.unresolved_findings
                        )
                    )
            _write_json(
                run_dir / "validated" / "catalog.json",
                catalog.model_dump(by_alias=True),
            )

            calls = _parallel(
                use_case_inputs,
                lambda item: _call_structure(
                    inputs=item,
                    scenario=scenario,
                    catalog=catalog,
                    run_dir=run_dir,
                    budget=budget,
                    required_effect_operation_refs=_required_effect_operations(
                        seals[item.use_case.id]
                    ),
                )[0],
                parallelism=parallelism,
            )
            for item in use_case_inputs:
                validate_effect_call_links(
                    seals[item.use_case.id], calls[item.use_case.id]
                )

            bindings: dict[str, ValidatedBindingPlan] = {}
            for item in use_case_inputs:
                call_structure = calls[item.use_case.id]
                try:
                    binding, _ = _binding_plan(
                        inputs=item,
                        scenario=scenario,
                        catalog=catalog,
                        calls=call_structure,
                        run_dir=run_dir,
                        budget=budget,
                    )
                except BindingSelectionError as first_error:
                    repaired_calls, _ = _call_structure(
                        inputs=item,
                        scenario=scenario,
                        catalog=catalog,
                        run_dir=run_dir,
                        budget=budget,
                        required_effect_operation_refs=_required_effect_operations(
                            seals[item.use_case.id]
                        ),
                        extra_finding=str(first_error),
                        force_repair=True,
                    )
                    calls[item.use_case.id] = repaired_calls
                    validate_effect_call_links(
                        seals[item.use_case.id], repaired_calls
                    )
                    binding, _ = _binding_plan(
                        inputs=item,
                        scenario=scenario,
                        catalog=catalog,
                        calls=repaired_calls,
                        run_dir=run_dir,
                        budget=budget,
                    )
                bindings[item.use_case.id] = binding

            witnesses = tuple(
                validate_execution_witness(
                    catalog,
                    calls[item.use_case.id],
                    bindings[item.use_case.id],
                )
                for item in use_case_inputs
            )
            accepted: AcceptedBehaviorModel = accept_behavior(
                catalog,
                witnesses,
                required_use_case_ids=(item.use_case.id for item in use_case_inputs),
            )
            model: BCEModel = materialize_bce_model(accepted)
            model_payload = model.model_dump(by_alias=True)
            _write_json(
                run_dir / "accepted-behavior.json", accepted.model_dump(by_alias=True)
            )
            _write_json(run_dir / "class-model.json", model_payload)
            (run_dir / "class-diagram.puml").write_text(
                generate_plantuml_from_bce_json(model_payload),
                encoding="utf-8",
            )
        physical = [
            item for item in [*prior_timings, *timings] if item.get("physicalRequest")
        ]
        review_adjudications = [
            item
            for review in (
                inventory_review,
                behavior_review,
                *(seal.review for seal in seals.values()),
            )
            if review is not None
            for item in review.adjudications
        ]
        confirmed_count = sum(
            item.disposition is ReviewFindingDisposition.CONFIRMED
            for item in review_adjudications
        )
        refuted_count = sum(
            item.disposition is ReviewFindingDisposition.REFUTED
            for item in review_adjudications
        )
        unresolved_count = sum(
            item.disposition is ReviewFindingDisposition.UNRESOLVED
            for item in review_adjudications
        )
        summary.update(
            {
                "status": "ACCEPTED",
                "elapsedSeconds": round(perf_counter() - started, 3),
                "logicalCallsThisRun": budget.used,
                "physicalCallsTotal": len(physical),
                "inputTokensTotal": sum(
                    int(item.get("inputTokens") or 0) for item in physical
                ),
                "outputTokensTotal": sum(
                    int(item.get("outputTokens") or 0) for item in physical
                ),
                "inventoryReviewFindingCount": len(
                    inventory_review.review.findings if inventory_review else ()
                ),
                "behaviorReviewFindingCount": len(
                    behavior_review.review.findings if behavior_review else ()
                ),
                "reviewConfirmedFindingCount": confirmed_count,
                "reviewRefutedFindingCount": refuted_count,
                "reviewUnresolvedFindingCount": unresolved_count,
                "confirmedDefectRate": (
                    round(confirmed_count / len(review_adjudications), 4)
                    if review_adjudications
                    else None
                ),
                "reviewerFalsePositiveRate": (
                    round(refuted_count / len(review_adjudications), 4)
                    if review_adjudications
                    else None
                ),
                "semanticRepairOwnerCount": len(semantic_repair_owners),
                "localSealCount": len(seals),
                "localContractEscapeOwnerCount": len(escaped_owner_ids),
                "declaredStateEffectCount": sum(
                    len(operation.effects)
                    for seal in seals.values()
                    for operation in seal.semantics.contract.operations
                ),
                "useCaseCount": len(use_case_inputs),
                "executionGroupCount": sum(
                    len(item.groups) for item in use_case_inputs
                ),
                "classCount": len(model.Classes),
                "dataTypeCount": len(model.DataTypes),
                "operationCount": sum(len(item.operations) for item in model.Classes),
                "callCount": sum(len(item.calls) for item in model.Collaborations),
                "collaborationCount": len(model.Collaborations),
                **_finding_ledger_metrics(run_dir),
            }
        )
    except Exception as error:
        summary.update(
            {
                "status": "FAILED",
                "elapsedSeconds": round(perf_counter() - started, 3),
                "logicalCallsThisRun": budget.used,
                "errorType": type(error).__name__,
                "error": str(error),
            }
        )
        raise
    finally:
        all_timings = [*prior_timings, *timings]
        _write_json(run_dir / "llm-timings.json", all_timings)
        physical = [item for item in all_timings if item.get("physicalRequest")]
        summary.update(
            {
                "physicalCallsTotal": len(physical),
                "inputTokensTotal": sum(
                    int(item.get("inputTokens") or 0) for item in physical
                ),
                "outputTokensTotal": sum(
                    int(item.get("outputTokens") or 0) for item in physical
                ),
            }
        )
        _write_json(run_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", default=DEFAULT_APP_ID)
    parser.add_argument("--run-name", default=DEFAULT_RUN_NAME)
    parser.add_argument("--parallelism", type=int, default=4)
    parser.add_argument("--max-calls", type=int, default=40)
    arguments = parser.parse_args()
    if arguments.parallelism < 1 or arguments.max_calls < 1:
        raise ValueError("parallelism and max-calls must be positive")
    run(
        arguments.app_id,
        EXPERIMENT_ROOT / arguments.run_name,
        parallelism=arguments.parallelism,
        max_calls=arguments.max_calls,
    )


if __name__ == "__main__":
    main()
