"""타입 id 만들기·정규화 (지식베이스 패키지 공유).

## 왜 공용인가

지식베이스는 서로 import하지 않지만 **`type_id`로 조인한다.** `capacitykb/model/records.py`가
"두 지식베이스는 코드가 분리돼 있지만 이 규약 덕분에 질의 시점에 조인할 수 있다"고
명시한 규약이다. 그 규약은 id를 만드는 규칙이 **모든 KB에서 같을 때만** 성립한다.

실제로는 그렇지 않았다. graphkb에는 대표 표기를 고르는 로직이 있고 capacitykb에는
같은 로직이 **복사돼 있으면서 정작 id를 만들 때 쓰이지 않았다.** 결과:

    capacity: azure::Microsoft.Compute/cloudServices/roleInstances/networkInterfaces
    graph:    azure::microsoft.Compute/cloudServices/roleInstances/networkInterfaces
                     ^ 조인 실패

## Azure 표기가 갈리는 이유

ARM 타입 이름은 대소문자를 구분하지 않는다. 그래서 Azure 자신도 일관되게 적지 않는다 —
**API 버전마다 표기가 다르다**:

    network/microsoft.compute/2025-03-01/types.json → Microsoft.Compute/cloudServices/...
    network/microsoft.compute/2025-07-01/types.json → microsoft.Compute/cloudServices/...

index.json 전체에서 대소문자만 다른 타입이 **71종**이다(`Microsoft.Cache/Redis` vs
`Microsoft.Cache/redis` 등). 문자열만 보고는 어느 쪽이 "옳은" 표기인지 알 수 없으므로,
순수 함수로는 정규화할 수 없다. 대신 **모든 KB가 같은 index.json에서 같은 규칙으로
대표를 고른다** — 소문자로 묶고, 최신 안정 버전의 표기를 대표로 쓴다. 소스에 핀이
박혀 있으므로(`kbcommon/sources.py`) 이 선택은 빌드마다 재현된다.
"""

from __future__ import annotations

from dataclasses import dataclass


def make_type_id(provider: str, type_name: str) -> str:
    """`aws::AWS::EC2::Instance` 형태의 조인 키. 형식을 아는 곳은 여기 하나다."""
    return f"{provider}::{type_name}"


def _is_preview(version: str) -> bool:
    return "preview" in version.lower()


def _version_better(candidate: str, current: str) -> bool:
    """비-preview 우선, 같은 등급이면 사전순(날짜형이라 사전순=시간순) 최신."""
    if _is_preview(candidate) != _is_preview(current):
        return _is_preview(current)
    return candidate > current


@dataclass(frozen=True)
class AzureTypeIndex:
    """index.json에서 뽑은 타입별 최신 버전과 **대표 표기**.

    `latest`는 {대표표기: (버전, types.json 상대경로)}, `by_lower`는
    {소문자: 대표표기}다. 두 KB가 같은 index.json으로 같은 결과를 얻는다.
    """

    latest: dict[str, tuple[str, str]]
    by_lower: dict[str, str]

    def canonical(self, type_name: str) -> str:
        """types.json이 어떻게 적었든 대표 표기로 바꾼다. 모르는 이름은 그대로 둔다."""
        return self.by_lower.get(type_name.lower(), type_name)

    def type_id(self, type_name: str) -> str:
        """정규화까지 마친 조인 키. **id를 만들 때는 항상 이 쪽을 쓴다.**"""
        return make_type_id("azure", self.canonical(type_name))


def read_azure_index(index: dict) -> AzureTypeIndex:
    """index.json을 읽어 대표 표기와 최신 버전을 정한다."""
    by_lower_full: dict[str, tuple[str, str, str]] = {}  # 소문자 → (버전, 경로, 표기)
    for key, ref in (index.get("resources") or {}).items():
        type_name, _, version = key.partition("@")
        rel_path = (ref.get("$ref") or "").split("#")[0]
        if not type_name or not version or not rel_path:
            continue
        lowered = type_name.lower()
        current = by_lower_full.get(lowered)
        if current is None or _version_better(version, current[0]):
            by_lower_full[lowered] = (version, rel_path, type_name)

    latest = {
        name: (version, rel_path)
        for version, rel_path, name in by_lower_full.values()
    }
    return AzureTypeIndex(
        latest=latest,
        by_lower={name.lower(): name for name in latest},
    )
