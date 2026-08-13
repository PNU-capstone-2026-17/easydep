# speckb — CSP VM 카탈로그 원본

AWS·Azure·GCP가 발행한 응답 본문 그대로. 정규화·병합·필드명 통일 없음.
gzip은 저장 압축일 뿐이며 내용은 바꾸지 않는다.

수집일 2026-08-13. 파일 863개, 41MB (AWS 1.5MB · Azure 36MB · GCP 3.1MB).
모든 `.json.gz`에 `.provenance.json` 사이드카가 있고, `sha256`·`bytes`는
**압축 전 원본 응답 바이트** 기준이다.

무가공 확인 방법 — 사이드카의 `url`을 다시 받아 저장본과 바이트 비교:

```
gzip -dc raw/aws/region_index.json.gz | sha256sum
python -c "import json;print(json.load(open('raw/aws/region_index.json.gz.provenance.json'))['sha256'])"
```

---

## AWS

> **`raw/aws/`는 커밋하지 않는다** (`.gitignore`). AWS 약관이 가격 데이터 재배포를
> 명시적으로 금지하기 때문이며, 문구가 없는 Azure·GCP와 다르다. 아래 수치는 실제
> 수집 결과이고, 재생성은 명령 하나다:
>
> ```
> python -m app.core.cloudkb.speckb.fetch_aws
> ```
>
> 리전 표시명 매핑(`aws_locations.json`)은 가격이 없어서 커밋한다. 그 덕에 재수집 시
> 106회 Range 요청을 건너뛴다.

### EC2 온디맨드 Linux 피드

`raw/aws/ondemand-linux/<region-code>.json.gz` — 리전당 한 파일, 106개 리전, 23,978건, 인스턴스 타입 1,340종.

```
https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/{Location}/Linux/index.json
```

인증 없음. 파일 최상위 키: `manifest` · `regions` · `sets`.
레코드는 `regions.<Location>.<rateCode>` 아래에 있다.

| 필드 | |
|---|---|
| `rateCode` | 레코드 키 |
| `price` | 시간당 USD (문자열) |
| `Location` | AWS 리전 표시명 |
| `Instance Type` | 예: `m5n.12xlarge` |
| `Instance Family` | 예: `General purpose` |
| `vCPU` | 문자열 |
| `Memory` | 예: `192 GiB` |
| `Storage` | 예: `EBS only` |
| `Network Performance` | 예: `50 Gigabit` |
| `Operating System` | `Linux` |
| `Pre Installed S/W` | |
| `License Model` | |
| `plc:OperatingSystem` `plc:InstanceFamily` | 콘솔 표시용 중복 필드 |

`manifest`: `serviceId` `accessType` `hawkFilePublicationDate` `ETLIngestionTriggerDate` `currencyCode` `source`

**vCPU·메모리·가격이 한 레코드에 함께 있다.**

### 리전 인덱스

`raw/aws/region_index.json.gz` — 리전 106개의 코드와 벌크 파일 URL.

```
https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/region_index.json
```

### 리전 표시명 매핑 (파생 — 벤더 원본 아님)

`aws_locations.json`. 콘솔 피드가 리전 코드가 아니라 표시명으로 경로를 잡기 때문에 필요하다.
리전별 벌크 Price List 파일 앞부분에 Range 요청을 걸어 첫 `location` 값을 뽑았다.

```
https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/<region>/index.json
```

우리가 조립한 파일이므로 `raw/` 밖에 둔다.

> 두 AWS 소스가 같은 리전을 다르게 부른다. 벌크는 `eu-central-2`를 `Europe (Zurich)`로,
> 콘솔 피드는 `EU (Zurich)`로만 받는다(전자는 404). GovCloud도 벌크 `AWS GovCloud (US-West)` /
> 피드 `AWS GovCloud (US)`. 실제로 어긋난 9개 리전은 `raw/aws/manifest.json`의
> `feed_name_differs_from_bulk`에 있다.

---

## Azure

### Retail Prices API

`raw/azure/retail-prices/<armRegionName>/page-NNNN.json.gz` — 리전별 디렉터리, 응답 페이지당 한 파일.
62개 리전, 675페이지, 644,061건.

```
https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview
  &$filter=serviceName eq 'Virtual Machines' and armRegionName eq '<region>'
```

인증 없음. 페이지 최상위 키: `BillingCurrency` `CustomerEntityId` `CustomerEntityType` `Count` `Items` `NextPageLink`.

`Items[]` 필드:

| 필드 | |
|---|---|
| `armSkuName` | 예: `Standard_D14` |
| `skuName` `productName` `meterName` | |
| `skuId` `productId` `meterId` `serviceId` | |
| `serviceName` `serviceFamily` | `Virtual Machines` / `Compute` |
| `armRegionName` `location` | |
| `retailPrice` `unitPrice` `currencyCode` `tierMinimumUnits` | |
| `unitOfMeasure` | |
| `type` | `Consumption` · `Reservation` · `DevTestConsumption` |
| `reservationTerm` | |
| `savingsPlan[]` | `term` `retailPrice` `unitPrice` |
| `effectiveStartDate` `effectiveEndDate` | |
| `isPrimaryMeterRegion` | |

**vCPU·메모리가 없다.** `armSkuName`만 주고 코어 수는 알려주지 않는다.

> 예약 인스턴스 레코드는 `unitOfMeasure`가 `1 Hour`인데 `retailPrice`는 기간 총액이다
> (1년이면 8,760시간분). 저장된 값은 해석 전 원본이다.

### 가격 계산기

`raw/azure/calculator-virtual-machines.json.gz` — 단일 파일 9.6MB, 전 리전 포함.

```
https://azure.microsoft.com/api/v3/pricing/virtual-machines/calculator/?culture=en-us&discount=mosp
```

인증 없음. 최상위 키: `offers`(3,496) `skus`(45,612) `regions`(62) `resources`(1,956)
`sizesPayGo` `sizesOneYear` `sizesThreeYear` `sizesFiveYear` `sizesSavingsOneYear`
`sizesSavingsThreeYear` `softwareLicenses` `operatingSystems` `linuxTypes` `windowsTypes`
`tiers` `dropdown` `billingOptions` `computePaymentOptions` `subscriptionOptions`
`discounts` `schema` `responseId` `responseTime`

`offers.<slug>` 필드:

| 필드 | |
|---|---|
| `cores` | vCPU 수 |
| `ram` | GiB |
| `series` | 예: `Dv5` |
| `diskSize` | |
| `gpu` | |
| `isVcpu` `isHidden` `isOnPrem` `availableForML` | |
| `offerType` `pricingTypes` | |
| `prices.perhour.<region-slug>.value` | 시간당 USD |

`skus.<name>` 값 키: `payg` `spot` `one-year` `three-year` `five-year` `sv-one-year`
`sv-three-year` `ahb` `ahbspot` `ahbone-year` `ahbthree-year` `ahbfive-year`
`ahbsv-one-year` `ahbsv-three-year` 및 구독 결합형(`paygone-year-subscription` 등)

`regions[]`: `slug` `displayName`

**여기에만 cores·ram이 있다.** 대신 meterId·예약가가 없어서 Retail API와 둘 다 필요하다.

> `regions[].slug`는 `armRegionName`이 아니다 — slug `us-east` ↔ ARM `eastus`로 어순이 뒤집힌다.
> `displayName`("East Asia")에서 공백을 없애고 소문자로 내리면 `armRegionName`이 된다.

---

## GCP

**사양과 가격이 다른 API로 분리돼 있다.** 두 파일 어느 쪽도 단독으로 vCPU+가격을 주지 않는다.

### Compute Engine 머신 타입 (사양)

`raw/gcp/machine-types-aggregated/page-NNNN.json.gz` — 72페이지, 존 130개, 35,698건.

```
https://compute.googleapis.com/compute/v1/projects/{project}/aggregated/machineTypes
```

인증: OAuth Bearer (`gcloud auth print-access-token`). API 키 불필요.
페이지 최상위: `kind` `id` `items` `selfLink` `nextPageToken`.
레코드는 `items.zones/<zone>.machineTypes[]` 아래에 있다.

| 필드 | |
|---|---|
| `name` | 예: `a2-highgpu-1g` |
| `guestCpus` | vCPU 수 |
| `memoryMb` | MB |
| `description` | |
| `architecture` | |
| `isSharedCpu` | |
| `accelerators[]` | `guestAcceleratorType` `guestAcceleratorCount` |
| `bundledLocalSsds` | |
| `maximumPersistentDisks` `maximumPersistentDisksSizeGb` | |
| `imageSpaceGb` | |
| `deprecated` | |
| `zone` `selfLink` `id` `kind` `creationTimestamp` | |

**가격 필드가 하나도 없다.**

### Cloud Billing Catalog SKU (가격)

`raw/gcp/billing-skus-compute-engine/page-NNNN.json.gz` — 7페이지, 32,242건.

```
https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus
```

인증: OAuth Bearer. 서비스 ID `6F81-5844-456A` = Compute Engine.

| 필드 | |
|---|---|
| `skuId` `name` | |
| `description` | 예: `Spot Preemptible E2 Custom Instance Core running in Paris` |
| `category.resourceFamily` | 예: `Compute` |
| `category.resourceGroup` | `CPU` `RAM` `GPU` `N1Standard` OS 라이선스 등 |
| `category.usageType` | `OnDemand` `Preemptible` `Commit1Yr` `Commit3Yr` `CmtCudPremium` |
| `category.serviceDisplayName` | |
| `serviceRegions[]` | |
| `pricingInfo[].pricingExpression.usageUnit` `usageUnitDescription` | |
| `pricingInfo[].pricingExpression.tieredRates[].startUsageAmount` | |
| `pricingInfo[].pricingExpression.tieredRates[].unitPrice` | `currencyCode` `units` `nanos` |
| `pricingInfo[].pricingExpression.baseUnit` `baseUnitDescription` `baseUnitConversionFactor` | |
| `pricingInfo[].effectiveTime` `summary` `currencyConversionRate` | |
| `geoTaxonomy` | `type` `regions[]` |
| `serviceProviderName` | |

**머신 타입 이름이 없다.** `description` 문자열로만 표현되고, 코어와 램이 별도 SKU로 쪼개져 있다.
Compute Engine 전체 SKU라 디스크·네트워크·OS 라이선스도 포함한다.

---

## 요약

| | vCPU·메모리 | 가격 | 한 레코드에 함께 |
|---|---|---|---|
| AWS 온디맨드 피드 | ✅ | ✅ | ✅ |
| Azure Retail Prices | ❌ | ✅ | ❌ |
| Azure 가격 계산기 | ✅ | ✅ | ✅ |
| GCP machineTypes | ✅ | ❌ | ❌ |
| GCP Billing SKU | ❌ | ✅ | ❌ |

수집 결과 수치는 `raw/<csp>/manifest.json`에 있다.
