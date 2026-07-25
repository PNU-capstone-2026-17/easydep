"""IBM Global Catalog → IBM VPC 인스턴스 프로필의 성능 신호.

## 왜 IBM만인가

perfkb는 aws·azure·gcp 셋뿐이고 나머지 일곱(alibaba·tencent·ibm·ncp·kt·nhn·openstack,
스펙 8,051건)은 **0건**이었다. 조사에서 그 일곱 중 **소스가 실재하는 것은 IBM뿐**으로
확인됐다 — Terraform provider는 타입 축만 주고 스펙 카탈로그를 주지 않는다.

## 담을 것을 값으로 정했다 — 채움률이 아니라 **변별력**으로

`default_config`에 칸이 30개 가까이 있지만 **값이 하나뿐인 칸은 담지 않는다.**
채움률 100%여도 전부 같은 값이면 아무것도 구분하지 못하고, 오히려 확신에 찬
오답이 된다.

    freqency        310건 100%  값 **1가지**(2000)   ← 안 담는다
    status          287건  93%  값 **1가지**(current)
    vcpu_architecture / os_architecture / reservation_terms / iothreads … 전부 1가지

`freqency`는 원본 철자 그대로다(frequency 아님). 담았으면 **모든 IBM 스펙이
"2.0 GHz"로 나왔을 것**이고, 그건 이 저장소가 가장 경계하는 종류의 값이다 —
실제 클럭은 세대마다 다른데 우리는 그걸 모른다.

담는 것은 실제로 갈리는 것들이다:

    bandwidth        98.1%  18가지      port_speed      92.6%   3가지
    max_nics         92.6%   5가지      numa_count      62.6%   2가지
    vcpu_manufacturer 82.9%  2가지(Intel/AMD)
    cpu_family       52.3%   3가지(Sapphirerapids/Graniterapids/Turin)
    vcpu_tenancy     92.6%   3가지      provisioning_timeout_seconds 92.6% 7가지
    gpu_*             4.5%

## `sustainedCpu`를 만들어 내지 않는다

perfkb의 경고는 `sustainedCpu`와 `currentGeneration`에서 나온다. IBM에는 그 신호가
없다. `vcpu_tenancy`가 `dedicated`인 걸 보고 "상시 CPU 보장"이라고 적고 싶어지지만
**그건 우리 추론이지 원본이 한 말이 아니다.**

대신 `vcpuTenancy`를 그대로 담고, 조회 계층이 **"레코드는 있으나 버스트·세대 신호가
없다"**를 별도 상태로 답한다(`NOTE_PARTIAL`). 침묵을 "확인됨"으로 읽게 하지 않으려는
것이고, 결함 C4에서 이미 같은 이유로 상태를 넷으로 갈랐다.

## 재배포 허가를 확인하지 못했다

무인증으로 열려 있지만 라이선스 문구를 찾지 못했다. Azure Retail과 같은 취급 —
**빌드 때만 받고 `data/`에 커밋하지 않는다.**
"""

from __future__ import annotations

import hashlib
import json
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from kbcommon.artifact import write_dataset
from kbcommon.fetch import cache_dir
from kbcommon.sources import SOURCES

PROVIDER = "ibm"

#: 한 번에 받는 개수. **`limit`이 아니라 `_limit`이다** — `limit`은 조용히 무시되고
#: 기본 페이지만 와서, 첫 페이지만 보면 커버리지가 14%로 보인다(조사에서 실제로 그랬다).
_PAGE = 200

#: 값이 하나뿐이라 **일부러 담지 않는** 칸. 지우지 말고 남겨 둔다 — 다음 사람이
#: "왜 클럭이 없지?" 하고 다시 넣는 것을 막는 것이 이 목록의 목적이다.
DROPPED_CONSTANT_FIELDS = (
    "freqency",  # 원본 철자 그대로. 310건 전부 2000 → 담으면 확신에 찬 오답
    "status",  # 287건 전부 current
    "vcpu_architecture",  # 전부 amd64
    "os_architecture",  # 전부 ["amd64"]
    "reservation_terms",  # 전부 one_year/three_year
    "network_bandwidth_mode",  # 전부 pooled
    "iothreads",  # 전부 true
)

_UA = "cloudkb-build"


def _fetch(refresh: bool = False) -> tuple[list[dict], str]:
    """인스턴스 프로필 전부. `(레코드, 응답 sha256)`."""
    path = cache_dir() / "ibm-global-catalog-profiles.json"
    if path.exists() and not refresh:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()

    ctx = ssl.create_default_context()
    base = SOURCES["ibm-global-catalog"].url.split("?")[0]
    rows: list[dict] = []
    offset = 0
    while True:
        url = base + "?" + urllib.parse.urlencode(
            {"q": "is.instance", "_limit": _PAGE, "_offset": offset}
        )
        request = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"}
        )
        with urllib.request.urlopen(request, context=ctx, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
        got = body.get("resources") or []
        rows.extend(got)
        offset += len(got)
        if not got or offset >= (body.get("count") or 0):
            break
    raw = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return rows, hashlib.sha256(raw).hexdigest()


def _config(profile: dict) -> dict:
    return (((profile.get("metadata") or {}).get("other") or {})
            .get("profile") or {}).get("default_config") or {}


def _profile_meta(profile: dict) -> dict:
    return ((profile.get("metadata") or {}).get("other") or {}).get("profile") or {}


def _number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def to_record(profile: dict, regions: list[str]) -> list[dict]:
    """프로필 하나 → 리전별 성능 레코드. id는 costkb와 같은 `provider+region+name`."""
    name = profile.get("name") or ""
    if not name:
        return []
    conf = _config(profile)
    meta = _profile_meta(profile)

    fields: dict[str, object] = {}
    for source_key, our_key in (
        ("bandwidth", "networkBandwidthMbps"),
        ("port_speed", "portSpeedMbps"),
        ("max_nics", "maxNics"),
        ("numa_count", "numaCount"),
        ("provisioning_timeout_seconds", "provisioningTimeoutSeconds"),
        ("gpu_count", "gpuCount"),
        ("gpu_memory", "gpuMemoryGB"),
    ):
        value = _number(conf.get(source_key))
        if value is not None:
            fields[our_key] = value
    if isinstance(conf.get("vcpu_manufacturer"), str):
        fields["cpuVendor"] = conf["vcpu_manufacturer"]
    if isinstance(conf.get("cpu_family"), str):
        fields["cpuFamily"] = conf["cpu_family"]
    if isinstance(conf.get("gpu_model"), str):
        fields["gpuModel"] = conf["gpu_model"]
    if isinstance(meta.get("family"), str):
        fields["family"] = meta["family"]
    tenancy = conf.get("vcpu_tenancy")
    if isinstance(tenancy, dict) and isinstance(tenancy.get("default"), str):
        fields["vcpuTenancy"] = tenancy["default"]

    return [
        {"id": f"{PROVIDER}+{region}+{name}", "provider": PROVIDER,
         "specName": name, **fields}
        for region in regions
    ]


def build(output: Path, *, mirror: Path, refresh: bool = False) -> dict:
    """미러의 ibm 스펙에 붙는 것만 담는다 — 붙지 않으면 답할 길이 없다."""
    specs = json.loads(mirror.read_text(encoding="utf-8"))["specs"]
    regions_of: dict[str, list[str]] = {}
    cpu_ram: dict[str, tuple[float, float]] = {}
    for spec in specs:
        if spec["provider"] != PROVIDER:
            continue
        key = spec["specName"].lower()
        regions_of.setdefault(key, []).append(spec["region"])
        cpu_ram.setdefault(key, (spec["vCPU"], spec["memGiB"]))

    profiles, digest = _fetch(refresh=refresh)
    records: list[dict] = []
    joined = agreed = disagreed = 0
    unmatched: list[str] = []
    for profile in profiles:
        if profile.get("kind") != "instance.profile":
            continue
        name = (profile.get("name") or "").lower()
        if name not in regions_of:
            unmatched.append(profile.get("name") or "?")
            continue
        joined += 1
        # **가정하지 않고 대조한다.** cpu·ram이 미러와 같으면 독립 소스 둘이 같은
        # 값을 말하는 것이라 교차 확인이 되고, 다르면 그건 우리가 알아야 할 신호다.
        conf = _config(profile)
        want = cpu_ram[name]
        if _number(conf.get("cpu")) is not None and _number(conf.get("ram")) is not None:
            if conf["cpu"] == want[0] and abs(conf["ram"] - want[1]) < 0.01:
                agreed += 1
            else:
                disagreed += 1
        records.extend(to_record(profile, sorted(set(regions_of[name]))))

    records.sort(key=lambda r: r["id"])
    payload = {
        "_note": (
            "Performance signals from the IBM Global Catalog instance profiles. **Fields "
            "with only one distinct value are not included** — in particular `freqency` "
            "(the source's own spelling) is 2000 for all 310 entries, so including it "
            "would have put a confident wrong answer, '2.0 GHz', on every IBM spec. "
            "`sustainedCpu` (burstable or not) and `currentGeneration` (generation) are "
            "**not in the source, so we did not invent them** — writing 'sustained CPU "
            "guaranteed' because `vcpuTenancy` is dedicated would be our inference, not "
            "something the source said. So candidates from this provider carry no "
            "performance warning, and the query layer states that as 'no signal' rather "
            "than 'verified'.\n"
            "**Source and redistribution status**: derived from values fetched from the "
            "IBM Cloud Global Catalog (https://globalcatalog.cloud.ibm.com/api/v1). "
            "Rights to the catalog data belong to IBM. **We found no wording granting "
            "redistribution, and none prohibiting it either** — the API is open without "
            "authentication, and we include the derived artifact in this repository only "
            "for convenience; that does not mean permission was granted. We will remove "
            "it if the rights holder asks."
        ),
        "specs": records,
        "_coverage": [
            {
                "provider": PROVIDER,
                "profiles": len(profiles),
                "joined": joined,
                "records": len(records),
                "mirrorAgreed": agreed,
                "mirrorDisagreed": disagreed,
                "unmatched": len(unmatched),
                "droppedConstantFields": list(DROPPED_CONSTANT_FIELDS),
                "note": (
                    "`mirrorAgreed` counts the entries whose cpu and ram in the catalog "
                    "match the mirror — two independent sources stating the same value, "
                    "which is a cross-check. `unmatched` is the profiles present in the "
                    "catalog but absent from the mirror; they are not included (with "
                    "nothing to join to, there is no way to answer)."
                ),
            }
        ],
        "_source": [
            {
                "source": "ibm-global-catalog",
                "pin_kind": "digest",
                "pin": "(고정 불가)",
                "url": SOURCES["ibm-global-catalog"].url,
                "sha256": digest,
                "bytes": None,
                "fetched_at": None,
                "note": (
                    "The API has no version, so reproduction is impossible in principle. "
                    "We record the sha256 of the response we received, so **a changed "
                    "fact is never missed.**"
                ),
            }
        ],
    }
    from perfkb.dataset import schema

    write_dataset(output, payload, schema())
    print(f"IBM 성능 신호: {len(records)}건 → {output}")
    print(
        f"  프로필 {len(profiles)} · 미러 조인 {joined} · cpu/ram 일치 {agreed}"
        f" · 불일치 {disagreed} · 미러에 없어 제외 {len(unmatched)}",
        file=sys.stderr,
    )
    return payload
