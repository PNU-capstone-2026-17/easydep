"""리전 지명 → 리전 코드 해석의 접근점.

`RESOURCE_SPEC.region`은 **코드**여야 한다(`ap-northeast-2`). 지명('서울')이 그대로
들어가면 뒤 단계의 조인이 조용히 빈 답이 된다 — 스키마가 그 사실을 실측이라고 적어 뒀다.

해석은 `app/deployment/envkb/regions.py`가 한다. 여기서 덧붙이는 것은 **모호함을 감추지
않는 반환 모양** 하나다.

## 왜 하나로 좁혀 주지 않는가

원본이 여러 건을 돌려주는 데에는 이유가 있다 — '서울'은 프로바이더 10곳에 걸리고
'미국'은 수십 곳에 걸린다. 하나로 좁혀 돌려주면 **우리가 고른 것이 사용자가 뜻한 것인 양**
보인다. 그래서 이 층도 좁히지 않고, 부르는 쪽이 `provider`를 알 때만 좁혀지게 한다.

빈 결과는 "그런 리전이 없다"가 아니라 **"우리가 못 알아들었다"**이다. LLM에게 리전
코드를 지어내게 하는 대신 이 해석기를 쓰는 이유가 그것이다 — 못 알아들은 것을 못
알아들었다고 말할 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.deployment.envkb import regions as _regions


@dataclass(frozen=True)
class RegionCandidate:
    """해석 후보 하나. **원문을 함께 들고 다닌다** — 틀렸을 때 되짚을 근거다."""

    code: str
    provider: str
    display_name: str
    as_written: str


def resolve(query: str, *, provider: str | None = None) -> tuple[RegionCandidate, ...]:
    """사람이 쓴 리전 표현에서 후보를 찾는다. 못 알아들으면 빈 튜플.

    `provider`를 알면 그 안에서만 찾는다 — 프로바이더가 정해진 뒤에는 후보가 대개
    하나로 떨어진다.
    """
    if not (query or "").strip():
        return ()
    matches = _regions.resolve_region(query, provider=provider)
    return tuple(
        RegionCandidate(
            code=m.code,
            provider=m.provider,
            display_name=getattr(m, "display_name", "") or getattr(m, "name", ""),
            as_written=query,
        )
        for m in matches
    )


def providers() -> tuple[str, ...]:
    """리전 지식베이스가 **실제로 아는** 프로바이더 id들.

    `RESOURCE_SPEC.provider`는 자유 문자열이라 스키마가 값을 제한하지 않는다. 그래서
    생산자 쪽에 어휘가 필요한데, 손으로 적으면 그건 임의 사전이고 KB가 늘어날 때
    조용히 뒤처진다. 조인이 실제로 도는 축을 그대로 쓴다.
    """
    return _regions.providers()


def is_region_code(value: str, *, provider: str | None = None) -> bool:
    """이미 리전 **코드**인가 — 지명을 코드 자리에 넣은 것을 잡는다."""
    return any(c.code == value for c in resolve(value, provider=provider))
