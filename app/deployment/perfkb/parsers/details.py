"""`spec_infos.details` 파서 — CSP 원본 응답을 읽는다.

지식 차원 분담은 `perfkb/__init__.py` 참고.

## 이 파일이 따로 있는 이유 — 포맷이 위험하다

`details`는 JSON 배열(`[{"key":..., "value":...}]`)이지만 **value 안쪽은 JSON이 아니다.**
Go의 `%v` 포맷이라 따옴표가 없고, 값에 공백이 들어가고, 중첩·배열이 섞인다:

    {EbsOptimizedInfo:{BaselineBandwidthInMbps:347,BaselineIops:2000},EbsOptimizedSupport:default}
    {NetworkCards:[{NetworkCardIndex:0,NetworkPerformance:Up to 5 Gigabit}],NetworkPerformance:Up to 5 Gigabit}

표준 JSON 파서로 못 읽고, 범용 파서를 쓰면 `Up to 5 Gigabit`의 공백에서 깨진다.
그래서 **전체를 파싱하지 않는다** — 필요한 키만 정규식으로 뽑고, 못 뽑으면 `None`을 준다
(fail-open). 뽑는 키를 소수로 유지하는 것 자체가 안전장치다.

## 실측 (v0.12.25, 73,083행)

- `details` 채움: aws 18,564 / gcp 11,622 / azure 34,846 — **전부 100%**
- AWS `EbsInfo` 결측 0건, `NetworkPerformance` 형태는 `Up to 10 Gigabit`(버스트) 또는
  `25 Gigabit`(고정) 두 가지뿐
- Azure 중복 키(`MaxDataDiskCount`) 34,846건, **값 불일치 0건** → dedupe 안전.
  단 값이 달라지면 상위 스키마가 바뀐 것이므로 **크게 실패시킨다**(조용한 드리프트 금지).
"""

from __future__ import annotations

import json
import re

# `Key:value` 에서 value를 뽑는다.
# - 키 앞에 구분자(`{` `,` `[`)를 요구한다. 안 그러면 **키가 다른 키의 접미사일 때 걸린다**:
#   `Iops`를 찾으면 `MaximumIops:11800`이, `ThreadsPerCore`를 찾으면
#   `DefaultThreadsPerCore`가 매치된다. 테스트로 고정돼 있다.
# - value는 다음 구분자 전까지 — 공백은 값의 일부다(`NetworkPerformance:Up to 5 Gigabit`).
# - 값이 중첩(`{`/`[`)으로 시작하면 뽑지 않는다(None) — 스칼라만 다룬다.
_FIELD = r"(?:^|[{{,\[]){key}:(?!\{{|\[)([^,}}\]]+)"


class DetailsMismatch(ValueError):
    """중복 키의 값이 서로 다르다 — 상위 스키마가 바뀌었다는 신호."""


def parse_details(raw: str | None) -> dict[str, str]:
    """`details` 컬럼 → {키: 값} (최상위만).

    Azure는 같은 키가 두 번 나온다(실측: `MaxDataDiskCount`, 값은 항상 동일).
    값이 같으면 dedupe하고, **다르면 DetailsMismatch를 던진다.**
    """
    if not raw or not raw.startswith("["):
        return {}
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}  # fail-open: 못 읽으면 성능 정보 없음으로 취급

    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key is None:
            continue
        value = str(item.get("value"))
        if key in out and out[key] != value:
            raise DetailsMismatch(
                f"details의 중복 키 '{key}' 값이 다릅니다: {out[key]!r} vs {value!r}. "
                "상위 스키마가 바뀌었을 수 있으니 파서를 점검하세요."
            )
        out[key] = value
    return out


def go_field(blob: str | None, key: str) -> str | None:
    """Go `%v` 문자열에서 `key:` 뒤의 스칼라 값을 뽑는다. 없거나 중첩이면 None.

    같은 키가 여러 번 나오면 **마지막(=최상위)**을 쓴다. AWS `NetworkInfo`가 그렇다 —
    `NetworkCards:[{...NetworkPerformance:X}],NetworkPerformance:X` 처럼 배열 안에 한 번,
    최상위에 한 번 나온다(실측상 값은 같다).
    """
    if not blob:
        return None
    found = re.findall(_FIELD.format(key=re.escape(key)), blob)
    if not found:
        return None
    return found[-1].strip() or None


def go_number(blob: str | None, key: str) -> float | None:
    """Go `%v` 문자열에서 숫자 필드를 뽑는다. 숫자가 아니면 None(fail-open)."""
    raw = go_field(blob, key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def go_bool(value: str | None) -> bool | None:
    """`"true"`/`"false"` → bool. 그 외는 None (빈 문자열·누락 포함)."""
    if value is None:
        return None
    low = value.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return None


def is_burst_bandwidth(network_performance: str | None) -> bool | None:
    """`"Up to 5 Gigabit"`은 버스트, `"25 Gigabit"`은 고정.

    실측상 AWS `NetworkPerformance`는 이 두 형태뿐이다. 판단할 수 없으면 None.
    """
    if not network_performance:
        return None
    return network_performance.strip().lower().startswith("up to")
