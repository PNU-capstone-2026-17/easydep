"""시퀀스-클래스 다이어그램 대사 — 시퀀스가 참조하는 메서드를 클래스에 보강한다.

시퀀스 다이어그램이 만들어진 직후, 클래스 다이어그램과 **대사**(reconciliation)한다.
시퀀스 메시지가 호출하는 메서드가 클래스 다이어그램의 BCE 모델에 없으면, 클래스
다이어그램을 자동으로 보강하여 두 산출물의 일관성을 보장한다.

두 가지 경우를 처리한다:

  1. **시퀀스 메시지가 비어 있고 클래스에 메서드가 없음** — 클래스가 존재하되 메서드가
     없어서 시퀀스를 뽑지 못한 것이다. LLM으로 유스케이스 명세 기반 메서드를 보강한 뒤
     시퀀스를 재추출한다.
  2. **메시지 라벨이 클래스 메서드에 없음** — 해당 메서드를 결정론적으로 클래스에 추가한다.
     LLM을 부르지 않아 빠르고 예측 가능하다.

어느 쪽이든 클래스 다이어그램을 수정하면 PlantUML을 다시 렌더하고 저장소에 반영한다.
생성과 피드백 양쪽에서 동작한다 — 피드백이 새 메시지를 추가해도 클래스가 따라온다.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.reviser import revise_bce_classes
from app.design.services.common.validation import validate_puml_artifact
from app.design.services.sequence_diagram.extractor import extract_sequence_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------
def _normalize_method_name(raw: str) -> str:
    """메서드 이름을 비교 가능한 형태로 정규화한다.

    가시성 기호(+/-/#/~)와 매개변수, 반환 타입을 벗기고 이름만 소문자로 남긴다.
    detectors.py의 같은 이름 함수와 동일한 규칙이지만, 의존 방향을 역전시키지 않기
    위해 여기에 둔다.
    """
    raw = re.sub(r'^[+\-#~]\s*', '', raw)
    match = re.match(r'([A-Za-z_]\w*)', raw)
    return match.group(1).lower() if match else raw.lower().strip()


def _class_methods_map(bce: dict) -> dict[str, set[str]]:
    """BCE 모델에서 클래스별 정규화된 메서드 이름 집합."""
    result: dict[str, set[str]] = {}
    for c in bce.get("Classes", []):
        name = c.get("className")
        if not name:
            continue
        methods: set[str] = set()
        for m in c.get("methods") or []:
            norm = _normalize_method_name(str(m).strip())
            if norm:
                methods.add(norm)
        result[name] = methods
    return result


def _participant_class_map(seq_model: dict) -> dict[str, str]:
    """참가자 이름 → 대응 클래스 이름. 액터는 제외."""
    mapping: dict[str, str] = {}
    for p in seq_model.get("Participants", []):
        name = str(p.get("name", "")).strip()
        kind = str(p.get("kind", "")).strip().lower()
        if kind == "actor" or not name:
            continue
        class_ref = str(p.get("source_class", "")).strip() or name
        mapping[name] = class_ref
    return mapping


def _find_missing_methods(
    messages: list[dict],
    participant_to_class: dict[str, str],
    class_methods: dict[str, set[str]],
) -> dict[str, list[str]]:
    """시퀀스 메시지가 참조하지만 BCE에 없는 메서드를 찾는다.

    반환: {클래스명: [빠진 메서드 라벨, ...]}
    """
    missing: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()

    for msg in messages:
        if str(msg.get("type", "sync")).lower() == "return":
            continue
        label = str(msg.get("label", "")).strip()
        if not label:
            continue
        target = str(msg.get("target", "")).strip()
        target_class = participant_to_class.get(target)
        if not target_class or target_class not in class_methods:
            continue

        norm = _normalize_method_name(label)
        if norm and norm not in class_methods[target_class]:
            key = (target_class, norm)
            if key not in seen:
                seen.add(key)
                missing.setdefault(target_class, []).append(label)

    return missing


def _add_methods_to_bce(bce: dict, missing: dict[str, list[str]]) -> dict:
    """BCE 모델의 클래스에 빠진 메서드를 결정론적으로 추가한다.

    괄호가 없는 이름에는 ``()``를 붙여 메서드 형태를 갖춘다.
    """
    updated = dict(bce)
    updated_classes = []
    for c in bce.get("Classes", []):
        c = dict(c)
        name = c.get("className")
        if name and name in missing:
            existing = list(c.get("methods") or [])
            for method_label in missing[name]:
                if "(" not in method_label:
                    method_label = f"{method_label}()"
                existing.append(method_label)
            c["methods"] = existing
        updated_classes.append(c)
    updated["Classes"] = updated_classes
    return updated


def _classes_lack_methods(bce: dict) -> bool:
    """클래스가 있는데 메서드가 전혀 없는가."""
    classes = bce.get("Classes", [])
    if not classes:
        return False
    return sum(len(c.get("methods") or []) for c in classes) == 0


def _render_class_diagram(bce: dict) -> dict[str, Any]:
    """BCE → PlantUML + 검증 결과."""
    puml = generate_plantuml_from_bce_json(bce)
    validation = validate_puml_artifact(puml)
    return {
        "class_diagram_puml": puml,
        "class_diagram_syntax_valid": validation["syntax_valid"],
        "class_diagram_syntax_errors": validation["syntax_errors"],
    }


def _persist_class_diagram_if_needed(app_id: str | None, state: dict) -> None:
    """클래스 다이어그램 변경을 저장소에 반영한다.

    이 노드는 시퀀스 다이어그램 서브그래프 안에서 동작하므로, 시퀀스의 persist 노드는
    시퀀스만 저장한다. 클래스 다이어그램 변경은 여기서 직접 저장해야 한다 — cascade.py가
    하류 스테이지를 직접 저장하는 것과 같은 이유다.
    """
    if not app_id:
        return
    from app.db.models import ORIGIN_AUTO_FIXED
    from app.repositories import artifact_repository

    artifact_repository.save_stage(
        app_id, "class_diagram", state, origin=ORIGIN_AUTO_FIXED,
    )


# ---------------------------------------------------------------------------
# 노드 함수
# ---------------------------------------------------------------------------
def reconcile_class_methods(state: ArchitectureState) -> dict:
    """시퀀스 모델이 참조하는 메서드 중 클래스 다이어그램에 없는 것을 보강한다.

    **Case 1 — 메시지가 비어 있고 클래스에 메서드가 없음:**
    클래스가 존재하되 메서드가 없으면 시퀀스 추출기가 호출할 것이 없어 빈 메시지를
    내놓기 쉽다. LLM에게 유스케이스 명세 기반 메서드 보강을 요청한 뒤 시퀀스를
    재추출한다.

    **Case 2 — 메시지 라벨이 클래스 메서드에 없음:**
    시퀀스 추출기가 클래스 다이어그램에 없는 오퍼레이션을 호출한 경우다. 해당 메서드를
    결정론적으로 BCE에 추가한다. LLM을 부르지 않는다.

    어느 쪽이든 클래스 다이어그램을 수정하면 PlantUML을 다시 렌더하고 저장소에 반영한다.
    문제가 없으면 빈 dict를 반환해 상태를 건드리지 않는다.
    """
    seq_model = state.get("sequence_diagram_model") or {}
    bce = state.get("extracted_bce_classes") or {}
    messages = seq_model.get("Messages", [])
    app_id = state.get("app_id")

    if not bce.get("Classes"):
        return {}

    participant_to_class = _participant_class_map(seq_model)
    class_methods = _class_methods_map(bce)

    # ── Case 1: 메시지가 비어 있고 클래스에 메서드가 부족 ───────────────────
    if not messages and _classes_lack_methods(bce):
        logger.info(
            "[reconcile] 시퀀스 메시지가 비어 있고 클래스에 메서드가 없음 → LLM으로 보강"
        )
        try:
            feedback = (
                "The classes currently have no methods. Based on the use case "
                "specification, add appropriate methods to each class so that a "
                "sequence diagram can be derived from them. Each Boundary class "
                "should have UI-facing methods, each Control class should have "
                "coordination/business-logic methods, and each Entity class should "
                "have data-access methods. Name them as verbNoun()."
            )
            revised_bce = revise_bce_classes(
                current_bce=bce,
                feedback=feedback,
                scenario_text=usecase_spec_text(state),
                targets=set(),
            )
            rendered = _render_class_diagram(revised_bce)
            patch: dict[str, Any] = {"extracted_bce_classes": revised_bce, **rendered}

            # 보강된 클래스로 시퀀스 재추출
            new_seq = extract_sequence_model(
                usecase_spec_text(state), rendered["class_diagram_puml"],
            )
            patch["sequence_diagram_model"] = new_seq

            # 클래스 다이어그램 저장
            temp_state = dict(state)
            temp_state.update(patch)
            _persist_class_diagram_if_needed(app_id, temp_state)

            return patch
        except Exception:
            logger.exception("[reconcile] 메서드 보강 실패 — 원본 유지")
            return {}

    # ── Case 2: 메시지 라벨이 클래스 메서드에 없음 ─────────────────────────
    missing = _find_missing_methods(messages, participant_to_class, class_methods)
    if not missing:
        return {}

    logger.info("[reconcile] 빠진 메서드 발견: %s → 클래스 다이어그램에 추가", missing)
    updated_bce = _add_methods_to_bce(bce, missing)
    rendered = _render_class_diagram(updated_bce)
    patch = {"extracted_bce_classes": updated_bce, **rendered}

    # 클래스 다이어그램 저장
    temp_state = dict(state)
    temp_state.update(patch)
    _persist_class_diagram_if_needed(app_id, temp_state)

    return patch
