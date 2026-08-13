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
68개 리전, 661,563건. (81개 리전에 질의해 13개는 결과 없음)

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

### Resource SKUs API (스펙)

`raw/azure/resource-skus/page-NNNN.json.gz` — 1페이지, 72,550건.
포털의 VM 크기 선택 화면이 쓰는 소스다.

```
https://management.azure.com/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus
  ?api-version=2021-07-01
```

**인증 필요** — `az login` 후 `az account get-access-token`의 Bearer 토큰.
페이지 최상위 키: `value` `nextLink`.

`resourceType` 분포: `virtualMachines` 64,970 · `disks` 4,405 ·
`hostGroups/hosts` 2,818 · `snapshots` 201 · `availabilitySets` 156.
VM 고유 이름 1,501종.

레코드 필드: `resourceType` `name` `tier` `size` `family` `locations`
`locationInfo[]`(`location` `zones` `zoneDetails`) `capabilities[]` `restrictions[]`

`name`이 **ARM SKU 이름 그대로**다 — `Standard_D4s_v5`. Retail의 `armSkuName`과 이 값으로 붙는다.

`capabilities[]`는 `{name, value}` 배열이고 VM에서 48종이 나온다. 요청 항목 대응
(전부 64,970/64,970 = 100% 보유):

| 항목 | capability | 예 (`Standard_D4s_v5`) |
|---|---|---|
| vCPU | `vCPUs` | `4` |
| RAM | `MemoryGB` | `16` |
| 데이터 디스크 | `MaxDataDiskCount` | `8` |
| 최대 IOPS | `UncachedDiskIOPS` | `6400` |
| 로컬 스토리지 | `MaxResourceVolumeMB` | `0` (Dsv5는 임시 디스크 없음) |
| 프리미엄 디스크 | `PremiumIO` | `True` |

나머지 42종: `ACUs` `AcceleratedNetworkingEnabled` `CachedDiskBytes`
`CombinedTempDiskAndCachedIOPS` `CombinedTempDiskAndCachedReadBytesPerSecond`
`CombinedTempDiskAndCachedWriteBytesPerSecond` `CapacityReservationSupported`
`ConfidentialComputingType` `CpuArchitectureType` `DiskControllerTypes`
`EncryptionAtHostSupported` `EphemeralOSDiskSupported` `GPUs` `HibernationSupported`
`HyperVGenerations` `LowPriorityCapable` `MaxNetworkInterfaces`
`MaxWriteAcceleratorDisksAllowed` `MemoryPreservingMaintenanceSupported`
`NvmeDiskSizeInMiB` `NvmeMaxReadBytesPerSecond` `NvmeMaxReadIops`
`NvmeMaxWriteBytesPerSecond` `NvmeMaxWriteIops` `NvmeSizePerDiskInMiB` `OSVhdSizeMB`
`ParentSize` `RdmaEnabled` `RetirementDateUtc` `ScaleOutType` `SubgroupSize`
`SubgroupType` `SupportedCapacityReservationTypes` `SupportedEphemeralOSDiskPlacements`
`SupportedVirtualizationTypes` `TrustedLaunchDisabled` `UltraSSDAvailable`
`UncachedDiskBytesPerSecond` `VMDeploymentTypes` `vCPUsAvailable`
`vCPUsConstraintsAllowed` `vCPUsPerCore`

**가격이 없다.** 비용은 Retail Prices 쪽이다.

> `locations` 값의 대소문자가 섞여 있다 — 같은 응답에 `KoreaCentral`과 `australiaeast`가
>함께 나온다. Retail 질의에 쓸 때는 소문자로 맞춰야 한다(`azure_regions.json`이 그 역할).

### 리전 목록 (파생 — 벤더 원본 아님)

`azure_regions.json`. Retail을 리전별로 받을 때 쓴다. Resource SKUs의 `locations`를
소문자로 맞춘 값과, 이미 Retail을 받아 둔 리전의 **합집합**이다.

합집합인 이유: 일반 구독의 Resource SKUs에는 US GovCloud가 안 나오는데(별도 클라우드)
공개 Retail API에는 `usgovarizona` 등의 가격이 있다. SKUs 결과만 쓰면 그 셋이 사라진다.

가격이 없는 리전 이름 목록이라 `raw/` 밖에 두고 커밋한다.

### 가격 계산기를 왜 안 쓰는가

`azure.microsoft.com/api/v3/pricing/virtual-machines/calculator/`도 cores·ram을 주지만
받지 않는다. 두 가지가 부족하다.

첫째, 데이터 디스크 수·IOPS·프리미엄 디스크 지원 여부가 없다. compute offer의 필드는
`cores` `ram` `diskSize` `gpu` `series` `isVcpu` `isHidden` `offerType` `pricingTypes`
`prices` `availableForML` 11종이 전부다.

둘째, **ARM SKU 이름을 주지 않는다.** 키가 `linux-d8adsv5-standard` 꼴인데 Terraform에는
`Standard_D8ads_v5`가 들어가야 한다. 슬러그→ARM 변환을 규칙으로 시도하면 1,435개 중
983개(68.5%)만 맞는다 — `d11s`의 실제 이름은 `Standard_DS11`로 s가 앞으로 가고,
`dc128edsv6`는 `Standard_DC128eds_v6`로 DC가 대문자다. 나머지 452개는 손으로 예외 표를
만들어야 해서 하지 않는다.

---

## GCP

> **`raw/gcp/`는 커밋하지 않는다** (`.gitignore`). `machineTypes` 응답의 `selfLink`마다
> 수집에 쓴 프로젝트 ID가 들어 있다(1페이지당 506회). 응답 본문 자체라 지우려면 원본을
>고쳐야 하는데 그러면 무가공 보장이 깨지므로, 원본을 그대로 두고 커밋만 하지 않는다.
> 아래 수치는 실제 수집 결과이고, 재생성은 명령 하나다:
>
> ```
> python -m app.core.cloudkb.speckb.fetch_gcp
> ```

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

| | vCPU·메모리 | 디스크·IOPS | 가격 | 인증 |
|---|---|---|---|---|
| AWS 온디맨드 피드 | ✅ | ❌ | ✅ | 없음 |
| Azure Resource SKUs | ✅ | ✅ | ❌ | **필요** |
| Azure Retail Prices | ❌ | ❌ | ✅ | 없음 |
| GCP machineTypes | ✅ | 일부 | ❌ | **필요** |
| GCP Billing SKU | ❌ | ❌ | ✅ | **필요** |

Azure와 GCP는 스펙과 가격이 다른 API로 갈라져 있다. 붙이는 키는 각각
`armSkuName`↔`name`(Azure), 머신 타입 이름↔SKU `description` 문자열(GCP)이다.
AWS만 한 레코드에 vCPU·메모리·가격이 함께 있다.

수집 결과 수치는 `raw/<csp>/manifest.json`에 있다.
