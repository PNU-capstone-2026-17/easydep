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

#: 필수 제약과 그 **왜** — 필수의 기준은 "없으면 뒤 단계 산출물의 요구사항 부합을
#: 잴 수 없다"는 판정식이다. 이 문장은 그대로 상류(easydep clarify 게이트)의
#: 되묻기 질문이 된다 — 무엇이 왜 필요한지 없이 되물으면 사용자는 임의로 채운다.
REQUIRED_WHY = {
    "provider": "값 조인 전부(비용·성능·번들·벤더 타입 대응)의 축입니다",
    "region": "단가·용량·탄소 데이터가 리전 코드로 색인되어 있습니다"
    " (지명이면 리전 해석을 거쳐 코드로)",
    "monthlyBudgetUSD": "비용이 요구에 부합하는지 판정할 기준값입니다 (USD)",
}
#: 규모 신호 — 둘 중 하나면 된다. 사이징 판정의 기준이라 필수다.
SCALE_FIELDS = ("expectedConcurrentUsers", "approxRequestsPerSecond")
SCALE_WHY = "사이징 판정의 기준입니다 — 없으면 스펙 추천이 전부 임의가 됩니다"


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
        return [f"[스키마] 제약은 객체여야 합니다 (지금 {type(spec).__name__})"]

    validator = jsonschema.Draft202012Validator(request_schema())
    for error in validator.iter_errors(spec):
        # 최상위 required·anyOf(규모 신호)는 아래에서 '왜'와 함께 따로 말한다.
        if not error.absolute_path and error.validator in ("required", "anyOf"):
            continue
        where = "/".join(str(p) for p in error.absolute_path) or "(최상위)"
        problems.append(f"[스키마] {where}: {error.message[:140]}")

    if "schemaVersion" not in spec:
        problems.append("[필수] schemaVersion 없음 — 계약 버전 표시입니다 (\"1\")")
    for field_name, why in REQUIRED_WHY.items():
        if field_name not in spec:
            problems.append(f"[필수] {field_name} 없음 — {why}")
    if not any(f in spec for f in SCALE_FIELDS):
        problems.append(
            f"[필수] 규모 신호 없음({' 또는 '.join(SCALE_FIELDS)}) — {SCALE_WHY}"
        )
    return problems


def validate_design(design: dict) -> list[str]:
    """스키마 + 참조 검증. 빈 목록이면 통과.

    **짐작으로 잇지 않는다** — 조인이 안 되는 요소는 여기서 걸리고, 걸린 채로
    composer에 들어가는 일이 없다.
    """
    problems: list[str] = []
    validator = jsonschema.Draft202012Validator(schema())
    for error in validator.iter_errors(design):
        where = "/".join(str(p) for p in error.absolute_path) or "(최상위)"
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
                problems.append(f"[스키마] {spot}: {best.message[:140]}")
                continue
        problems.append(f"[스키마] {where}: {error.message[:140]}")
    if problems:
        # 모양이 틀렸으면 참조 검증은 소음이 된다 — 스키마 문제부터 보여준다.
        return problems

    components = {c["id"] for c in design["components"]}
    externals = {e["id"] for e in design.get("externals") or []}
    if len(components) != len(design["components"]):
        problems.append("[참조] 컴포넌트 id가 겹친다")
    if components & externals:
        problems.append(f"[참조] 컴포넌트와 외부 시스템의 id가 겹친다: {sorted(components & externals)}")

    artifact_ids: set[str] = set()
    for artifact in design["artifacts"]:
        aid = artifact["id"]
        if aid in artifact_ids:
            problems.append(f"[참조] 산출물 id 중복: {aid}")
        artifact_ids.add(aid)
        kind = artifact["kind"]

        if kind == "openapi":
            if artifact["componentId"] not in components:
                problems.append(
                    f"[참조] {aid}: componentId '{artifact['componentId']}'가 components에 없다"
                )
            version = str(artifact["openapi"].get("openapi", ""))
            if not version.startswith("3."):
                problems.append(f"[참조] {aid}: OpenAPI 3.x가 아니다 (openapi={version!r})")

        elif kind == "er":
            names = set()
            for entity in artifact["entities"]:
                names.add(entity["name"])
                if entity["ownerComponentId"] not in components:
                    problems.append(
                        f"[참조] {aid}/{entity['name']}: ownerComponentId "
                        f"'{entity['ownerComponentId']}'가 components에 없다"
                    )
            for relation in artifact.get("relations") or []:
                for end in ("from", "to"):
                    if relation[end] not in names:
                        problems.append(
                            f"[참조] {aid}: 관계의 '{relation[end]}'가 entities에 없다"
                        )

        elif kind == "class":
            for cls in artifact["classes"]:
                if cls["componentId"] not in components:
                    problems.append(
                        f"[참조] {aid}/{cls['name']}: componentId "
                        f"'{cls['componentId']}'가 components에 없다"
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
                        f"[참조] {aid}/{pid}: componentId·externalId·actor 중 "
                        f"**정확히 하나**를 가리켜야 한다 (지금 {sum(refs)}개)"
                    )
                    continue
                if participant.get("componentId") is not None \
                        and participant["componentId"] not in components:
                    problems.append(
                        f"[참조] {aid}/{pid}: componentId "
                        f"'{participant['componentId']}'가 components에 없다"
                    )
                if participant.get("externalId") is not None \
                        and participant["externalId"] not in externals:
                    problems.append(
                        f"[참조] {aid}/{pid}: externalId "
                        f"'{participant['externalId']}'가 externals에 없다"
                    )
            for i, message in enumerate(artifact["messages"]):
                for end in ("from", "to"):
                    if message[end] not in participant_ids:
                        problems.append(
                            f"[참조] {aid}/messages[{i}]: '{message[end]}'가 "
                            "participants에 없다"
                        )
    return problems
