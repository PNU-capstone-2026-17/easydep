"""Azure Virtual Machines 카탈로그 원본 수집.

    python -m app.core.cloudkb.speckb.fetch_azure [--refresh] [--region koreacentral]

## 왜 엔드포인트가 두 개인가

한쪽만으로는 VM 목록이 완성되지 않는다. 둘 다 받는다.

**Retail Prices API** (`prices.azure.com`) — 공식 가격 API. meterId, 스팟,
예약 인스턴스, Savings Plan, Windows/Linux 미터가 전부 들어온다. 인증이 필요
없다. 다만 **vCPU와 메모리가 없다**. `armSkuName`(예: `Standard_D14`)만 주고
그 SKU가 몇 코어인지는 말해주지 않는다.

**가격 계산기 JSON** (`azure.microsoft.com/api/v3/...`) — `offers` 3,496개에
`cores`·`ram`·`series`와 전 리전 시간당 가격이 한 파일(9.6MB)에 들어 있다.
대신 meterId도 예약가도 없다.

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

CALCULATOR_URL = (
    "https://azure.microsoft.com/api/v3/pricing/virtual-machines/calculator/"
    "?culture=en-us&discount=mosp"
)
RETAIL_BASE = "https://prices.azure.com/api/retail/prices"
RETAIL_API_VERSION = "2023-01-01-preview"


def out_dir() -> Path:
    return raw_dir() / "azure"


def fetch_calculator(refresh: bool) -> dict:
    destination = out_dir() / "calculator-virtual-machines.json.gz"
    if refresh or not already_have(destination):
        print("[azure] 가격 계산기 JSON 받는 중 … (약 9.6MB)")
        response = get(CALCULATOR_URL)
        if not response.ok:
            raise RuntimeError(f"계산기 JSON 요청 실패: HTTP {response.status}")
        save_gz(destination, response.body, CALCULATOR_URL, headers=response.headers)
    return load_gz_json(destination)


def region_codes(calculator: dict) -> list[str]:
    """리전 목록을 Azure 응답에서 뽑는다 — 저장소 데이터를 쓰지 않는다.

    계산기 JSON의 `regions`는 `{"slug": "asia-pacific-east",
    "displayName": "East Asia"}` 꼴이다. Retail API가 쓰는 `armRegionName`은
    **slug가 아니라 displayName** 쪽과 대응한다 — slug는 `us-east`인데
    armRegionName은 `eastus`라 어순이 뒤집혀 있어서, 하이픈만 지우면 없는
    리전이 만들어진다.

    displayName에서 공백을 없애고 소문자로 내리면 armRegionName이 된다
    ("East Asia" → eastasia, "Australia Central 2" → australiacentral2).
    다만 이건 관찰된 규칙이지 Azure가 보장한 계약이 아니므로, 호출부에서 첫
    페이지가 비었는지 확인해 틀린 후보를 걸러낸다.
    """
    codes: list[str] = []
    for entry in calculator.get("regions", []):
        if not isinstance(entry, dict):
            continue
        display_name = entry.get("displayName")
        if display_name:
            codes.append(display_name.replace(" ", "").lower())
    return sorted(set(codes))


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
    args = parser.parse_args(argv)

    calculator = fetch_calculator(args.refresh)
    offers = calculator.get("offers", {})
    print(f"[azure] 계산기 offers {len(offers)}개, regions {len(calculator.get('regions', []))}개")

    regions = [args.region] if args.region else region_codes(calculator)
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
                    "key": "pricing-calculator-virtual-machines",
                    "url": CALCULATOR_URL,
                    "auth": "none",
                    "note": "cores·ram·series 보유, meterId·예약가 없음",
                },
                {
                    "key": "retail-prices",
                    "url": f"{RETAIL_BASE}?api-version={RETAIL_API_VERSION}"
                    "&$filter=serviceName eq 'Virtual Machines' and armRegionName eq '<region>'",
                    "auth": "none",
                    "note": "meterId·스팟·예약가 보유, vCPU·메모리 없음. 페이지별 한 파일",
                },
            ],
            "calculator_offers": len(offers),
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
