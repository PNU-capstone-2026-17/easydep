"""시퀀스 호출과 클래스 메서드를 대사해 두 산출물의 일관성을 보장한다."""
from __future__ import annotations

from typing import Any

from app.design.knowledge.detectors import (
    sequence_return_values_match_methods,
    sequence_usecase_coverage,
)
from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.reviser import revise_bce_classes
from app.design.services.common.validation import validate_puml_artifact
from app.design.services.sequence_diagram.extractor import extract_sequence_model
from app.design.services.sequence_diagram.methods import (
    is_complete_method_call,
    is_return_value_label,
    method_name,
    method_return_type,
)


_CALL_TYPES = {"sync", "async", "self"}


def _class_methods(bce: dict) -> dict[str, set[str]]:
    return {
        str(item.get("className")): {
            name
            for method in item.get("methods") or []
            if (name := method_name(str(method)))
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
        normalized = method_name(label)
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


def _add_methods(bce: dict, missing: dict[str, list[str]]) -> dict:
    updated = dict(bce)
    classes: list[dict] = []
    for original in bce.get("Classes") or []:
        item = dict(original)
        additions = missing.get(str(item.get("className")), [])
        if additions:
            existing = list(item.get("methods") or [])
            existing_names = {method_name(str(method)) for method in existing}
            for method in additions:
                if method_name(method) not in existing_names:
                    existing.append(method)
                    existing_names.add(method_name(method))
            item["methods"] = existing
        classes.append(item)
    updated["Classes"] = classes
    return updated


def _paired_return_types(sequence: dict) -> dict[tuple[str, str], set[str]]:
    """(호출 수신 alias, 메서드명)별 실제 반환 메시지 라벨."""
    pending_calls: list[dict] = []
    paired: dict[tuple[str, str], set[str]] = {}
    for message in sequence.get("Messages") or []:
        message_type = str(message.get("type", "sync")).strip().lower()
        source = str(message.get("source") or "").strip()
        target = str(message.get("target") or "").strip()
        if message_type in _CALL_TYPES:
            pending_calls.append(message)
            continue
        if message_type != "return":
            continue
        call_index = next(
            (
                index
                for index in range(len(pending_calls) - 1, -1, -1)
                if str(pending_calls[index].get("source") or "").strip() == target
                and str(pending_calls[index].get("target") or "").strip() == source
            ),
            None,
        )
        if call_index is None:
            continue
        call = pending_calls.pop(call_index)
        label = str(message.get("label") or "").strip()
        name = method_name(str(call.get("label") or ""))
        receiver = str(call.get("target") or "").strip()
        if name and is_return_value_label(label):
            paired.setdefault((receiver, name), set()).add(label)
    return paired


def _add_missing_return_types(sequence: dict, bce: dict) -> dict:
    """반환이 필요한데 클래스 메서드에 타입이 없으면 반환 라벨 타입을 선언한다."""
    participant_classes = _participant_classes(sequence)
    requested: dict[tuple[str, str], set[str]] = {}
    for (receiver, name), labels in _paired_return_types(sequence).items():
        class_name = participant_classes.get(receiver)
        if class_name:
            requested.setdefault((class_name, name), set()).update(labels)

    updated = dict(bce)
    classes: list[dict] = []
    for original in bce.get("Classes") or []:
        item = dict(original)
        class_name = str(item.get("className") or "")
        methods: list[str] = []
        for raw in item.get("methods") or []:
            raw_text = str(raw)
            labels = requested.get((class_name, method_name(raw_text)), set())
            # 서로 다른 반환 라벨이 경쟁하면 여기서 하나를 고르지 않는다. 검출기가
            # 불일치를 드러내고 시퀀스 수리기가 하나의 클래스 계약으로 정리한다.
            if method_return_type(raw_text) is None and len(labels) == 1:
                raw_text = f"{raw_text}: {next(iter(labels))}"
            methods.append(raw_text)
        item["methods"] = methods
        classes.append(item)
    updated["Classes"] = classes
    return updated


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


def _ensure_declared_methods(
    state: ArchitectureState,
    sequence: dict,
    bce: dict,
    *,
    strict: bool,
    persist: bool = True,
) -> dict:
    missing = _missing_methods(sequence, bce, strict=strict)
    updated_bce = _add_methods(bce, missing)
    updated_bce = _add_missing_return_types(sequence, updated_bce)
    if updated_bce == bce:
        return {}
    patch = _class_patch(updated_bce)
    if persist:
        _persist_class_diagram(state, patch)
    return patch


def reconcile_class_methods(state: ArchitectureState) -> dict:
    """누락 호출 메서드를 클래스에 추가하고, 미표현 흐름이면 클래스부터 보강한다."""
    sequence = state.get("sequence_diagram_model") or {}
    bce = state.get("extracted_bce_classes") or {}
    if not bce.get("Classes"):
        return {}

    patch = _ensure_declared_methods(state, sequence, bce, strict=False, persist=False)
    working_bce = patch.get("extracted_bce_classes", bce)
    working_state = {**state, **patch, "sequence_diagram_model": sequence}
    uncovered = sequence_usecase_coverage(sequence, working_state)
    if not uncovered:
        if patch:
            _persist_class_diagram(state, patch)
        return patch

    feedback = (
        "The sequence diagram cannot represent these required use-case flow steps because "
        "the class diagram lacks suitable operations:\n- "
        + "\n- ".join(finding.as_issue() for finding in uncovered)
        + "\nAdd the minimum necessary, use-case-grounded methods to the appropriate receiver "
        "classes. Preserve all existing classes, fields, methods, and relationships. "
        "Write every method as an ASCII identifier followed by parentheses. When a "
        "caller uses its result, append the grounded return type as ': ReturnType'."
    )
    revised_bce = revise_bce_classes(
        current_bce=working_bce,
        feedback=feedback,
        scenario_text=usecase_spec_text(state),
        targets=set(),
    )
    class_patch = _class_patch(revised_bce)
    revised_sequence = extract_sequence_model(
        usecase_spec_text(state), class_patch["class_diagram_puml"]
    )
    final_patch = _ensure_declared_methods(
        state, revised_sequence, revised_bce, strict=True, persist=False
    )
    result = {**class_patch, **final_patch, "sequence_diagram_model": revised_sequence}
    _persist_class_diagram(state, result)
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
    patch = _ensure_declared_methods(state, sequence, bce, strict=True)
    checked_state = {**state, **patch}
    return_findings = sequence_return_values_match_methods(sequence, checked_state)
    if return_findings:
        raise ValueError(
            "return messages must match class method return types: "
            + "; ".join(finding.message for finding in return_findings)
        )
    return patch
