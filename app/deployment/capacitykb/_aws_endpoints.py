"""AWS 서비스 소재 — botocore `endpoints.json` 축.

모듈 이름이 `_endpoints`가 아닌 이유: 이 안의 캐시 로더 함수가 `_endpoints()`다.
같은 이름이면 `from … import _endpoints`가 모듈인지 함수인지 읽는 사람도
`import` 문도 헷갈린다.

`agent_api`에서 갈라 나왔다(2026-07-28). **순수 코드 이동이고 문구는 그대로다.**

이 축은 용량 제약과 **다른 산출물**을 읽는다("속성에 걸린 제약"이 아니라 "무엇이 어디에
있는가"라서 `CAPACITY_FILES`에 넣지 않는다는 판단이 원래 주석에 있었다). 그래서 파일도
갈랐다 — 같은 이유로 갈라 놓은 것을 한 파일에 둘 이유가 없다.

같이 갈라 나오지 **못한** 것: Azure 작업 소요(`operation_time`)는 리소스 타입 이름을
용량 데이터로 정규화하므로 독립이 아니다. 억지로 빼면 순환 import가 되거나 해석기를
인자로 넘기는 배관이 새로 생긴다.

공개 이름(`where_available`)은 `agent_api`가 다시 내보낸다 — 아키텍처 검사가 공개 모듈을
`{agent_api, dataset, model, query}`로 못 박아 두었고, 부르는 쪽도 그 이름으로 부른다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.deployment.capacitykb._text import _plural
from app.deployment.kbcommon import artifact

DEFAULT_OUTPUT_DIR = Path("output")


# --------------------------------------------------------------------------
# 리전 — botocore endpoints.json (`output/aws-endpoints.json`)
#
# 이 산출물은 CapacitySet(제약·쿼터)과 모양이 달라 `CAPACITY_FILES`에 넣지 않는다.
# "속성에 걸린 제약"이 아니라 "무엇이 어디에 있는가"라서 억지로 끼워 넣으면
# `type_id`·`property` 칸을 거짓으로 채우게 된다.
# --------------------------------------------------------------------------

ENDPOINTS_FILE = "aws-endpoints.json"

_ENDPOINTS_MISSING = (
    "no region artifact. build it with `python -m capacitykb build --source "
    "aws-endpoints`."
)

#: 엔드포인트가 목록에 없다는 것은 **못 쓴다는 뜻이 아니다.** 이 문장을 답변에
#: 반드시 함께 내보낸다 — 빼면 침묵이 "없음"으로 읽힌다.
_ABSENCE_CAVEAT = (
    "※ a region not listed here is not 'unusable' — **this data does not know**. "
    "a global service has only one endpoint, and the marker that tells one apart is "
    "on only 22 of the 307 services in the source."
)


#: **서비스 소재**(where_available)는 출처가 botocore라 AWS 전용이다. 리전 *이름*은
#: 이제 cloudinfo로 프로바이더 10곳을 알지만(`region_lookup`), 이 축은 아니다.
#: 밝히지 않으면 AWS만 본 답이 전체를 본 답처럼 보인다.
_AWS_ONLY_CAVEAT = (
    "※ which regions a service is in is included for **AWS only** (the source is the "
    "AWS SDK). this tool does not answer service availability for other providers."
)


@lru_cache(maxsize=4)
def _endpoints(output_dir: str) -> dict | None:
    path = artifact.resolve(Path(output_dir), ENDPOINTS_FILE)
    if path is None:
        return None
    try:
        return artifact.load_json(path)
    except Exception:
        return None


def _service_id(name: str, services: dict) -> tuple[str | None, str]:
    """CFN 타입이나 서비스 이름을 botocore 서비스 id로. 못 붙이면 (None, 이유).

    붙이는 방법은 둘뿐이다 — 그대로 일치, 하이픈 빼고 일치(`acmpca` → `acm-pca`).
    실측으로 우리 네임스페이스 281개 중 182개(65%)가 이렇게 붙고 **충돌은 0건**이다.
    남는 99개는 철자가 아니라 이름 자체가 다르다(`cloudwatch`는 원본에서
    `monitoring`, `cognito`는 `cognito-idp`). 규칙으로 못 맞히는 것을 짐작으로
    붙이면 **엉뚱한 서비스의 리전을 자신 있게 답하게 된다.**
    """
    text = name.strip()
    match = re.match(r"^(?:aws::)?AWS::([^:]+)::", text)
    key = (match.group(1) if match else text).lower()

    if key in services:
        return key, ""
    flat = {s.replace("-", ""): s for s in services}
    if key in flat:
        return flat[key], ""
    return None, key


def where_available(name: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> str:
    """이 서비스의 엔드포인트가 어느 리전에 있는가.

    `AWS::EC2::Instance` 처럼 CFN 타입으로 물어도 되고 `ec2` 처럼 서비스 이름으로
    물어도 된다. **없는 리전은 답하지 않는다** — 위 `_ABSENCE_CAVEAT` 참조.
    """
    data = _endpoints(str(output_dir))
    if data is None:
        return _ENDPOINTS_MISSING

    services = data.get("services", {}).get("aws") or {}
    service, unmatched = _service_id(name, services)
    if service is None:
        import difflib

        # 비슷한 이름을 **제안**한다. 골라서 답하지는 않는다 — 제안은 사용자가
        # 확인할 수 있지만, 골라 버리면 틀렸을 때 확인할 방법이 없다.
        near = difflib.get_close_matches(unmatched, services, n=4, cutoff=0.6)
        lines = [
            f"our data cannot pin down which AWS SDK service '{name}' is "
            f"(name looked up: '{unmatched}').",
            "  some service names are not regular — CloudWatch is `monitoring`, "
            "Cognito is `cognito-idp`, Certificate Manager is `acm`.",
        ]
        if near:
            lines.append(f"  similar names: {', '.join(near)}")
        lines.append(
            "  → ask again with the SDK service name. matching it by guess would "
            "confidently answer with the wrong service's regions, so we did not."
        )
        return "\n".join(lines)

    body = services[service]
    regions = body.get("regions") or []
    partitions = data.get("partitions", {}).get("aws", {}).get("regions") or {}

    if body.get("global"):
        return (
            f"{service}: a **global service** — the source states it is not tied to "
            f"a region (partitionEndpoint={body.get('partition_endpoint')}).\n"
            "  it is not something you pick a region to deploy into."
        )

    if not regions:
        return (
            f"{service}: no endpoint found in the standard partition.\n"
            f"  {_ABSENCE_CAVEAT}"
        )

    lines = [
        f"{service} — {_plural(len(regions), 'region', 'regions')} with an endpoint"
    ]
    for code in regions[:12]:
        label = (partitions.get(code) or {}).get("description")
        lines.append(f"  - {code}" + (f" ({label})" if label else ""))
    if len(regions) > 12:
        lines.append(f"  … and {len(regions) - 12} more")
    lines.append(f"  {_ABSENCE_CAVEAT}")
    lines.append(f"  {_AWS_ONLY_CAVEAT}")
    return "\n".join(lines)


# `region_lookup`(지명 → 리전 코드)은 envkb.regions로 이사했다(재편 계획 ⑤) —
# 리전 카탈로그는 envkb의 산출물이고, capacitykb가 그걸 임포트하면 KB→KB가 된다.
