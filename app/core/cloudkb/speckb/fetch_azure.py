"""Azure Virtual Machines 카탈로그 원본 수집.

    python -m app.core.cloudkb.speckb.fetch_azure [--refresh] [--region koreacentral]

## 왜 엔드포인트가 두 개인가

한쪽만으로는 VM을 특정할 수 없다. 둘 다 받는다.

**Retail Prices API** (`prices.azure.com`) — 공식 가격 API. meterId, 스팟,
예약 인스턴스, Savings Plan, Windows/Linux 미터가 전부 들어온다. 인증이 필요
없다. 다만 **vCPU와 메모리가 없다** — 644,061건의 필드 23종을 전수 조사해
확인했다. `armSkuName`(예: `Standard_D14`)만 주고 몇 코어인지는 말하지 않는다.

**Resource SKUs API** (`management.azure.com`) — 포털의 VM 크기 선택 화면이
쓰는 소스. `vCPUs` `MemoryGB` `MaxDataDiskCount` `UncachedDiskIOPS`
`MaxResourceVolumeMB` `PremiumIO`를 준다. 대신 가격이 없다. 인증이 필요하다.

둘은 `Standard_D8ads_v5`라는 **같은 ARM 이름**으로 붙는다 — Retail의
`armSkuName`과 SKUs의 `name`이다.

## 가격 계산기를 왜 안 쓰는가

`azure.microsoft.com/api/v3/pricing/virtual-machines/calculator/`도 cores·ram을
주지만 두 가지가 부족해서 뺐다.

첫째, 데이터 디스크 수·IOPS·프리미엄 디스크 지원 여부가 없다(compute offer의
필드는 cores·ram·diskSize·gpu·series 등 11종이 전부).

둘째, **ARM SKU 이름을 주지 않는다.** 키가 `linux-d8adsv5-standard` 꼴인데
Terraform에는 `Standard_D8ads_v5`가 들어가야 한다. 슬러그→ARM 변환을 규칙으로
시도하면 1,435개 중 983개(68.5%)만 맞는다 — `d11s`의 실제 이름은 `Standard_DS11`로
s가 앞으로 가고 `dc128edsv6`는 `Standard_DC128eds_v6`로 DC가 대문자다. 나머지
452개는 손으로 예외 표를 만들어야 하는데 그건 지어낸 매핑이라 하지 않는다.

## 분량

서울 리전 하나가 13페이지·12,335건·7.5MB다(실측). 62개 리전이면 원본 약
529MB, gzip 후 약 40MB. AWS(~5MB)나 GCP(~20MB)보다 한 자릿수 크다.

## 주의 — 저장된 가격은 해석 전 값이다

Retail API의 예약 인스턴스 레코드는 `unitOfMeasure`가 "1 Hour"인데 실제
`retailPrice`는 기간 총액이다(1년이면 8,760시간분). speckb는 값을 해석하지
않고 원본만 저장하므로 이 함정에 걸리지 않지만, 이 데이터를 쓰는 쪽은 알아야
한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

try:
    from ._http import (
        already_have,
        enable_utf8_stdout,
        get,
        load_gz_json,
        raw_dir,
        save_gz,
        write_manifest,
    )
except ImportError:  # pragma: no cover - 단독 실행 경로
    from _http import (  # type: ignore[no-redef]
        already_have,
        enable_utf8_stdout,
        get,
        load_gz_json,
        raw_dir,
        save_gz,
        write_manifest,
    )

RETAIL_BASE = "https://prices.azure.com/api/retail/prices"
RETAIL_API_VERSION = "2023-01-01-preview"

ARM_RESOURCE = "https://management.azure.com/"
SKUS_URL = (
    "https://management.azure.com/subscriptions/{subscription}"
    "/providers/Microsoft.Compute/skus?api-version=2021-07-01"
)


def out_dir() -> Path:
    return raw_dir() / "azure"


def regions_path() -> Path:
    """armRegionName 목록.

    `aws_locations.json`과 같은 역할이다. Resource SKUs 응답의 `locations`에서
    만들며, 가격이 없는 리전 이름 목록이라 커밋해도 된다. 이 파일이 있으면
    Retail 수집은 az login 없이도 돌아간다.
    """
    return Path(__file__).resolve().parent / "azure_regions.json"


def load_regions() -> list[str]:
    path = regions_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("regions", [])


def collected_regions() -> set[str]:
    """이미 Retail 데이터를 받아 둔 리전."""
    root = out_dir() / "retail-prices"
    if not root.exists():
        return set()
    return {entry.name for entry in root.iterdir() if entry.is_dir()}


def write_regions(regions: list[str], source: str) -> None:
    """Resource SKUs의 locations에 이미 받아 둔 리전을 합쳐 기록한다.

    합집합이어야 하는 이유: 일반 구독의 Resource SKUs 조회에는 US GovCloud가
    나오지 않는다(별도 클라우드다). 그런데 공개 Retail API에는 usgovarizona·
    usgovtexas·usgovvirginia 가격이 있고 이미 받아 뒀다. SKUs 결과만 쓰면 그 셋이
    목록에서 빠져 다음 실행부터 집계에서 사라진다 — 실제로 한 번 그렇게 돼서
    총계가 644,061에서 640,659로 줄었다.
    """
    regions_path().write_text(
        json.dumps(
            {
                "_note": (
                    "Retail Prices를 리전별로 받을 때 쓰는 armRegionName 목록. "
                    "Resource SKUs 응답의 locations를 소문자로 맞춘 값과, 이미 "
                    "Retail을 받아 둔 리전의 합집합이다. Azure가 locations를 "
                    "대소문자 뒤섞어 주기 때문에 소문자로 맞춘다. "
                    "가격은 들어 있지 않다."
                ),
                "_source": source,
                "regions": sorted(set(regions) | collected_regions()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# winget으로 az를 막 설치하면 이미 떠 있는 셸의 PATH에는 아직 안 잡힌다.
# 터미널을 다시 열게 하는 대신 표준 설치 경로도 같이 본다.
AZ_FALLBACK_PATHS = (
    r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
    r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
)


def az_executable() -> str | None:
    found = shutil.which("az") or shutil.which("az.cmd")
    if found:
        return found
    for candidate in AZ_FALLBACK_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _az(*args: str) -> str | None:
    executable = az_executable()
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, *args], capture_output=True, text=True, timeout=180, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def azure_credentials() -> tuple[str, str] | None:
    """(토큰, 구독 ID). 준비가 안 됐으면 이유를 찍고 None."""
    if az_executable() is None:
        print("[azure] az CLI가 없어 Resource SKUs를 건너뛴다.")
        print("        winget install -e --id Microsoft.AzureCLI  후  az login")
        return None
    raw = _az("account", "get-access-token", "--resource", ARM_RESOURCE)
    if not raw:
        print("[azure] Azure 로그인 상태가 아니라 Resource SKUs를 건너뛴다. `az login` 필요.")
        return None
    try:
        token = json.loads(raw)["accessToken"]
    except (ValueError, KeyError):
        print("[azure] 액세스 토큰을 해석하지 못해 Resource SKUs를 건너뛴다.")
        return None
    subscription = _az("account", "show", "--query", "id", "-o", "tsv")
    if not subscription:
        print("[azure] 구독을 확인하지 못해 Resource SKUs를 건너뛴다.")
        return None
    return token, subscription


def fetch_resource_skus(refresh: bool) -> tuple[int, int, list[str]]:
    """Compute Resource SKUs 전체를 페이지별 파일로 저장한다.

    `$filter`는 location만 지원한다. 필터를 걸지 않으면 구독이 볼 수 있는 전
    리전이 한 번에 나오고 각 항목의 `locations`에 리전이 들어 있다 — 내가 만든
    리전 목록에 의존하지 않으므로 이 편이 정확하다.

    VM뿐 아니라 디스크·가용성 집합 등 Compute 전체 SKU가 온다. 거르지 않는다.
    """
    credentials = azure_credentials()
    if credentials is None:
        return 0, 0, []
    token, subscription = credentials
    print(f"[azure] 구독 {subscription}로 Resource SKUs 수집")

    page_dir = out_dir() / "resource-skus"
    url: str | None = SKUS_URL.format(subscription=subscription)
    headers = {"Authorization": f"Bearer {token}"}
    page = 0
    total = 0
    locations: set[str] = set()

    while url:
        page += 1
        destination = page_dir / f"page-{page:04d}.json.gz"
        if already_have(destination) and not refresh:
            payload = load_gz_json(destination)
        else:
            response = get(url, headers=headers)
            if not response.ok:
                raise RuntimeError(f"Resource SKUs {page}페이지 실패: HTTP {response.status}")
            save_gz(destination, response.body, url, headers=response.headers)
            payload = json.loads(response.body.decode("utf-8"))
        entries = payload.get("value", [])
        total += len(entries)
        for entry in entries:
            # Azure가 `locations`를 대소문자 뒤섞어 돌려준다 — 같은 응답 안에
            # "AustraliaCentral"과 "australiaeast"가 함께 나온다(137개 중 59개가
            # 대문자 시작). Retail의 `armRegionName eq '<region>'` 필터는 소문자
            # armRegionName을 받으므로 여기서 맞춰 둔다. 저장되는 원본 파일은
            # 손대지 않고, 질의에 쓸 목록만 정규화한다.
            locations.update(loc.lower() for loc in entry.get("locations") or [])
        url = payload.get("nextLink")

    return page, total, sorted(locations)


def retail_url(region: str) -> str:
    query = (
        f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}'"
    )
    return (
        f"{RETAIL_BASE}?api-version={RETAIL_API_VERSION}"
        f"&$filter={urllib.parse.quote(query)}"
    )


def fetch_region(region: str, refresh: bool) -> tuple[int, int]:
    """한 리전의 모든 페이지를 페이지별 파일로 저장한다.

    페이지를 합쳐 하나로 만들지 않는다. 합치는 순간 그건 가공이다.

    첫 페이지가 비어 있으면 armRegionName 후보가 틀렸다는 뜻이므로 아무것도
    저장하지 않고 (0, 0)을 돌려준다. 빈 파일을 남기면 나중에 "이 리전은 VM이
    없다"는 사실처럼 읽히기 때문이다.
    """
    region_dir = out_dir() / "retail-prices" / region
    url: str | None = retail_url(region)
    page = 0
    items = 0

    while url:
        page += 1
        destination = region_dir / f"page-{page:04d}.json.gz"
        if already_have(destination) and not refresh:
            payload = load_gz_json(destination)
        else:
            response = get(url)
            if not response.ok:
                raise RuntimeError(f"{region} {page}페이지 요청 실패: HTTP {response.status}")
            payload = json.loads(response.body.decode("utf-8"))
            if page == 1 and not payload.get("Items"):
                return 0, 0
            save_gz(destination, response.body, url, headers=response.headers)
        items += len(payload.get("Items", []))
        url = payload.get("NextPageLink")

    return page, items


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Azure VM 카탈로그 원본 수집")
    parser.add_argument("--refresh", action="store_true", help="이미 받은 파일도 다시 받는다")
    parser.add_argument("--region", help="이 리전 하나만 받는다 (예: koreacentral)")
    parser.add_argument("--skus-only", action="store_true", help="Resource SKUs만 받는다")
    args = parser.parse_args(argv)

    sku_pages, sku_total, locations = fetch_resource_skus(args.refresh)
    if sku_pages:
        print(f"[azure] Resource SKUs {sku_pages}페이지, {sku_total}건, 리전 {len(locations)}개")
        write_regions(locations, SKUS_URL.format(subscription="<subscription>"))

    if args.skus_only:
        print("[azure] --skus-only 이므로 Retail은 건너뛴다")
        return 0

    # 파일을 단일 출처로 삼는다. write_regions가 SKUs의 locations와 이미 받아 둔
    # 리전을 합쳐 써 두므로, locations를 직접 쓰면 GovCloud가 빠진다.
    regions = [args.region] if args.region else load_regions()
    if not regions:
        print(
            "[azure] 리전 목록이 없다. az login 후 한 번 실행하면 "
            "azure_regions.json이 만들어진다.",
            file=sys.stderr,
        )
        return 1
    print(f"[azure] Retail Prices 수집 대상 {len(regions)}개 리전")

    per_region: dict[str, dict[str, int]] = {}
    failed: list[str] = []
    empty: list[str] = []
    total_items = 0
    for position, region in enumerate(sorted(regions), start=1):
        try:
            pages, items = fetch_region(region, args.refresh)
        except RuntimeError as error:
            # 리전 하나가 죽어도 나머지는 계속 받는다.
            failed.append(region)
            print(f"[azure] ({position}/{len(regions)}) {region}: 실패 — {error}")
            continue
        if pages == 0:
            empty.append(region)
            print(f"[azure] ({position}/{len(regions)}) {region}: 결과 없음 — 리전명 불일치 추정")
            continue
        per_region[region] = {"pages": pages, "items": items}
        total_items += items
        print(f"[azure] ({position}/{len(regions)}) {region}: {pages}페이지 {items}건")

    write_manifest(
        out_dir() / "manifest.json",
        {
            "provider": "azure",
            "sources": [
                {
                    "key": "compute-resource-skus",
                    "url": SKUS_URL.format(subscription="<subscription>"),
                    "auth": "OAuth Bearer (az account get-access-token)",
                    "note": "vCPUs·MemoryGB·MaxDataDiskCount·UncachedDiskIOPS·"
                    "MaxResourceVolumeMB·PremiumIO 등 스펙. 가격 없음. "
                    "name 필드가 ARM SKU 이름(Standard_D8ads_v5)",
                },
                {
                    "key": "retail-prices",
                    "url": f"{RETAIL_BASE}?api-version={RETAIL_API_VERSION}"
                    "&$filter=serviceName eq 'Virtual Machines' and armRegionName eq '<region>'",
                    "auth": "none",
                    "note": "meterId·스팟·예약가 보유, vCPU·메모리 없음. 페이지별 한 파일. "
                    "armSkuName으로 Resource SKUs의 name과 붙는다",
                },
            ],
            "resource_skus": {"pages": sku_pages, "records": sku_total},
            "regions_attempted": len(regions),
            "regions_failed": sorted(failed),
            "regions_empty": sorted(empty),
            "retail_items_total": total_items,
            "per_region": dict(sorted(per_region.items())),
        },
    )
    print(
        f"[azure] 완료 — {len(per_region)}개 리전, 총 {total_items}건, "
        f"결과 없음 {len(empty)}개, 실패 {len(failed)}개"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
