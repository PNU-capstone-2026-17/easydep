"""리전 이름 ↔ 리전 코드. `서울` → `ap-northeast-2`.

**왜 인프라인가.** 네 지식베이스가 전부 리전을 키로 쓴다 — costkb는 가격을,
perfkb는 스펙을, capacitykb는 리전별 허용값을 리전 코드로 색인한다. 그런데 사람은
`ap-northeast-2`라고 묻지 않고 "서울"이라고 묻는다. 둘 이상이 쓰는 공용 인프라라
여기 둔다(`kbcommon/__init__.py`의 규칙).

**왜 이게 필요했나.** 에이전트 실측에서 "서울 리전에서 GPU 인스턴스 쓸 수 있나"에
답하지 못했다. 그런데 데이터는 **있었다** — `output/aws-regions.json`에
`ap-northeast-2`의 EC2 인스턴스 타입 780개가 그대로 담겨 있다. 없던 것은 질문의
`서울`을 색인 키로 바꾸는 길뿐이었다. 이번 조사에서 반복해서 확인한 실패 모양이
또 나온 것이다 — **데이터는 있는데 에이전트가 못 닿는다.**

## 한국어 별칭은 리전 코드가 아니라 영어 낱말에 붙인다

    "서울" → "Seoul" → (원본에서 찾기) → ap-northeast-2

`"서울": "ap-northeast-2"`로 적는 편이 짧지만, 그러면 **리전 코드가 우리 표에
박힌다.** AWS가 리전을 늘리거나 이름을 바꾸면 표가 조용히 거짓이 된다. 낱말에
붙여 두면 코드는 늘 원본에서 온다 — 우리가 더하는 것은 번역뿐이다.

`matched_by`로 무엇 덕분에 맞았는지 함께 돌려준다. `code`·`name`은 원본이 말한
것이고 `alias`는 **우리가 더한 번역**이라, 답변에서 둘을 같은 무게로 말하면 안 된다.

## 지금은 AWS 리전만 안다 — 이걸 밝히지 않으면 답이 조용히 AWS 전용이 된다

출처가 botocore라 여기 담긴 46개 리전은 전부 AWS다. 그런데 우리 스펙 미러에는
**서울에 해당하는 리전이 여섯 프로바이더에 더 있다**(실측):

    azure    koreasouth        gcp      asia-northeast3
    tencent  ap-seoul          kt/ncp/nhn  kr1 · kr · kr1,kr2

프로바이더를 안 밝힌 "서울" 질문에 `ap-northeast-2` 하나만 돌려주고 조용히 넘어가면,
**AWS만 본 답이 전체를 본 답처럼 보인다.** 그래서 결과에 범위를 함께 적는다.

다른 프로바이더로 넓히려면 리전 코드가 아니라 **표시 이름**을 주는 소스가 프로바이더
별로 필요하다(코드만으로는 `asia-northeast3`가 서울인 줄 알 길이 없다). 아직 없으므로
지금은 넓히지 않고 **모른다고 밝힌다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from kbcommon.artifact import load_json, resolve

ARTIFACT = "aws-endpoints.json"

#: 한국어 낱말 → 원본 표시 이름에 들어 있는 영어 낱말.
#: 리전 코드를 여기 적지 않는 이유는 위 docstring 참조. 원본 표시 이름
#: (`Asia Pacific (Seoul)`, `US East (N. Virginia)`)에 실재하는 낱말만 적는다.
KOREAN_ALIASES: dict[str, str] = {
    # 아시아·태평양
    "서울": "Seoul",
    "도쿄": "Tokyo",
    "오사카": "Osaka",
    "타이베이": "Taipei",
    "대만": "Taipei",
    "홍콩": "Hong Kong",
    "싱가포르": "Singapore",
    "시드니": "Sydney",
    "멜버른": "Melbourne",
    "자카르타": "Jakarta",
    "뭄바이": "Mumbai",
    "하이데라바드": "Hyderabad",
    "말레이시아": "Malaysia",
    "뉴질랜드": "New Zealand",
    "태국": "Thailand",
    # 유럽
    "프랑크푸르트": "Frankfurt",
    "취리히": "Zurich",
    "스톡홀름": "Stockholm",
    "밀라노": "Milan",
    "스페인": "Spain",
    "아일랜드": "Ireland",
    "런던": "London",
    "파리": "Paris",
    "독일": "Germany",
    # 미주
    "버지니아": "N. Virginia",
    "오하이오": "Ohio",
    "캘리포니아": "N. California",
    "오리건": "Oregon",
    "캐나다": "Canada",
    "멕시코": "Mexico",
    "상파울루": "Sao Paulo",
    "브라질": "Sao Paulo",
    # 그 밖
    "케이프타운": "Cape Town",
    "남아프리카": "Cape Town",
    "이스라엘": "Israel",
    "텔아비브": "Tel Aviv",
    "바레인": "Bahrain",
    "아랍에미리트": "UAE",
    "중국": "China",
    "베이징": "Beijing",
    "닝샤": "Ningxia",
    # 넓은 말 — 여러 리전에 걸린다. 걸러내지 않고 여러 건을 그대로 돌려준다.
    "미국": "US",
    "유럽": "Europe",
    "아시아": "Asia Pacific",
    "중동": "Middle East",
    "남미": "South America",
    "아프리카": "Africa",
}


@dataclass(frozen=True, slots=True)
class RegionMatch:
    """리전 하나와, 무엇 덕분에 찾았는지."""

    code: str
    name: str
    partition: str
    matched_by: str
    """`code` | `name` | `alias`.

    앞의 둘은 **원본이 말한 것**이고 `alias`는 우리가 더한 한국어 번역이다.
    """


@lru_cache(maxsize=4)
def _catalog(output_dir: str | None) -> tuple[RegionMatch, ...]:
    """산출물에서 (코드, 이름, 파티션)을 읽는다. 없으면 빈 목록."""
    path = resolve(Path(output_dir) if output_dir else Path("output"), ARTIFACT)
    if path is None:
        return ()
    try:
        data = load_json(path)
    except Exception:
        # 산출물이 깨져도 리전 해석 하나 때문에 질의 전체가 죽으면 안 된다.
        return ()
    out = []
    for partition, body in (data.get("partitions") or {}).items():
        for code, region in (body.get("regions") or {}).items():
            out.append(
                RegionMatch(
                    code=code,
                    name=region.get("description") or code,
                    partition=partition,
                    matched_by="code",
                )
            )
    # 표준 파티션(`aws`)을 앞에 놓는다. **거르지는 않는다** — GovCloud·ISO도 실재하는
    # 리전이라 빼면 거짓이 된다. 다만 "미국"이라고 물으면 열에 아홉은 표준 파티션을
    # 뜻하므로, 고르는 건 부르는 쪽에 맡기되 순서로 힌트를 준다.
    return tuple(sorted(out, key=lambda r: (r.partition != "aws", r.code)))


def catalog(*, output_dir: str | None = None) -> tuple[RegionMatch, ...]:
    """알려진 리전 전체."""
    return _catalog(output_dir)


def name_of(code: str, *, output_dir: str | None = None) -> str | None:
    """`ap-northeast-2` → `Asia Pacific (Seoul)`. 모르면 None."""
    lowered = code.strip().lower()
    for region in _catalog(output_dir):
        if region.code == lowered:
            return region.name
    return None


def resolve_region(query: str, *, output_dir: str | None = None) -> list[RegionMatch]:
    """사람이 쓴 말에서 리전을 찾는다. 코드 → 표시 이름 → 한국어 별칭 순.

    **여러 건이 나올 수 있다.** "미국"은 리전 넷에 걸리고 "유럽"은 아홉에 걸린다.
    하나로 좁혀 돌려주면 우리가 고른 것이 사용자가 뜻한 것인 양 보이므로, 좁히는
    일은 부르는 쪽에 맡긴다.

    못 찾으면 빈 목록이다. 이건 "그런 리전이 없다"가 아니라 **"우리가 못 알아들었다"**
    이므로, 부르는 쪽은 없다고 단정하지 말고 아는 리전 목록을 보여 주는 편이 낫다.
    """
    text = query.strip()
    if not text:
        return []
    regions = _catalog(output_dir)
    lowered = text.lower()

    exact = [r for r in regions if r.code == lowered]
    if exact:
        return exact

    by_name = [r for r in regions if lowered in r.name.lower()]
    if by_name:
        return [
            RegionMatch(r.code, r.name, r.partition, "name") for r in by_name
        ]

    # 한국어는 띄어쓰기가 없어도 붙어 나오므로 부분 문자열로 찾는다
    # ("서울리전", "서울에서" 모두 걸려야 한다).
    found: list[RegionMatch] = []
    seen: set[str] = set()
    for korean, english in KOREAN_ALIASES.items():
        if korean not in text:
            continue
        for r in regions:
            if english.lower() in r.name.lower() and r.code not in seen:
                seen.add(r.code)
                found.append(RegionMatch(r.code, r.name, r.partition, "alias"))
    return found
