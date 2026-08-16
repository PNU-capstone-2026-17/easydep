"""시퀀스 호출과 클래스 메서드를 대사해 두 산출물의 일관성을 보장한다."""
from __future__ import annotations

import hashlib
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


_CALL_TYPES = {"sync", "async", "self"}

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


def reconcile_class_methods(state: ArchitectureState) -> dict:
    """LLM이 유스케이스 근거를 검토한 뒤에만 클래스 메서드를 수정한다."""
    sequence = state.get("sequence_diagram_model") or {}
    bce = state.get("extracted_bce_classes") or {}
    if not bce.get("Classes"):
        return {}

    diagrams = _sequence_diagrams(sequence)
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
        return {}

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
        targets.update(
            str(item.get("className") or "").strip()
            for item in bce.get("Classes") or []
            if item.get("className")
        )
        targets.discard("")

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
    )
    proposed_bce = revise_bce_classes(
        current_bce=bce,
        feedback=feedback,
        scenario_text=usecase_spec_text(state),
        targets=targets,
    )
    revised_bce = _merge_method_revision(
        bce,
        proposed_bce,
        editable_signatures,
        allow_grounded_additions=bool(method_need_findings),
        forbidden_additions=misplaced_calls,
    )
    result: dict[str, Any] = {}
    if revised_bce != bce:
        result.update(_class_patch(revised_bce))
        if isinstance(sequence.get("Diagrams"), list):
            result["sequence_diagram_model"] = {
                **sequence,
                "class_diagram_hash": hashlib.sha256(
                    result["class_diagram_puml"].encode("utf-8")
                ).hexdigest(),
            }
        _persist_class_diagram(state, result)

    return result


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
    try:
        ensure_sequence_class_methods(state)
    except ValueError as exc:
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
        # Legacy models can pass the strict compatibility subset while the current
        # deterministic checker still reports defects.  Findings, not model age,
        # decide whether an image may be exposed.
        return {
            "sequence_diagram_renderable": False,
            "sequence_diagram_check": report,
        }
    return {"sequence_diagram_renderable": True}
