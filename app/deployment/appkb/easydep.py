"""easydep 설계 최종본 → 입력 계약(JSON) 어댑터. 재편 계획 ③.

## 왜 되파싱인가

이 계약(schema.json)은 "설계도는 JSON이고 PlantUML은 표현"을 전제했는데, 본체
easydep이 **저장하는 최종본은 class·sequence·ER 모두 PlantUML 텍스트**다
(api_spec만 JSON). 구조화 중간물(BCE JSON)은 의도적으로 저장하지 않는다 —
피드백이 PlantUML을 직접 고치므로 저장해 두면 어긋난다는 것이 그쪽의 실측이고,
easydep HANDOFF 스스로 "최종 산출물을 소비 시점에 파싱하라"를 방침으로 적었다.
그래서 이 어댑터는 배포 구성을 만들 때마다 **그 시점의 최종본**을 되파싱한다.
JSON 원본화(설계 노드가 JSON을 원본으로 갖고 PlantUML을 렌더링으로)는 팀 합의
안건으로 남아 있다 — 합의되면 이 모듈이 사라지는 것이 맞다.

## 컴포넌트는 하나다 — 합성하지 않는다

easydep 설계 파이프라인에는 배포 단위 분해가 없다 — 유스케이스 명세 하나 →
클래스들 → API 명세 **하나** → ERD **하나**. 분해 신호가 원본에 없으므로
스테레오타입·패키지에서 마이크로서비스 경계를 지어내면 **설계가 그리지 않은
경계를 우리가 만드는 것**이다. 컴포넌트는 정확히 하나(`app`)로 옮긴다.

## 못 읽으면 못 읽었다고 한다

시퀀스·ERD는 문법 제약이 없는 LLM 출력이다(클래스만 결정론 렌더러가 만든다 —
`plantuml_class_diagram.py`가 곧 그 문법 명세다). 파서는 **아는 모양만** 읽고,
못 읽은 것은 짐작으로 잇지 않고 반환되는 목록에 올린다. 계약의 async 칸은
"화살표 표기가 아니라 의미"를 받으라고 적혀 있지만, 최종본이 PlantUML뿐이라
여기서는 표기(`->>`)에서 의미를 **추정**한다 — 그 사실도 목록에 적는다.
"""

from __future__ import annotations

import re

#: 단일 컴포넌트의 id. 리소스 이름 씨앗 규칙(^[a-z0-9][a-z0-9-]*$)을 지킨다.
COMPONENT_ID = "app"

#: 결정론 렌더러의 클래스 선언: `class Name <<Stereotype>> {`.
#: 이름은 렌더러가 [A-Za-z0-9_]로 소독해 주므로 그 밖의 모양은 나오지 않는다.
_CLASS = re.compile(r"^\s*class\s+([A-Za-z0-9_]+)(?:\s*<<([^>]+)>>)?", re.M)

#: 시퀀스 참가자 선언. PlantUML이 허용하는 종류 키워드 전부.
_PARTICIPANT = re.compile(
    r"^\s*(actor|participant|boundary|control|entity|database|collections|queue)\s+"
    r'(?:"([^"]+)"|(\S+))(?:\s+as\s+(\S+))?\s*$',
    re.M,
)

#: 시퀀스 화살표. 흔한 네 꼴만 안다: `->` `-->` `->>` `-->>`.
#: 점선(`--`)은 PlantUML 의미상 응답(reply)이라 메시지로 세지 않는다.
_ARROW = re.compile(
    r'^\s*(?:"([^"]+)"|([\w.]+))\s*(-{1,2}>{1,2})\s*(?:"([^"]+)"|([\w.]+))\s*'
    r"(?::\s*(.+?))?\s*$",
    re.M,
)

#: ERD 엔티티 선언: `entity Name` · `entity "표시 이름" as alias`.
_ENTITY = re.compile(
    r'^\s*entity\s+(?:"([^"]+)"|([A-Za-z0-9_]+))(?:\s+as\s+(\w+))?', re.M
)

#: 시퀀스에 저장소가 참가자로 나오면 건너뛴다 — ER에서 이미 저장소 노드를
#: 세우므로 두 번 그리면 같은 것이 다른 두 상자가 된다. **걸러냈다는 사실은
#: 반환 목록에 적는다** (조용한 휴리스틱은 두지 않는다).
_STORE_LIKE = re.compile(r"(db|database|repository|저장소)", re.I)

#: RESOURCE_SPEC에서 설계 계약의 requirements로 내려가는 칸(투영).
_REQ_FIELDS = (
    "provider", "region", "expectedConcurrentUsers",
    "approxRequestsPerSecond", "monthlyBudgetUSD", "multiZone",
    "trafficPattern", "stateless", "dataResidency",
)


def _slug(text: str) -> str:
    """외부 시스템 이름 → 리소스 이름 규칙에 맞는 id."""
    slug = re.sub(r"[^a-z0-9-]", "-", text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "external")[:63]


def parse_classes(class_puml: str) -> list[tuple[str, str]]:
    """`[(이름, 스테레오타입)]`. 렌더러가 결정론이라 이 파서만은 문법이 핀돼 있다."""
    return [(m.group(1), (m.group(2) or "").strip()) for m in _CLASS.finditer(class_puml)]


def parse_er_entities(erd_puml: str) -> list[str]:
    names = []
    for m in _ENTITY.finditer(erd_puml):
        names.append(m.group(1) or m.group(2))
    return names


def parse_sequence(
    sequence_puml: str,
) -> tuple[dict[str, tuple[str, str]], list[tuple[str, str, str, bool]]]:
    """(선언된 참가자 {별칭/이름: (종류, 표시 이름)}, 메시지 [(from, to, label, async)]).

    표시 이름을 따로 드는 이유: 화살표는 별칭(`pg`)을 쓰는데 별칭만 남기면
    외부 시스템이 "PG Gateway"가 아니라 "pg"라는 이름으로 세워진다(실측).

    async는 화살촉 `>>`에서 추정한다. 점선은 응답이라 버린다 — 단, 전부 점선이면
    그 판단이 메시지를 0으로 만드는 것이므로 점선을 메시지로 받아들인다(호출자가
    그 사실을 알 수 있게 하려면 여기가 아니라 못 읽음 목록에 적어야 하는데,
    그 목록은 design_from_easydep가 만든다).
    """
    kinds: dict[str, tuple[str, str]] = {}
    for m in _PARTICIPANT.finditer(sequence_puml):
        kind, quoted, bare, alias = m.groups()
        display = quoted or bare
        key = alias or display
        kinds[key] = (kind, display)
        if display and display != key:
            kinds[display] = (kind, display)

    solid: list[tuple[str, str, str, bool]] = []
    dashed: list[tuple[str, str, str, bool]] = []
    for m in _ARROW.finditer(sequence_puml):
        src = m.group(1) or m.group(2)
        arrow = m.group(3)
        dst = m.group(4) or m.group(5)
        label = (m.group(6) or "").strip()
        is_async = arrow.endswith(">>")
        (dashed if arrow.startswith("--") else solid).append((src, dst, label, is_async))
    return kinds, (solid if solid else dashed)


def design_from_easydep(
    name: str,
    *,
    api_spec: dict | None = None,
    class_puml: str = "",
    sequence_puml: str = "",
    erd_puml: str = "",
    resource_spec: dict | None = None,
) -> tuple[dict, list[str]]:
    """easydep 최종 산출물 → (설계 계약 JSON, 못 읽은 것 목록).

    목록이 비어 있지 않아도 설계는 돌려준다 — 부분 입력에 부분 답. 다만 그
    목록은 배포 답변의 "답하지 못한 것"으로 이어져야 한다(호출자 책임).
    """
    skipped: list[str] = []
    artifacts: list[dict] = []
    externals: list[dict] = []

    classes = parse_classes(class_puml or "")
    class_names = {c for c, _ in classes}
    if class_puml and not classes:
        skipped.append("클래스 다이어그램에서 클래스를 하나도 읽지 못했습니다")
    if classes:
        artifacts.append({
            "id": "class-1", "kind": "class",
            "classes": [
                {"name": cname, "componentId": COMPONENT_ID,
                 **({"stereotypes": [st]} if st else {})}
                for cname, st in classes
            ],
        })

    if api_spec is not None:
        version = str(api_spec.get("openapi", "")) if isinstance(api_spec, dict) else ""
        if version.startswith("3."):
            artifacts.append({
                "id": "api-1", "kind": "openapi",
                "componentId": COMPONENT_ID, "openapi": api_spec,
            })
        else:
            skipped.append(
                f"API 명세가 OpenAPI 3.x가 아니라 쓰지 못했습니다 (openapi={version!r})"
            )

    entities = parse_er_entities(erd_puml or "")
    if erd_puml and not entities:
        skipped.append(
            "ERD에서 엔티티를 하나도 읽지 못했습니다 — 아는 문법은 `entity 이름`뿐입니다. "
            "저장소 노드가 빠진 채 구성됩니다"
        )
    if entities:
        artifacts.append({
            "id": "er-1", "kind": "er",
            "entities": [
                {"name": entity, "ownerComponentId": COMPONENT_ID} for entity in entities
            ],
        })

    if sequence_puml:
        kinds, messages = parse_sequence(sequence_puml)
        if not messages:
            skipped.append(
                "시퀀스에서 메시지를 하나도 읽지 못했습니다 — 아는 화살표는 "
                "`->` `-->` `->>` `-->>`뿐입니다. 비동기·공개 노출 신호가 빠집니다"
            )
        else:
            participants: dict[str, dict] = {}
            id_of: dict[str, str] = {}

            def _resolve(raw: str) -> str | None:
                """참가자 → 계약 참가자 id. 저장소류는 None(건너뜀)."""
                if raw in id_of:
                    return id_of[raw]
                kind, display = kinds.get(raw, ("", raw))
                if kind == "actor":
                    pid = _slug(raw)
                    participants[pid] = {"id": pid, "actor": True, "name": display}
                elif raw in class_names or display in class_names or kind in (
                    "boundary", "control", "entity"
                ):
                    # 앱의 클래스(또는 BCE 참가자)다 — 전부 단일 컴포넌트 안이다.
                    pid = COMPONENT_ID
                    participants.setdefault(pid, {"id": pid, "componentId": COMPONENT_ID})
                elif kind in ("database", "queue") or _STORE_LIKE.search(display):
                    # 저장소·큐가 참가자로 나오면 건너뛴다 — ER·비동기 신호에서
                    # 이미 그 노드를 세우므로 두 번 그리면 같은 것이 다른 상자가 된다.
                    skipped.append(
                        f"시퀀스 참가자 '{display}'는 저장소·큐로 보여 건너뛰었습니다 — "
                        "ER·비동기 신호가 세우는 노드와 겹치지 않게 하기 위해서입니다"
                        "(휴리스틱)"
                    )
                    id_of[raw] = None  # type: ignore[assignment]
                    return None
                else:
                    pid = _slug(display)
                    if pid not in {e["id"] for e in externals}:
                        externals.append({"id": pid, "name": display})
                    participants[pid] = {"id": pid, "externalId": pid}
                id_of[raw] = pid
                return pid

            contract_messages = []
            for src, dst, label, is_async in messages:
                src_id, dst_id = _resolve(src), _resolve(dst)
                if src_id is None or dst_id is None:
                    continue
                if src_id == dst_id and not is_async:
                    continue  # 한 배포 단위 안의 동기 호출은 배포 선이 아니다
                contract_messages.append({
                    "from": src_id, "to": dst_id, "async": is_async,
                    **({"label": label} if label else {}),
                })
            if participants and contract_messages:
                artifacts.append({
                    "id": "seq-1", "kind": "sequence",
                    "participants": list(participants.values()),
                    "messages": contract_messages,
                })
                if any(m["async"] for m in contract_messages):
                    skipped.append(
                        "비동기 여부는 화살표 표기(`->>`)에서 추정했습니다 — "
                        "계약이 원래 요구하는 것은 의미이지 표기가 아닙니다"
                    )

    design: dict = {
        "schemaVersion": "1",
        "name": name or "(이름 없음)",
        "components": [{
            "id": COMPONENT_ID,
            "name": name or "애플리케이션",
            "summary": "easydep 설계 전체 — 배포 단위 분해 신호가 원본에 없어 "
                       "단일 컴포넌트로 옮겼습니다",
        }],
        "artifacts": artifacts,
    }
    if externals:
        design["externals"] = externals
    if resource_spec:
        requirements = {
            k: resource_spec[k] for k in _REQ_FIELDS if k in resource_spec
        }
        if requirements:
            design["requirements"] = requirements
    if not artifacts:
        skipped.append(
            "읽을 수 있는 산출물이 하나도 없었습니다 — 이 설계는 입력 계약을 "
            "통과하지 못합니다"
        )
    return design, skipped
