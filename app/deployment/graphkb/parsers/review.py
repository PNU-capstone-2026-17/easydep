"""사람 검수 결과를 그래프에 적용한다.

## 왜 이게 있나

목표는 **완벽한 데이터셋**이지 완벽한 파서가 아니다. 소스에 핀을 박아 입력이 얼어
있으므로(`kbcommon/sources.py`), 눈으로 보고 손으로 고친 결과가 다음 빌드에서도
그대로 유효하다. 핀을 올릴 때만 다시 보면 된다.

그래서 오탐 몇십 건을 잡겠다고 정규식을 정교하게 다듬는 대신, **해당 엣지를 직접
지목해서 지운다.** 규칙은 왜 틀렸는지 설명하지 못하지만 검수 항목은 `reason`에
이유를 남긴다.

선례가 있다 — `core_vendor_map.json`은 CB-Spider 드라이버를 사람이 읽고 검수한
매핑 목록이고, `status: confirmed`인 것만 그래프에 들어간다.

## 파일 형식 (`graphkb/reviewed/<provider>-edges.json`)

```json
{
  "reviewed_against": {"source": "cfn-schema", "sha256": "83b888…"},
  "rejected": [
    {"from": "aws::AWS::QuickSight::Analysis", "to": "aws::AWS::Cases::Field",
     "reason": "FieldId는 차트 내부 항목 번호이지 고객센터의 Field 부품이 아니다"}
  ],
  "confirmed": [
    {"from": "aws::AWS::EC2::Subnet", "to": "aws::AWS::EC2::VPC",
     "reason": "구역은 네트워크 안에 만든다"}
  ],
  "added": [
    {"from": "…", "to": "…", "via_property": "…", "required": false,
     "cardinality": "one", "reason": "설명문에 명시돼 있는데 파서가 못 읽음"}
  ]
}
```

- **rejected** — 잘못된 관계. `via_property`를 생략하면 그 타입쌍의 **모든 경로**를
  지운다. QuickSight `FieldId` 오탐이 421개 경로에 퍼져 있어서 이 형태가 필요했다.
- **confirmed** — 보고 맞다고 확인한 것. 엣지에 `reviewed: true`가 붙는다.
  짐작(`heuristic`)이라도 확인됐으면 사실로 취급할 수 있다.
- **added** — 소스에 있는데 파서가 못 뽑은 것. 손으로 채워 넣는다.

`reviewed_against`는 검수 시점의 소스 해시다. 핀을 올린 뒤 이 값이 다르면
"검수가 낡았을 수 있다"고 빌드가 알린다 — 자동으로 버리지는 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from graphkb.model import Edge, Graph

REVIEW_DIR = Path(__file__).resolve().parent.parent / "reviewed"


def review_path(provider: str) -> Path:
    return REVIEW_DIR / f"{provider}-edges.json"


def load_review(provider: str, path: Path | None = None) -> dict:
    """검수 파일을 읽는다. 없으면 빈 검수(아무것도 안 함)."""
    target = path or review_path(provider)
    if not target.exists():
        return {"rejected": [], "confirmed": [], "added": []}
    data = json.loads(target.read_text(encoding="utf-8"))
    for key in ("rejected", "confirmed", "added"):
        data.setdefault(key, [])
    return data


def _match(entry: dict, edge: Edge) -> bool:
    """검수 항목이 이 엣지에 해당하는가. **적은 것만 본다.**

    셋 다 생략 가능하고, 생략한 항목은 "아무거나"라는 뜻이다. 덕분에 판단 단위를
    그때그때 고를 수 있다:

        {"to": "aws::AWS::IAM::Role"}          → 권한으로 가는 엣지 전부 (110개 출발)
        {"from": …, "to": …}                    → 그 타입쌍의 모든 경로
        {"from": …, "to": …, "via_property": …} → 그 칸 하나

    대상 하나를 판단하면 수십 쌍이 한 번에 정리된다 — `RoleArn`이 권한을 가리키는지는
    출발 부품이 무엇이든 답이 같기 때문이다. 예외는 `rejected`가 `confirmed`를
    이기므로 따로 적으면 된다.
    """
    for field, value in (("from", edge.from_id), ("to", edge.to_id),
                         ("via_property", edge.via_property)):
        wanted = entry.get(field)
        if wanted is not None and wanted != value:
            return False
    return True


def apply_review(graph: Graph, provider: str, *, path: Path | None = None) -> dict:
    """검수 결과를 그래프에 적용하고 요약을 반환한다.

    순서는 지우기 → 확인 표시 → 추가다. 지운 뒤에 확인 표시를 해야
    "지웠는데 확인됨으로 남는" 모순이 안 생긴다.
    """
    review = load_review(provider, path)
    rejected, confirmed, added = review["rejected"], review["confirmed"], review["added"]

    kept: list[Edge] = []
    dropped = 0
    for edge in graph.edges:
        if any(_match(entry, edge) for entry in rejected):
            dropped += 1
            continue
        kept.append(edge)

    # Edge는 frozen이라 수정 대신 새로 만든다.
    marked = 0
    for i, edge in enumerate(kept):
        if any(_match(entry, edge) for entry in confirmed) and not edge.reviewed:
            kept[i] = Edge(
                from_id=edge.from_id,
                to_id=edge.to_id,
                type=edge.type,
                via_property=edge.via_property,
                required=edge.required,
                cardinality=edge.cardinality,
                evidence=edge.evidence,
                confidence=edge.confidence,
                reviewed=True,
            )
            marked += 1

    graph.edges.clear()
    graph._edge_index.clear()
    for edge in kept:
        graph.add_edge(edge)

    inserted = 0
    for entry in added:
        # 추가는 대상을 특정해야 한다 — "아무거나"로 엣지를 만들 수는 없다.
        node_ids = (entry.get("from"), entry.get("to"))
        if not all(node_ids) or not all(n in graph.nodes for n in node_ids):
            print(f"경고: 검수 추가 항목의 노드가 그래프에 없어 건너뜀 — {entry}")
            continue
        graph.add_edge(
            Edge(
                from_id=entry["from"],
                to_id=entry["to"],
                type=entry.get("type", "references"),
                via_property=entry.get("via_property", ""),
                required=bool(entry.get("required", False)),
                cardinality=entry.get("cardinality", "one"),
                evidence="human-review",
                confidence=1.0,
                reviewed=True,
            )
        )
        inserted += 1

    return {"dropped": dropped, "confirmed": marked, "added": inserted}


def check_freshness(provider: str, provenance: list[dict]) -> str | None:
    """검수가 지금 소스 기준인지 확인한다. 낡았으면 설명 문자열, 아니면 None.

    자동으로 버리지 않는다 — 소스가 조금 바뀌어도 검수 대부분은 여전히 유효하고,
    무엇이 낡았는지는 사람이 봐야 알 수 있기 때문이다.
    """
    review = load_review(provider)
    against = review.get("reviewed_against")
    if not against:
        return None
    now = {p.get("source"): p.get("sha256") for p in provenance}
    then_source, then_hash = against.get("source"), against.get("sha256")
    current = now.get(then_source)
    if current and then_hash and current != then_hash:
        return (
            f"검수 파일이 {then_source}의 다른 버전({then_hash[:12]}…) 기준입니다. "
            f"지금은 {current[:12]}… — 검수 항목을 다시 확인하세요."
        )
    return None
