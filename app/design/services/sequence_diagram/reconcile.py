"""시퀀스 호출과 클래스 메서드를 대사해 두 산출물의 일관성을 보장한다."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.design.knowledge.detectors import (
    sequence_diagram_findings,
    sequence_return_values_match_methods,
)
from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.reviser import revise_bce_classes
from app.design.services.common.validation import validate_puml_artifact
from app.design.services.sequence_diagram.methods import (
    is_complete_method_call,
    method_call_signature,
    method_return_type,
    normalize_return_type,
)
from app.design.services.sequence_diagram.extractor import (
    reassemble_sequence_diagrams,
)


_CALL_TYPES = {"sync", "async", "self"}

# These defects are best repaired by re-running the fixed sequence template
# against the already-approved class contract.  They are not a reason to ask a
# language model to edit an arbitrary existing interaction in place.
_REASSEMBLY_RULE_IDS = {
    "sequence.actor-step-involvement",
    "sequence.boundary-operation-direction",
    "sequence.causal-call-chain",
    "sequence.database-access-discipline",
    "sequence.duplicate-consecutive-messages",
    "sequence.flow-order",
    "sequence.fragment-condition-consistency",
    "sequence.initial-message-entry",
    "sequence.message-bce-flow",
    "sequence.message-participants-exist",
    "sequence.no-lifecycle-events",
    "sequence.orphan-participant-detection",
    "sequence.participant-classes-exist",
    "sequence.references-exist",
    "sequence.step-operation-distinctness",
    "sequence.usecase-step-coverage",
}


def _pending_method_proposals(sequence: dict) -> list[dict[str, Any]]:
    proposals = sequence.get("MethodProposals") if isinstance(sequence, dict) else None
    return [item for item in proposals or [] if isinstance(item, dict)]


def approved_method_proposal_ids(sequence: dict, feedback: str) -> set[str]:
    """Return proposal ids explicitly accepted in user feedback.

    The UI shows the IDs, but a human should not have to type one for every
    method.  Korean and English "approve all/add them" replies are therefore
    accepted, while negative wording is deliberately never treated as consent.
    """
    proposals = _pending_method_proposals(sequence)
    if not proposals or not str(feedback or "").strip():
        return set()
    compact = re.sub(r"\s+", "", str(feedback).lower())
    if any(token in compact for token in ("승인하지", "추가하지", "거절", "reject", "decline", "do-not-add")):
        return set()
    ids = {
        str(item.get("id") or "").strip()
        for item in proposals
        if str(item.get("id") or "").strip()
    }
    approval_words = ("승인", "approve", "accept", "추가해", "추가해주세요", "추가할게")
    all_words = ("모두", "전체", "all")
    if any(word in compact for word in approval_words) and any(
        word in compact for word in all_words
    ):
        return ids
    lowered = str(feedback).lower()
    return {proposal_id for proposal_id in ids if proposal_id.lower() in lowered}


def is_method_proposal_approval(sequence: dict, feedback: str) -> bool:
    """Whether feedback is a proposal approval rather than a diagram edit."""
    return bool(approved_method_proposal_ids(sequence, feedback))

def _sequence_diagrams(sequence: dict) -> list[dict]:
    diagrams = sequence.get("Diagrams") if isinstance(sequence, dict) else None
    if isinstance(diagrams, list):
        return [diagram for diagram in diagrams if isinstance(diagram, dict)]
    return [sequence]


def _expected_use_case_ids(state: dict) -> set[str]:
    specification = state.get("usecase_spec") or {}
    if not isinstance(specification, dict):
        return set()
    identifiers = {
        str(item.get("id") or "").strip()
        for item in specification.get("use_cases") or []
        if isinstance(item, dict) and item.get("id")
    }
    identifiers.update(
        str(item.get("use_case_id") or "").strip()
        for item in specification.get("use_case_specs") or []
        if isinstance(item, dict) and item.get("use_case_id")
    )
    return identifiers


def _class_methods(bce: dict) -> dict[str, set[str]]:
    return {
        str(item.get("className")): {
            name
            for method in item.get("methods") or []
            if (name := method_call_signature(str(method)))
        }
        for item in bce.get("Classes") or []
        if item.get("className")
    }


def _participant_classes(sequence: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for participant in sequence.get("Participants") or []:
        if str(participant.get("kind", "")).lower() == "actor":
            continue
        alias = str(participant.get("alias") or participant.get("name") or "").strip()
        class_name = str(
            participant.get("source_class") or participant.get("name") or ""
        ).strip()
        if alias and class_name:
            mapping[alias] = class_name
    return mapping


def _missing_methods(sequence: dict, bce: dict, *, strict: bool) -> dict[str, list[str]]:
    methods_by_class = _class_methods(bce)
    participant_classes = _participant_classes(sequence)
    missing: dict[str, list[str]] = {}
    invalid: list[str] = []

    for message in sequence.get("Messages") or []:
        if str(message.get("type", "sync")).lower() not in _CALL_TYPES:
            continue
        label = str(message.get("label") or "").strip()
        target = str(message.get("target") or "").strip()
        if not is_complete_method_call(label):
            invalid.append(f"{target}: {label or '<empty>'}")
            continue
        class_name = participant_classes.get(target)
        if not class_name or class_name not in methods_by_class:
            invalid.append(f"{target}: {label}")
            continue
        normalized = method_call_signature(label)
        if normalized not in methods_by_class[class_name]:
            labels = missing.setdefault(class_name, [])
            if label not in labels:
                labels.append(label)

    if strict and invalid:
        raise ValueError(
            "call messages must target a class-diagram class with a complete method: "
            + ", ".join(invalid)
        )
    return missing


def _merge_method_revision(
    bce: dict,
    revised: dict,
    editable_signatures: dict[str, set[str]],
    *,
    allow_grounded_additions: bool,
    forbidden_additions: set[tuple[str, str]],
) -> dict:
    """기존 메서드를 보존하고 대사 근거가 있는 변경만 채택한다.

    호출/반환 결함으로 지목된 시그니처만 수정할 수 있다. 미커버 단계가 있어 설계 LLM에
    메서드 필요 여부를 물은 경우에만 새 시그니처를 허용한다. 호출자가 이미 가진 메서드를
    수신자에게 복제해 잘못된 호출 방향을 합법화하는 제안은 별도로 차단한다.
    """

    def merge_methods(class_name: str, original: list, candidate: list) -> list:
        candidate_by_signature: dict[str, str] = {}
        candidate_order: list[str] = []
        for raw_method in candidate:
            method = str(raw_method)
            signature = method_call_signature(method)
            if not signature:
                continue
            if signature not in candidate_by_signature:
                candidate_order.append(signature)
            candidate_by_signature[signature] = method

        merged: list = []
        existing_signatures: set[str] = set()
        for raw_method in original:
            method = str(raw_method)
            signature = method_call_signature(method)
            if signature:
                existing_signatures.add(signature)
            if signature in editable_signatures.get(class_name, set()):
                merged.append(candidate_by_signature.get(signature, method))
            else:
                merged.append(method)

        for signature in candidate_order:
            if signature in existing_signatures:
                continue
            explicitly_requested = signature in editable_signatures.get(class_name, set())
            if not explicitly_requested and not allow_grounded_additions:
                continue
            if (class_name, signature) in forbidden_additions:
                continue
            merged.append(candidate_by_signature[signature])
            existing_signatures.add(signature)
        return merged

    revised_by_name = {
        str(item.get("className")): item
        for item in revised.get("Classes") or []
        if item.get("className")
    }
    classes: list[dict] = []
    for original in bce.get("Classes") or []:
        item = dict(original)
        class_name = str(item.get("className") or "")
        candidate = revised_by_name.get(class_name)
        if candidate and (
            class_name in editable_signatures or allow_grounded_additions
        ):
            item["methods"] = merge_methods(
                class_name,
                list(item.get("methods") or []),
                list(candidate.get("methods") or []),
            )
        classes.append(item)
    return {**bce, "Classes": classes}


def _class_patch(bce: dict) -> dict[str, Any]:
    puml = generate_plantuml_from_bce_json(bce)
    validation = validate_puml_artifact(puml)
    return {
        "extracted_bce_classes": bce,
        "class_diagram_puml": puml,
        "class_diagram_syntax_valid": validation["syntax_valid"],
        "class_diagram_syntax_errors": validation["syntax_errors"],
    }


def _persist_class_diagram(state: dict, patch: dict) -> None:
    app_id = state.get("app_id")
    if not app_id:
        return
    from app.db.models import ORIGIN_AUTO_FIXED
    from app.repositories.artifact_repository import save_stage

    save_stage(app_id, "class_diagram", {**state, **patch}, origin=ORIGIN_AUTO_FIXED)


def _reassembly_targets(sequence: dict, state: dict) -> set[str]:
    """Find UC cards whose fixed template must be reassembled."""
    targets: set[str] = set()
    for diagram in _sequence_diagrams(sequence):
        use_case_id = str(diagram.get("use_case_id") or "").strip()
        template_entry_unresolved = any(
            "boundary was not reached by a preceding actor interaction"
            in str(item.get("reason") or "").lower()
            for item in diagram.get("UnresolvedSteps") or []
            if isinstance(item, dict)
        )
        if use_case_id and any(
            finding.rule_id in _REASSEMBLY_RULE_IDS
            for finding in sequence_diagram_findings(diagram, state)
        ) or (use_case_id and template_entry_unresolved):
            targets.add(use_case_id)

    # Collection-level defects (for example a missing diagram) do not appear
    # while inspecting a single card.  Their locations contain the exact UC id.
    known = _expected_use_case_ids(state)
    for finding in sequence_diagram_findings(sequence, state):
        if finding.rule_id not in _REASSEMBLY_RULE_IDS:
            continue
        location = str(finding.location or "")
        targets.update(
            use_case_id
            for use_case_id in known
            if use_case_id and use_case_id in location
        )
    return targets


def _unresolved_contract_targets(diagrams: list[dict], bce: dict) -> set[str]:
    """Limit class-method proposals to the affected use-case route.

    An unresolved sequence step used to make the reviser inspect every class in
    the application.  That gave it unrelated Boundaries and Controls as
    plausible receivers, so a sign-in failure could produce suggestions on a
    schedule screen.  A proposal is useful only when it stays on the Boundary
    and Control path visible in the same use-case card.  When an incomplete
    card has not yet reached its Control, follow the declared Boundary relation
    one hop to recover that local Control candidate.
    """
    classes = {
        str(item.get("className") or "").strip(): item
        for item in bce.get("Classes") or []
        if isinstance(item, dict) and str(item.get("className") or "").strip()
    }
    neighbours: dict[str, set[str]] = {}
    for relationship in bce.get("Relationships") or []:
        if not isinstance(relationship, dict):
            continue
        source = str(relationship.get("source") or "").strip()
        target = str(relationship.get("target") or "").strip()
        if source in classes and target in classes:
            neighbours.setdefault(source, set()).add(target)
            neighbours.setdefault(target, set()).add(source)

    def stereotype_of(class_name: str) -> str:
        return (
            str(classes[class_name].get("stereotype") or "")
            .replace("<", "")
            .replace(">", "")
            .strip()
            .lower()
        )

    targets: set[str] = set()
    for diagram in diagrams:
        participant_classes = set(_participant_classes(diagram).values())
        local = {name for name in participant_classes if name in classes}
        boundaries = {
            name
            for name in local
            if stereotype_of(name) == "boundary"
        }
        controls = {
            name
            for name in local
            if stereotype_of(name) == "control"
        }
        for boundary in boundaries:
            controls.update(
                name
                for name in neighbours.get(boundary, set())
                if stereotype_of(name) == "control"
            )
        # Legacy/minimal models may omit stereotypes.  Their visible route is
        # still a safer scope than every BCE class.
        targets.update(boundaries or local)
        targets.update(controls)
    return targets


def _proposal_step_ids(findings: list[Any], fallback_use_case_ids: set[str]) -> list[str]:
    step_ids: set[str] = set()
    for finding in findings:
        location = str(getattr(finding, "location", "") or "")
        step_ids.update(
            re.findall(
                r"[A-Za-z][A-Za-z0-9_-]*:(?:main|extension):[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?",
                location,
            )
        )
    if step_ids:
        return sorted(step_ids)
    return [f"{use_case_id}:review" for use_case_id in sorted(fallback_use_case_ids)]


def _method_addition_proposals(
    original_bce: dict,
    candidate_bce: dict,
    editable_signatures: dict[str, set[str]],
    *,
    allow_grounded_additions: bool,
    forbidden_additions: set[tuple[str, str]],
    findings: list[Any],
    use_case_ids: set[str],
) -> list[dict[str, Any]]:
    """Extract only validated *new* class methods from an LLM recommendation."""
    merged = _merge_method_revision(
        original_bce,
        candidate_bce,
        editable_signatures,
        allow_grounded_additions=allow_grounded_additions,
        forbidden_additions=forbidden_additions,
    )
    original_methods = _class_methods(original_bce)
    by_class = {
        str(item.get("className") or "").strip(): item
        for item in merged.get("Classes") or []
        if isinstance(item, dict) and item.get("className")
    }
    evidence = next(
        (str(finding.message or "").strip() for finding in findings if finding.message),
        "현재 시퀀스 검증이 이 동작에 대응하는 클래스 메서드를 찾지 못했습니다.",
    )
    step_ids = _proposal_step_ids(findings, use_case_ids)
    proposals: list[dict[str, Any]] = []
    for class_name, item in sorted(by_class.items()):
        known = original_methods.get(class_name, set())
        for raw_method in item.get("methods") or []:
            method = str(raw_method).strip()
            signature = method_call_signature(method)
            if not signature or signature in known:
                continue
            proposals.append(
                {
                    "id": f"method:{class_name}:{signature}",
                    "class_name": class_name,
                    "method": method,
                    "reason": evidence,
                    "use_case_ids": sorted(use_case_ids),
                    "step_ids": step_ids,
                }
            )
            known.add(signature)
    return proposals


def _scope_proposal_evidence(
    proposals: list[dict[str, Any]],
    diagrams: list[dict],
    state: ArchitectureState,
    bce: dict,
) -> list[dict[str, Any]]:
    """Keep every proposed method tied to the UC route that justified it.

    The class reviser may examine several affected cards in one request for
    efficiency.  Its additions must nevertheless not inherit the complete set
    of findings from that request: doing so made one method proposal appear to
    solve unrelated use cases.  Associate a proposal only with cards where its
    receiver is on that card's visible Boundary/Control route.
    """
    scoped: list[dict[str, Any]] = []
    for proposal in proposals:
        class_name = str(proposal.get("class_name") or "").strip()
        related: list[tuple[str, list[Any]]] = []
        for diagram in diagrams:
            use_case_id = str(diagram.get("use_case_id") or "").strip()
            if not use_case_id or class_name not in _unresolved_contract_targets([diagram], bce):
                continue
            findings = [
                finding
                for finding in sequence_diagram_findings(diagram, state)
                if finding.rule_id
                in {
                    "sequence.usecase-step-coverage",
                    "sequence.actor-step-involvement",
                    "sequence.boundary-operation-direction",
                    "sequence.step-operation-distinctness",
                    "sequence.unresolved-usecase-step",
                }
            ]
            if findings:
                related.append((use_case_id, findings))
        if not related:
            # The class reviser may suggest a plausible method on an unrelated
            # route.  It has no evidence on the affected card, so retaining it
            # would present a misleading approval choice (for example, a search
            # method offered to repair a catalog-browse step).  Do not turn a
            # broad LLM guess into a cross-use-case class contract change.
            continue
        use_case_ids = {use_case_id for use_case_id, _ in related}
        findings = [finding for _, items in related for finding in items]
        evidence = next(
            (str(finding.message or "").strip() for finding in findings if finding.message),
            str(proposal.get("reason") or "").strip(),
        )
        scoped.append(
            {
                **proposal,
                "reason": evidence,
                "use_case_ids": sorted(use_case_ids),
                "step_ids": _proposal_step_ids(findings, use_case_ids),
            }
        )
    return scoped


def _apply_approved_method_proposals(
    bce: dict,
    proposals: list[dict[str, Any]],
    approved_ids: set[str],
) -> dict:
    """Apply exactly the additions the user explicitly approved."""
    selected = {
        str(item.get("id") or "").strip(): item
        for item in proposals
        if str(item.get("id") or "").strip() in approved_ids
    }
    if not selected:
        return bce
    classes: list[dict[str, Any]] = []
    for raw_class in bce.get("Classes") or []:
        item = dict(raw_class)
        class_name = str(item.get("className") or "").strip()
        existing = {
            method_call_signature(str(method))
            for method in item.get("methods") or []
            if method_call_signature(str(method))
        }
        methods = list(item.get("methods") or [])
        for proposal in selected.values():
            if str(proposal.get("class_name") or "").strip() != class_name:
                continue
            method = str(proposal.get("method") or "").strip()
            signature = method_call_signature(method)
            if signature and signature not in existing:
                methods.append(method)
                existing.add(signature)
        item["methods"] = methods
        classes.append(item)
    return {**bce, "Classes": classes}


def _legacy_reconcile_class_methods(state: ArchitectureState) -> dict:
    """Former sequence-to-class method proposal workflow (not invoked)."""
    sequence = state.get("sequence_diagram_model") or {}
    bce = state.get("extracted_bce_classes") or {}
    if not bce.get("Classes"):
        return {}

    # First use the fixed sequence template and the methods the user has
    # already approved.  This corrects legacy generic messages, missing traces,
    # lifecycle events, and participant ordering without changing the class
    # model or touching unaffected UC cards.
    result: dict[str, Any] = {}
    reassembly_targets = _reassembly_targets(sequence, state)
    working_sequence = sequence
    pending_proposals = _pending_method_proposals(sequence)
    approved_ids = approved_method_proposal_ids(
        sequence, str(state.get("sequence_diagram_feedback") or "")
    )
    if approved_ids:
        revised_bce = _apply_approved_method_proposals(
            bce, pending_proposals, approved_ids
        )
        if revised_bce != bce:
            result.update(_class_patch(revised_bce))
            approved_use_cases = {
                str(use_case_id).strip()
                for proposal in pending_proposals
                if str(proposal.get("id") or "").strip() in approved_ids
                for use_case_id in proposal.get("use_case_ids") or []
                if str(use_case_id).strip()
            }
            refreshed_targets = reassembly_targets | approved_use_cases
            if isinstance(working_sequence.get("Diagrams"), list) and refreshed_targets:
                working_sequence = reassemble_sequence_diagrams(
                    working_sequence,
                    state.get("usecase_spec"),
                    str(result["class_diagram_puml"]),
                    refreshed_targets,
                )
                working_sequence = {
                    **working_sequence,
                    "MethodProposals": [
                        proposal
                        for proposal in pending_proposals
                        if str(proposal.get("id") or "").strip() not in approved_ids
                    ],
                }
            else:
                working_sequence = {
                    **working_sequence,
                    "MethodProposals": [
                        proposal
                        for proposal in pending_proposals
                        if str(proposal.get("id") or "").strip() not in approved_ids
                    ],
                }
            result["sequence_diagram_model"] = working_sequence
            _persist_class_diagram(state, result)
            return result

    if isinstance(sequence.get("Diagrams"), list) and reassembly_targets:
        working_sequence = reassemble_sequence_diagrams(
            sequence,
            state.get("usecase_spec"),
            str(state.get("class_diagram_puml") or ""),
            reassembly_targets,
        )
        if working_sequence != sequence:
            result["sequence_diagram_model"] = working_sequence

    # An outstanding proposal remains a user decision.  Do not call the class
    # reviser again and produce a shifting second recommendation on every
    # refresh or unrelated feedback round.
    if pending_proposals:
        if working_sequence != sequence:
            result["sequence_diagram_model"] = {
                **working_sequence,
                "MethodProposals": pending_proposals,
            }
        return result

    diagrams = _sequence_diagrams(working_sequence)
    missing: dict[str, list[str]] = {}
    misplaced_calls: set[tuple[str, str]] = set()
    misplaced_return_locations: set[str] = set()
    return_findings = []
    method_need_findings = []
    for diagram in diagrams:
        participant_classes = _participant_classes(diagram)
        methods_by_class = _class_methods(bce)
        for class_name, labels in _missing_methods(
            diagram, bce, strict=False
        ).items():
            target_labels = missing.setdefault(class_name, [])
            target_labels.extend(label for label in labels if label not in target_labels)
        for message in diagram.get("Messages") or []:
            if str(message.get("type", "sync")).lower() not in _CALL_TYPES:
                continue
            source_class = participant_classes.get(
                str(message.get("source") or "").strip(), ""
            )
            target_class = participant_classes.get(
                str(message.get("target") or "").strip(), ""
            )
            signature = method_call_signature(str(message.get("label") or ""))
            if (
                source_class
                and target_class
                and signature
                and signature not in methods_by_class.get(target_class, set())
                and signature in methods_by_class.get(source_class, set())
            ):
                misplaced_calls.add((target_class, signature))
        calls = {
            str(message.get("call_id") or "").strip(): message
            for message in diagram.get("Messages") or []
            if str(message.get("type", "sync")).lower() in _CALL_TYPES
            and str(message.get("call_id") or "").strip()
        }
        for message in diagram.get("Messages") or []:
            if str(message.get("type", "")).lower() != "return":
                continue
            call = calls.get(str(message.get("reply_to") or "").strip())
            if call is None:
                continue
            source_class = participant_classes.get(
                str(call.get("source") or "").strip(), ""
            )
            target_class = participant_classes.get(
                str(call.get("target") or "").strip(), ""
            )
            signature = method_call_signature(str(call.get("label") or ""))
            returned_type = normalize_return_type(str(message.get("label") or ""))
            source_returns = {
                normalize_return_type(value)
                for item in bce.get("Classes") or []
                if str(item.get("className") or "") == source_class
                for raw_method in item.get("methods") or []
                if method_call_signature(str(raw_method)) == signature
                and (value := method_return_type(str(raw_method)))
            }
            target_returns = {
                normalize_return_type(value)
                for item in bce.get("Classes") or []
                if str(item.get("className") or "") == target_class
                for raw_method in item.get("methods") or []
                if method_call_signature(str(raw_method)) == signature
                and (value := method_return_type(str(raw_method)))
            }
            if (
                source_class
                and target_class
                and signature
                and returned_type in source_returns
                and returned_type not in target_returns
            ):
                misplaced_return_locations.add(
                    f"{message.get('source', '')} --> {message.get('target', '')} : "
                    f"{message.get('label', '') or '<empty>'}"
                )
        return_findings.extend(sequence_return_values_match_methods(diagram, state))
        method_need_findings.extend(
            finding
            for finding in sequence_diagram_findings(diagram, state)
            if finding.rule_id in {
                "sequence.usecase-step-coverage",
                "sequence.actor-step-involvement",
                "sequence.boundary-operation-direction",
                # Reusing one Boundary input for two independent actor actions
                # usually means the class contract is missing a distinct input
                # operation. Reassembly alone cannot create that operation.
                "sequence.step-operation-distinctness",
                # An unresolved step has no call to inspect, so the old
                # missing-receiver-method path never reached the class reviser.
                # It is still concrete evidence that the existing BCE contract
                # cannot express a stated use-case behavior. Ask for a minimal,
                # reviewable method proposal rather than leaving the same review
                # note on every sequence regeneration.
                "sequence.unresolved-usecase-step",
            }
        )

    return_findings = [
        finding
        for finding in return_findings
        if finding.location not in misplaced_return_locations
    ]

    # 같은 시그니처가 이미 다른 클래스에 있으면 클래스 누락이 아니라 수신자 배치
    # 오류일 가능성이 높다. 클래스를 복제해 오류를 숨기지 않고 시퀀스 수리에 맡긴다.
    missing_for_revision = {
        class_name: [
            label
            for label in labels
            if (class_name, method_call_signature(label)) not in misplaced_calls
        ]
        for class_name, labels in missing.items()
    }
    missing_for_revision = {
        class_name: labels
        for class_name, labels in missing_for_revision.items()
        if labels
    }
    if not missing_for_revision and not return_findings and not method_need_findings:
        return result

    editable_signatures: dict[str, set[str]] = {
        class_name: {
            signature
            for label in labels
            if (signature := method_call_signature(label))
        }
        for class_name, labels in missing_for_revision.items()
    }
    targets = set(editable_signatures)
    if return_findings:
        for diagram in diagrams:
            participant_classes = _participant_classes(diagram)
            calls = {
                str(message.get("call_id") or "").strip(): message
                for message in diagram.get("Messages") or []
                if str(message.get("type", "sync")).lower() in _CALL_TYPES
            }
            for message in diagram.get("Messages") or []:
                if str(message.get("type", "")).lower() != "return":
                    continue
                call = calls.get(str(message.get("reply_to") or "").strip())
                if call is None:
                    continue
                class_name = participant_classes.get(
                    str(call.get("target") or "").strip(), ""
                )
                signature = method_call_signature(str(call.get("label") or ""))
                if class_name and signature:
                    targets.add(class_name)
                    editable_signatures.setdefault(class_name, set()).add(signature)

    if method_need_findings:
        scoped_diagrams = [
            diagram
            for diagram in diagrams
            if any(
                finding.rule_id in {
                    "sequence.usecase-step-coverage",
                    "sequence.actor-step-involvement",
                    "sequence.boundary-operation-direction",
                    "sequence.step-operation-distinctness",
                    "sequence.unresolved-usecase-step",
                }
                for finding in sequence_diagram_findings(diagram, state)
            )
        ]
        scoped_targets = _unresolved_contract_targets(scoped_diagrams, bce)
        if scoped_targets:
            targets.update(scoped_targets)
        elif not targets:
            # There is no visible Boundary/Control route to which a new method
            # can be attached.  Calling the class reviser with an empty target
            # set would make it revise the full application model and recreate
            # the unrelated-method proposals this scope guard exists to avoid.
            # Keep the step unresolved until a route is present instead.
            return result

    issues = [
        *(
            f"{class_name}: missing call signature {label}"
            for class_name, labels in missing_for_revision.items()
            for label in labels
        ),
        *(finding.as_issue() for finding in return_findings),
        *(finding.as_issue() for finding in method_need_findings),
    ]
    direction_feedback = ""
    if any(
        finding.rule_id == "sequence.boundary-operation-direction"
        for finding in method_need_findings
    ):
        direction_feedback = (
            " A Boundary output operation (display/show/render/prompt/notify) cannot "
            "represent the actor input step named in that finding. Inspect the exact "
            "use-case action. If no existing Boundary input/event method semantically "
            "represents it, you MUST add the minimum grounded input method to the most "
            "appropriate existing Boundary; do not leave the output method as a substitute."
        )
    unresolved_feedback = ""
    if any(
        finding.rule_id == "sequence.unresolved-usecase-step"
        for finding in method_need_findings
    ):
        unresolved_feedback = (
            " An unresolved step means no grounded class operation could express a "
            "behavior explicitly stated in the use case. Propose the smallest "
            "necessary method on an existing Boundary or Control, with an exact "
            "signature and return type. Preserve the unresolved step if the scenario "
            "does not determine a safe method; never reuse a generic operation just "
            "to make the diagram appear complete."
        )
    distinct_input_feedback = ""
    if any(
        finding.rule_id == "sequence.step-operation-distinctness"
        for finding in method_need_findings
    ):
        distinct_input_feedback = (
            " Different actor input steps reported as sharing one Boundary call "
            "must not be repaired by reusing that generic method. Add a separate, "
            "grounded Boundary input method only when the stated user actions are "
            "semantically distinct."
        )
    feedback = (
        "Review these sequence-to-class contract issues against the use-case specification:\n- "
        + "\n- ".join(issues)
        + "\nFor each proposed method, decide whether the use case genuinely requires it. "
        "Only when grounded, add or correct that method on the named receiver class, "
        "using a complete signature and a return type when a result is used. If a "
        "proposal is unsupported, leave the class unchanged; the sequence repair stage "
        "will remove or remap that call. For an uncovered use-case step, first decide "
        "whether an existing method can represent it; add the minimum required method "
        "only if no existing method can. If the caller already owns a missing call's "
        "signature, do not copy it to the receiver; leave it for sequence remapping. "
        "Preserve every field, relationship, and unrelated method."
        + direction_feedback
        + unresolved_feedback
        + distinct_input_feedback
    )
    proposed_bce = revise_bce_classes(
        current_bce=bce,
        feedback=feedback,
        scenario_text=usecase_spec_text(state),
        targets=targets,
    )
    proposal_use_case_ids = reassembly_targets or {
        str(diagram.get("use_case_id") or "").strip()
        for diagram in diagrams
        if str(diagram.get("use_case_id") or "").strip()
    }
    proposals = _method_addition_proposals(
        bce,
        proposed_bce,
        editable_signatures,
        allow_grounded_additions=bool(method_need_findings),
        forbidden_additions=misplaced_calls,
        findings=[*method_need_findings, *return_findings],
        use_case_ids=proposal_use_case_ids,
    )
    if method_need_findings:
        proposals = _scope_proposal_evidence(proposals, scoped_diagrams, state, bce)
    if proposals:
        result["sequence_diagram_model"] = {
            **working_sequence,
            "MethodProposals": proposals,
        }
        return result

    # Correcting a return declaration on an already-existing operation is not
    # a new capability.  Preserve the existing automatic contract correction;
    # only *added* methods require the user's approval.
    revised_bce = _merge_method_revision(
        bce,
        proposed_bce,
        editable_signatures,
        allow_grounded_additions=bool(method_need_findings),
        forbidden_additions=misplaced_calls,
    )
    if revised_bce != bce:
        result.update(_class_patch(revised_bce))
        if isinstance(working_sequence.get("Diagrams"), list):
            result["sequence_diagram_model"] = {
                **working_sequence,
                "class_diagram_hash": hashlib.sha256(
                    result["class_diagram_puml"].encode("utf-8")
                ).hexdigest(),
            }
        _persist_class_diagram(state, result)

    return result


def reconcile_class_methods(state: ArchitectureState) -> dict:
    """Compatibility entry point for explicit legacy reconciliation callers.

    Generation graphs no longer register this function: sequence generation is
    one-way from the accepted class model.  Retaining the callable avoids
    breaking persisted-artifact maintenance paths while keeping that legacy
    workflow out of normal generation and feedback execution.
    """
    return _legacy_reconcile_class_methods(state)


def ensure_sequence_class_methods(state: ArchitectureState) -> dict:
    """렌더 직전 모든 시퀀스 의미 계약을 강제하는 최종 장벽."""
    sequence = state.get("sequence_diagram_model") or {}
    bce = state.get("extracted_bce_classes") or {}
    expected_class_hash = str(sequence.get("class_diagram_hash") or "").strip()
    if expected_class_hash:
        actual_class_hash = hashlib.sha256(
            str(state.get("class_diagram_puml") or "").encode("utf-8")
        ).hexdigest()
        if expected_class_hash != actual_class_hash:
            raise ValueError(
                "sequence diagram was generated from a different class diagram version"
            )
    if isinstance(sequence.get("Diagrams"), list):
        expected = _expected_use_case_ids(state)
        actual = [
            str(diagram.get("use_case_id") or "").strip()
            for diagram in sequence.get("Diagrams") or []
            if isinstance(diagram, dict)
        ]
        if expected and (set(actual) != expected or len(actual) != len(set(actual))):
            raise ValueError(
                "sequence diagrams must contain exactly one diagram per use case: "
                f"expected {sorted(expected)}, got {actual}"
            )
    if not bce.get("Classes"):
        if any(
            str(message.get("type", "sync")).lower() in _CALL_TYPES
            for diagram in _sequence_diagrams(sequence)
            for message in diagram.get("Messages") or []
        ):
            raise ValueError("cannot validate call messages without a class diagram")
        return {}
    diagrams = _sequence_diagrams(sequence)
    missing: dict[str, list[str]] = {}
    for diagram in diagrams:
        for class_name, labels in _missing_methods(
            diagram, bce, strict=True
        ).items():
            target_labels = missing.setdefault(class_name, [])
            target_labels.extend(label for label in labels if label not in target_labels)
    if missing:
        raise ValueError(
            "call messages must exactly match receiver class method signatures: "
            + "; ".join(
                f"{class_name}: {', '.join(labels)}"
                for class_name, labels in missing.items()
            )
        )
    contract_findings = sequence_diagram_findings(sequence, state)
    is_new_contract = bool(expected_class_hash) or any(
        "call_id" in message or "reply_to" in message
        for diagram in diagrams
        for message in diagram.get("Messages") or []
        if isinstance(message, dict)
    )
    if not is_new_contract:
        legacy_final_rules = {
            "sequence.unmatched-return-message",
            "sequence.async-call-has-no-return",
            "sequence.return-label-matches-method-return",
            "sequence.nonvoid-call-requires-return",
            "sequence.causal-call-chain",
            "sequence.fragment-condition-consistency",
        }
        contract_findings = [
            finding for finding in contract_findings if finding.rule_id in legacy_final_rules
        ]
    if contract_findings:
        raise ValueError(
            "sequence interaction contracts remain invalid: "
            + "; ".join(finding.message for finding in contract_findings)
        )
    return {}


def finalize_sequence_class_methods(state: ArchitectureState) -> dict:
    """Turn the strict final contract into a render decision instead of a 502.

    ``ensure_sequence_class_methods`` remains the strict reusable assertion.  The
    graph-facing wrapper preserves an invalid structured model and its deterministic
    findings so the gate can ask for repair, while preventing that model from being
    rendered as if it were approved.
    """
    report = dict(state.get("sequence_diagram_check") or {})
    findings = list(report.get("findings") or [])
    explicit_unresolved = any(
        isinstance(diagram, dict)
        and any(
            isinstance(step, dict)
            for step in diagram.get("UnresolvedSteps", []) or []
        )
        for diagram in _sequence_diagrams(
            state.get("sequence_diagram_model") or {}
        )
    )
    only_explicit_unresolved = explicit_unresolved and all(
        "[sequence.unresolved-usecase-step]" in str(finding)
        for finding in findings
    )
    try:
        ensure_sequence_class_methods(state)
    except ValueError as exc:
        if only_explicit_unresolved:
            # The diagram is intentionally a visible review artifact, not an
            # approved interaction.  Hiding it would make this UC disappear
            # again even though the model records exactly why it is incomplete.
            return {
                "sequence_diagram_renderable": True,
                "sequence_diagram_check": report,
            }
        if not findings:
            findings.append(f"{exc} [sequence.final-contract]")
            report.update(
                {
                    "findings": findings,
                    "repair_iters": int(report.get("repair_iters") or 0),
                    "stopped": report.get("stopped") or "checked_only",
                }
            )
        return {
            "sequence_diagram_renderable": False,
            "sequence_diagram_check": report,
        }
    if findings:
        if only_explicit_unresolved:
            return {
                "sequence_diagram_renderable": True,
                "sequence_diagram_check": report,
            }
        # Legacy models can pass the strict compatibility subset while the current
        # deterministic checker still reports defects.  Findings, not model age,
        # decide whether an image may be exposed.
        return {
            "sequence_diagram_renderable": False,
            "sequence_diagram_check": report,
        }
    return {"sequence_diagram_renderable": True}
