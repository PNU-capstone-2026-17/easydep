"""입력 계약의 검증 — 스키마가 못 보는 것을 여기서 본다.

JSON Schema는 모양만 본다. **계약의 핵심 약속 — 조인은 명시 id로만 — 은 참조
검증**이라 스키마 밖이다: `componentId`가 실재하는 컴포넌트를 가리키는지,
시퀀스 메시지의 from/to가 실재하는 participant인지.

문제를 예외가 아니라 **목록으로** 돌려준다. 상류는 에이전트다 — 첫 오류에서
멈추면 고치고 다시, 고치고 다시를 반복하게 된다. 한 번에 다 보여준다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

_SCHEMA_PATH = Path(__file__).with_name("schema.json")
_REQUEST_SCHEMA_PATH = Path(__file__).with_name("request.json")

#: 산출물 kind → schema.json `artifacts.items.oneOf`의 분기 순번.
#: **스키마의 oneOf 순서와 함께 움직여야 한다** — 테스트가 넷 다 물어 고정한다.
_KIND_BRANCH = {"openapi": 0, "er": 1, "class": 2, "sequence": 3}

#: **`SCALE_FIELDS`는 2026-08-01에 사라졌다.** 규모가 두 칸이던 동안에는 "둘 중
#: 하나면 된다"를 부르는 쪽에 알려 줄 목록이 필요했다. 한 칸(`scale`)이 되자 그
#: 목록이 원소 하나짜리가 됐고, **읽는 곳을 세어 보니 0곳이었다** — 두 칸을 한
#: 칸으로 접는 변경이 상수 자체를 함께 지웠다. 규모를 읽는 곳은 스키마의 `scale`을
#: 직접 본다.

#: **질문 문구·근거·계층은 여기 없다**(2026-08-01). 그것들은 `app/core/input_registry.py`가
#: 항목마다 들고 있고, 여기 사본을 두면 두 곳이 갈린다 — 이 저장소가 stages·프롬프트·
#: 인용에서 반복해서 물린 자리다. 이 모듈은 **모양 검증**만 한다:
#: 무엇이 필수인가는 `request.json`의 `required`가 진실이고, **왜** 필수인가는
#: 레지스트리의 `opens`가 진실이다.
#:
#: 층이 이렇게 갈린 이유: 레지스트리는 스키마를 읽지만(모양) 스키마는 레지스트리를
#: 모른다. 반대로 엮으면 순환이 된다.


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def request_schema() -> dict:
    return json.loads(_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_request(spec: dict) -> list[str]:
    """클라우드 리소스 제약(RESOURCE_SPEC)의 검증. 빈 목록이면 통과.

    필수 누락은 `[필수] 칸 없음 — 왜` 꼴로 낸다 — jsonschema의 "'provider' is a
    required property"는 **왜 필요한지**를 못 담아서, 그대로 되물으면 사용자가
    아무 값이나 채우게 된다. 나머지 모양 위반은 스키마 메시지를 그대로 옮긴다.
    """
    problems: list[str] = []
    if not isinstance(spec, dict):
        return [f"[schema] the constraint must be an object (got {type(spec).__name__})"]

    validator = jsonschema.Draft202012Validator(request_schema())
    for error in validator.iter_errors(spec):
        # 최상위 required·anyOf(규모 신호)는 아래에서 '왜'와 함께 따로 말한다.
        if not error.absolute_path and error.validator in ("required", "anyOf"):
            continue
        where = "/".join(str(p) for p in error.absolute_path) or "(top level)"
        problems.append(f"[schema] {where}: {error.message[:140]}")

    # 필수 목록은 **스키마가 진실이다** — 손으로 적은 사본을 두면 스키마를 고칠 때
    # 조용히 갈린다. 왜 필수인지는 여기서 말하지 않는다(레지스트리의 `opens`).
    for field_name in request_schema().get("required", ()):
        if field_name not in spec:
            problems.append(f"[required] {field_name} missing")
    return problems


def validate_design(design: dict) -> list[str]:
    """스키마 + 참조 검증. 빈 목록이면 통과.

    **짐작으로 잇지 않는다** — 조인이 안 되는 요소는 여기서 걸리고, 걸린 채로
    composer에 들어가는 일이 없다.
    """
    problems: list[str] = []
    validator = jsonschema.Draft202012Validator(schema())
    for error in validator.iter_errors(design):
        where = "/".join(str(p) for p in error.absolute_path) or "(top level)"
        # artifacts가 oneOf라 실패 메시지가 "is not valid under any of the given
        # schemas"로 뭉개진다 — **어느 칸이 왜 틀렸는지가 삼켜진다.** 실측에서
        # ownerComponentId 누락이 그 문구에 묻혀 안 보였고, best_match 휴리스틱은
        # 엉뚱한 분기(openapi의 componentId)를 골랐다. 산출물엔 `kind` 판별자가
        # 있으므로 휴리스틱 대신 **판별자로 분기를 직접 고른다**.
        if error.validator == "oneOf" and error.context:
            kind = error.instance.get("kind") if isinstance(error.instance, dict) else None
            branch = _KIND_BRANCH.get(kind)
            candidates = [
                sub for sub in error.context
                if branch is not None and next(iter(sub.schema_path), None) == branch
            ]
            best = jsonschema.exceptions.best_match(candidates or error.context)
            if best is not None:
                inner = "/".join(str(p) for p in best.path)
                spot = f"{where}/{inner}" if inner else where
                problems.append(f"[schema] {spot}: {best.message[:140]}")
                continue
        problems.append(f"[schema] {where}: {error.message[:140]}")
    if problems:
        # 모양이 틀렸으면 참조 검증은 소음이 된다 — 스키마 문제부터 보여준다.
        return problems

    components = {c["id"] for c in design["components"]}
    externals = {e["id"] for e in design.get("externals") or []}
    if len(components) != len(design["components"]):
        problems.append("[reference] component ids collide")
    if components & externals:
        problems.append(
            "[reference] a component id and an external system id collide: "
            f"{sorted(components & externals)}"
        )

    artifact_ids: set[str] = set()
    for artifact in design["artifacts"]:
        aid = artifact["id"]
        if aid in artifact_ids:
            problems.append(f"[reference] duplicate artifact id: {aid}")
        artifact_ids.add(aid)
        kind = artifact["kind"]

        if kind == "openapi":
            if artifact["componentId"] not in components:
                problems.append(
                    f"[reference] {aid}: componentId "
                    f"'{artifact['componentId']}' is not in components"
                )
            version = str(artifact["openapi"].get("openapi", ""))
            if not version.startswith("3."):
                problems.append(
                    f"[reference] {aid}: not OpenAPI 3.x (openapi={version!r})"
                )

        elif kind == "er":
            names = set()
            for entity in artifact["entities"]:
                names.add(entity["name"])
                if entity["ownerComponentId"] not in components:
                    problems.append(
                        f"[reference] {aid}/{entity['name']}: ownerComponentId "
                        f"'{entity['ownerComponentId']}' is not in components"
                    )
            for relation in artifact.get("relations") or []:
                for end in ("from", "to"):
                    if relation[end] not in names:
                        problems.append(
                            f"[reference] {aid}: the relation's "
                            f"'{relation[end]}' is not in entities"
                        )

        elif kind == "class":
            for cls in artifact["classes"]:
                if cls["componentId"] not in components:
                    problems.append(
                        f"[reference] {aid}/{cls['name']}: componentId "
                        f"'{cls['componentId']}' is not in components"
                    )

        elif kind == "sequence":
            participant_ids = set()
            for participant in artifact["participants"]:
                pid = participant["id"]
                participant_ids.add(pid)
                refs = [
                    participant.get("componentId") is not None,
                    participant.get("externalId") is not None,
                    bool(participant.get("actor")),
                ]
                if sum(refs) != 1:
                    problems.append(
                        f"[reference] {aid}/{pid}: must point at **exactly one** of "
                        f"componentId · externalId · actor (got {sum(refs)})"
                    )
                    continue
                if participant.get("componentId") is not None \
                        and participant["componentId"] not in components:
                    problems.append(
                        f"[reference] {aid}/{pid}: componentId "
                        f"'{participant['componentId']}' is not in components"
                    )
                if participant.get("externalId") is not None \
                        and participant["externalId"] not in externals:
                    problems.append(
                        f"[reference] {aid}/{pid}: externalId "
                        f"'{participant['externalId']}' is not in externals"
                    )
            for i, message in enumerate(artifact["messages"]):
                for end in ("from", "to"):
                    if message[end] not in participant_ids:
                        problems.append(
                            f"[reference] {aid}/messages[{i}]: '{message[end]}' is "
                            "not in participants"
                        )
    return problems
