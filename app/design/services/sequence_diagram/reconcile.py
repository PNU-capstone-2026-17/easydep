"""시퀀스 호출과 클래스 메서드를 대사해 두 산출물의 일관성을 보장한다."""
from __future__ import annotations

from typing import Any

from app.design.knowledge.detectors import (
    sequence_async_returns,
    sequence_return_values_match_methods,
    sequence_unmatched_returns,
    sequence_usecase_coverage,
)
from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.reviser import revise_bce_classes
from app.design.services.common.validation import validate_puml_artifact
from app.design.services.sequence_diagram.extractor import extract_sequence_model
from app.design.services.sequence_diagram.methods import (
    is_complete_method_call,
    method_call_signature,
)


_CALL_TYPES = {"sync", "async", "self"}


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


def _merge_method_revision(bce: dict, revised: dict, targets: set[str]) -> dict:
    """LLM 수정본에서 지정 클래스의 methods만 채택한다."""
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
        if candidate and (not targets or class_name in targets):
            item["methods"] = list(candidate.get("methods") or [])
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

    missing = _missing_methods(sequence, bce, strict=False)
    return_findings = sequence_return_values_match_methods(sequence, state)
    uncovered = sequence_usecase_coverage(sequence, state)
    if not missing and not return_findings and not uncovered:
        return {}

    participant_classes = _participant_classes(sequence)
    targets = set(missing)
    if return_findings:
        targets.update(
            participant_classes.get(str(message.get("source") or "").strip(), "")
            for message in sequence.get("Messages") or []
            if str(message.get("type", "")).lower() == "return"
        )
        targets.discard("")

    issues = [
        *(
            f"{class_name}: missing call signature {label}"
            for class_name, labels in missing.items()
            for label in labels
        ),
        *(finding.as_issue() for finding in return_findings),
        *(finding.as_issue() for finding in uncovered),
    ]
    feedback = (
        "Review these sequence-to-class contract issues against the use-case specification:\n- "
        + "\n- ".join(issues)
        + "\nFor each proposed method, decide whether the use case genuinely requires it. "
        "Only when grounded, add or correct that method on the named receiver class, "
        "using a complete signature and a return type when a result is used. If a "
        "proposal is unsupported, leave the class unchanged; the sequence repair stage "
        "will remove or remap that call. Preserve every field and relationship."
    )
    proposed_bce = revise_bce_classes(
        current_bce=bce,
        feedback=feedback,
        scenario_text=usecase_spec_text(state),
        targets=targets,
    )
    revised_bce = _merge_method_revision(bce, proposed_bce, targets)
    result: dict[str, Any] = {}
    if revised_bce != bce:
        result.update(_class_patch(revised_bce))
        _persist_class_diagram(state, result)

    if uncovered:
        class_puml = result.get("class_diagram_puml") or state.get("class_diagram_puml", "")
        result["sequence_diagram_model"] = extract_sequence_model(
            usecase_spec_text(state), class_puml
        )
    return result


def ensure_sequence_class_methods(state: ArchitectureState) -> dict:
    """렌더 직전 호출 소유권과 반환 타입 계약을 강제하는 최종 장벽."""
    sequence = state.get("sequence_diagram_model") or {}
    bce = state.get("extracted_bce_classes") or {}
    if not bce.get("Classes"):
        if any(
            str(message.get("type", "sync")).lower() in _CALL_TYPES
            for message in sequence.get("Messages") or []
        ):
            raise ValueError("cannot validate call messages without a class diagram")
        return {}
    missing = _missing_methods(sequence, bce, strict=True)
    if missing:
        raise ValueError(
            "call messages must exactly match receiver class method signatures: "
            + "; ".join(
                f"{class_name}: {', '.join(labels)}"
                for class_name, labels in missing.items()
            )
        )
    contract_findings = [
        *sequence_unmatched_returns(sequence, state),
        *sequence_async_returns(sequence, state),
        *sequence_return_values_match_methods(sequence, state),
    ]
    if contract_findings:
        raise ValueError(
            "sequence call/return contracts remain invalid: "
            + "; ".join(finding.message for finding in contract_findings)
        )
    return {}
