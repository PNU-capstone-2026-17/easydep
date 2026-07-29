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
from dataclasses import replace
from pathlib import Path

from app.core.cloudkb.graphkb.model import Edge, Graph

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


def reference_map_path(provider: str) -> Path:
    return REVIEW_DIR / f"{provider}-references.json"


def load_reference_map(provider: str, path: Path | None = None) -> dict[str, str | None]:
    """참조 껍데기 → 대상 타입 표를 읽는다. (`<provider>-references.json`)

    Azure의 참조는 `NetworkInterfaceReference`처럼 **대상의 id만 담는 껍데기 객체**로
    표현되는데, 그 껍데기 이름에서 대상 타입을 되찾는 일은 자동으로 안 된다:

    - `networkInterface`로 끝나는 타입이 5개다(Network·AzureStackHCI·ManagedNetworkFabric…).
      스키마 어디에도 그중 무엇인지 적혀 있지 않다 — Azure를 알아야 아는 사실이다.
    - `SubResource`(303회)·`CommonSubResource`(87회)는 이름에 대상 정보가 아예 없다.
      단서는 프로퍼티 이름뿐인데 `sourceVault`→KeyVault처럼 이름도 어긋난다.
    - `ApplicationGatewayHttpListener` 같은 건 애초에 참조가 아니라 **부모 body 안에
      인라인으로 적는 자식**이다. ARM 자식이라 `id`는 있어서 참조와 구분되지 않는다.

    그래서 파서는 후보가 **유일할 때만** 스스로 판단하고, 나머지는 이 표를 본다.
    표에 없는 껍데기는 엣지를 만들지 않고 빌드가 미결로 보고한다 — 조용히 넘어가면
    "관계 없음"과 "아직 안 본 것"이 구분되지 않는다.

    키는 두 단계로 찾는다. `래퍼@프로퍼티`가 있으면 그게 이기고, 없으면 `래퍼`를 본다.
    껍데기 대부분은 어디서 쓰이든 뜻이 같아 `래퍼` 한 줄이면 되고, 뜻이 자리마다
    다른 범용 껍데기 3종만 프로퍼티까지 적으면 된다.

    파일은 판단을 세 갈래로 나눠 적는다. 셋 다 결과는 "엣지 없음"이지만 **뜻이
    다르고**, 뜻이 달라야 다음 검수 때 무엇을 다시 볼지 알 수 있다:

    - `resolved` — 대상을 정했다. `{"NetworkInterfaceReference": "Microsoft.Network/networkInterfaces"}`
    - `not_a_reference` — 참조가 아니다. 부모 body 안에 인라인으로 적는 자식이거나
      설정 객체다. 다시 볼 필요 없다.
    - `intra_resource` — 같은 리소스 안의 다른 부품을 가리킨다. 애플리케이션
      게이트웨이의 라우팅 규칙이 같은 게이트웨이의 리스너·백엔드풀을 지목하는 식이다.
      부품 사이 배선이지 리소스 사이 의존이 아니라서 타입 수준 그래프에는 안 넣는다.
      (미결로 남은 76종 중 50종이 이것이었다.)
    - `polymorphic` — 참조는 맞는데 대상이 자리마다 다르다. DNS 레코드의
      `targetResource`는 공용 IP일 수도 트래픽 매니저일 수도 있다. 하나로 못 정하는
      것이 결론이므로 미결로 다시 보고하지 않는다.

    표에 아예 없는 껍데기만 미결이다 — 아직 안 본 것이라는 뜻이다.

    `Common` 접두사는 벗겨서 찾는다. bicep-types는 공용 정의 파일에도 같은 모양을
    `CommonSubResource`처럼 한 벌 더 내보내는데, 뜻은 접두사 없는 것과 같다.
    안 벗기면 표가 두 배가 된다(실측 251행 중 61행이 이 중복이었다).
    """
    target = path or reference_map_path(provider)
    if not target.exists():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    table: dict[str, str | None] = {}
    for block in ("not_a_reference", "intra_resource", "polymorphic"):
        for key in data.get(block) or []:
            table[key] = None
    table.update(data.get("resolved") or {})
    return table


def kind_alias_path(provider: str) -> Path:
    return REVIEW_DIR / f"{provider}-kind-aliases.json"


def load_kind_aliases(provider: str, path: Path | None = None) -> dict[str, str]:
    """설명문이 쓴 이름 → 실재하는 종류명. (`<provider>-kind-aliases.json`)

    CRD의 `description`은 사람이 읽으라고 쓴 산문이라 종류명을 정확히 적지 않는다.
    "a Secret resource"라고 쓰지만 KCC 종류명은 `SecretManagerSecret`이고,
    "BigQuery BigLake Catalog"라고 쓰지만 종류명은 `BigLakeCatalog`다.

    이걸 검수 파일의 `rejected`+`added`로 처리하면 **노드가 남는다** — 엣지만
    지워지고 파서가 만든 허구 종류 노드는 그대로다. 그래서 표기 교정은 파서가
    id를 만들기 전에 한다. 대상이 통째로 틀린 경우(종류는 실재하는데 그 자리의
    답이 아닌 것)는 여전히 검수 파일에서 다룬다 — 그건 이름 문제가 아니다.
    """
    target = path or kind_alias_path(provider)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8")).get("aliases") or {}


def _match(entry: dict, edge: Edge) -> bool:
    """검수 항목이 이 엣지에 해당하는가. **적은 것만 본다.**

    셋 다 생략 가능하고, 생략한 항목은 "아무거나"라는 뜻이다. 덕분에 판단 단위를
    그때그때 고를 수 있다:

        {"to": "aws::AWS::IAM::Role"}          → 권한으로 가는 엣지 전부 (110개 출발)
        {"from": …, "to": …}                    → 그 타입쌍의 모든 경로
        {"from": …, "to": …, "via_property": …} → 그 칸 하나
        {"to": …, "type": "references"}         → 그 대상을 **참조**하는 것만
                                                  (계층 contained_in은 건드리지 않음)
        {"evidence": "relationshipRef"}         → 그 근거로 만들어진 것 전부

    대상 하나를 판단하면 수십 쌍이 한 번에 정리된다 — `RoleArn`이 권한을 가리키는지는
    출발 부품이 무엇이든 답이 같기 때문이다. 예외는 `rejected`가 `confirmed`를
    이기므로 따로 적으면 된다.

    `type`이 필요했던 실례: Azure `networkManagers/routingConfigurations`로 들어오는
    엣지 9개가 진짜 계층 1개 + 인라인 설정 오탐 8개였다. 대상만 적으면 계층까지 죽는다.
    """
    for field, value in (("from", edge.from_id), ("to", edge.to_id),
                         ("type", edge.type), ("evidence", edge.evidence),
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

    # Edge는 frozen이라 수정 대신 새로 만든다. **필드를 손으로 나열하지 않는다** —
    # 그렇게 했다가 나중에 추가된 target_property가 조용히 떨어져 나갔다.
    # 모든 엣지가 confirmed였던 탓에 결합 지점이 전부 사라졌는데, 빌드는 성공했다.
    marked = 0
    for i, edge in enumerate(kept):
        if any(_match(entry, edge) for entry in confirmed) and not edge.reviewed:
            kept[i] = replace(edge, reviewed=True)
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
