# perfkb 필드 축 재설계 계획 (2026-07-29)

**실행 전 계획입니다.** 이 문서 시점에 코드·산출물은 바뀌지 않았습니다.

`kb-source-atlas-2026-07-29.md`를 쓰면서 전수 대조를 하다 나온 것입니다. 소스 하나의
결함이 아니라 **선정 절차가 없다**는 문제라, 하드닝이 아니라 축을 다시 긋습니다.

---

## 1. 문제

perfkb의 축 경계가 **암묵적**입니다.

`perfkb/parsers/details.py`의 *"필요한 키만 정규식으로 뽑고 … 뽑는 키를 소수로 유지하는
것 자체가 안전장치다"*는 **파싱 방법**에 대한 정당한 설명입니다. Go `%v` 포맷이
위험하므로 전체를 파싱하지 않는다는 것은 맞습니다. 그런데 그 문장이 **어떤 사실을
KB에 담을지**를 정하는 절차 자리까지 대신 차지하고 있었습니다. 둘은 다른 질문입니다.

    "이 문자열을 어떻게 안전하게 읽나"        ← 답이 있다 (키별 정규식, fail-open)
    "읽을 수 있는 것 중 무엇을 담나"          ← 답이 기록돼 있지 않다

그 결과 셋이 동시에 벌어졌습니다.

**(1) 같은 규율이 소스마다 다르게 적용됐습니다.**
`ibm-perf`는 산출물 `_coverage`에 버린 칸 이름을 적습니다
(`droppedConstantFields: [freqency, status, vcpu_architecture, …]`).
미러 `details`에는 그런 기록이 없습니다. 그래서 무엇을 왜 안 담았는지 아무도 모릅니다.

**(2) 스키마에 칸이 있는데 비어 있습니다.**
`perfkb/schema.json`의 `$defs/spec`에 `maxNics`·`localSsdGB`가 선언돼 있습니다.

    maxNics     aws       0 / 18,564   ← NetworkInfo.MaximumNetworkInterfaces 가 100% 있다
                azure 25,385 / 34,846
                ibm    2,002 /  2,002
    localSsdGB  aws       0 / 18,564   ← InstanceStorageInfo 가 39.9% 있다
                gcp    1,441 / 11,622

**(3) 미러가 어디까지 답하는지 세어 보지 않고 외부 소스를 추가했습니다.**
azure `maxNics`를 채우려고 `azure-compute-docs`를 새로 받아왔는데, 미러 `details`의
`MaxNetworkInterfaces`가 이미 **100%** 채워져 있었습니다. 두 값을 대조한 결과:

    겹침 25,385  →  일치 24,553 · 불일치 832 (3.3%)
    미러에만 있는 것 9,461건
    예: azure+italynorth+standard_b8as_v2      문서표 2  vs  미러 4
        azure+italynorth+standard_nv36ads_a10_v5  문서표 4  vs  미러 8

**커버리지가 낮은 쪽(72.8%)을 골랐고, 3.3% 불일치는 교차 검증 기회였는데 대조하지
않았습니다.** `aws-limits`가 Price List × botocore에 대해 한 일(둘이 같을 때만 담기)을
여기서는 하지 않았습니다.

(1)(2)는 증상이고 **(3)이 뿌리**입니다.

---

## 2. 목표

"뽑을 키를 고른다"를 **전수 분류 → 명시적 선정 → 산출물에 남는 미사용 기록**으로
바꿉니다.

> 판단이 바뀌는 것은 괜찮습니다. **판단하지 않은 칸이 있는 것**이 안 됩니다.

---

## 3. 결정 기록 (2026-07-29, 사용자 승인)

| # | 결정 | 사유 |
|---|---|---|
| D1 | **범위는 perfkb `details`만** | costkb·envkb가 쓰는 미러 컬럼은 42개로 작고 전수 소비 중이라 같은 병이 없습니다. 병이 있는 곳은 `details`입니다 |
| D2 | **`unmapped-axis`는 후보로만 올리고 채택은 별도 결정** | 안 그러면 축 재설계가 스키마 확장으로 번지고, 가이드라인 KB가 스펙 카탈로그가 됩니다(위협 T5) |
| D3 | **azure `maxNics`는 일치하는 것만 담는다** | `aws-limits` 선례. 커버리지는 25,385 → 24,553으로 줄지만 **규율이 하나**가 됩니다. 불일치 832건은 담지 않고 보고합니다 |

D3의 대가를 명시합니다 — **커버리지가 줄어드는 방향의 결정입니다.** 그래도 고른 이유는,
두 소스가 다르게 말하는 값을 하나 골라 담으면 그건 우리 짐작이고, 이 저장소가 가장
경계하는 종류의 값이 되기 때문입니다.

---

## 4. 절차

### 4.1 인벤토리 — 무엇이 있는지 전수로 센다

프로바이더별 `details` 전 키에 대해 넷을 잽니다.

    채움률 (행 단위)  ·  값 종류 수  ·  값 예시  ·  채움률 (타입 단위)

**범위**: aws·azure·gcp·ibm의 **최상위 키 + 중첩 1단**
(`EbsInfo`·`NetworkInfo`·`ProcessorInfo`·`VCpuInfo`·`InstanceStorageInfo`·`GpuInfo`·
`BundledLocalSsds`·`Accelerators`).

**포화 기준 — 중첩 2단에서 멈춥니다.** 실측상 그 아래는 배열 안의 배열이라
`go_field`가 **원리적으로** 스칼라를 못 뽑습니다(`_FIELD` 정규식이 값이 `{`/`[`로
시작하면 `None`을 줍니다). 멈추는 것 자체가 아니라 **멈추는 이유를 `blocked` 사유로
기록하는 것**이 포화의 조건입니다 — 그래야 다음 사람이 같은 벽에 다시 부딪히지 않습니다.

**나머지 다섯(tencent 2,865 · ncp 393 · kt 220 · nhn 71 · openstack 6)은 키 목록만
세어 둡니다.** perfkb 미지원이라 이번 채택 대상이 아니지만, "그 다섯은 왜 성능 데이터가
없나"에 답할 근거가 됩니다. 지금은 "추적하지 않는다"로만 답하고 있습니다.

### 4.2 분류 — 키마다 판정 하나

상호 배타·전수여야 합니다.

| 코드 | 뜻 | 판정에 필요한 근거 |
|---|---|---|
| `constant` | 값 종류 1 — 변별력 0 | 실측 값 종류 수. `ibm-perf`의 `freqency` 규칙 |
| `duplicate:<파일>.<필드>` | 다른 산출물이 이미 담는 사실 | **값 대조 결과**(위협 T2). 어느 파일의 어느 필드인지 적는다 |
| `out-of-axis` | 어느 축에도 자리가 없는 사실 | 한 줄 사유 |
| `unmapped-axis` | 사실인데 스키마에 칸이 없다 | 후보. 어느 축에 붙일지 제안 |
| `blocked:<이유>` | 담고 싶은데 못 담는다 | 왜 못 읽는지 |
| `adopt` | KB 필드로 담는다 | 매핑할 스키마 필드명 |

**분류표는 산문이 아니라 코드 안 상수**로 둡니다(`perfkb/parsers/field_map.py` 등).
문서에 두면 두 번 뒤처집니다 — 이 저장소가 이미 겪은 실패입니다.

**테스트가 전수성을 강제합니다**: 인벤토리에 있는 키가 분류표에 없으면 빌드가 죽습니다.
이게 "조용한 누락이 구조적으로 불가능"의 실제 장치이고, 지금 없는 것이 정확히 이것입니다.

### 4.3 외부 매핑 — 독립 소스가 같은 말을 하는가

`adopt`·`unmapped-axis` 각각에 대해 대조 상대를 적습니다.

| 우리 필드 | 독립 소스 | 겹침 대조 |
|---|---|---|
| azure `maxNics` | `azure-compute-docs` 표 | **측정됨** — 겹침 25,385 · 불일치 832 (3.3%) |
| aws GPU | `ec2-hardware` `nvidia_gpus` | 미측정 |
| gcp `localSsdGB`·GPU | `gcloud-machine-types` · `cyclenerd-gcp-pricing` | 미측정 |
| ibm `maxNics` | `ibm-global-catalog` | 미측정 |

**채택 규칙**: 독립 소스가 둘이면 **일치할 때만 담고 불일치는 담지 않고 보고**합니다
(`aws-limits` 방식, 결정 D3). 하나뿐이면 그 사실을 `_coverage`에 적습니다 —
`azure_mutability`가 "단일 소스다"를 적어 둔 것과 같습니다.

### 4.4 산출물에 남기기

`ibm-perf`의 `droppedConstantFields`를 일반화합니다.

```json
"_coverage": [{"provider": "aws",
  "fields": {"adopted": 9, "constant": 3, "duplicate": 4,
             "out-of-axis": 8, "unmapped-axis": 2, "blocked": 1},
  "notAdopted": [
    {"key": "SupportedUsageClasses", "class": "unmapped-axis",
     "fill": 1.0, "distinct": 4,
     "reason": "스팟 가능 여부 — 가격 축에 자리가 없다"},
    {"key": "PlacementGroupInfo", "class": "out-of-axis",
     "fill": 1.0, "distinct": 2,
     "reason": "배치 전략은 성능 사실이 아니라 배포 옵션이다"}]}]
```

---

## 5. 타당성 위협

| | 위협 | 대응 |
|---|---|---|
| **T1** | `details` 스키마가 조용히 바뀐다 (Go `%v`라 계약이 없다) | 지금 `DetailsMismatch`는 **값**만 지킵니다. **키 집합을 불변식으로** 고정 — 인벤토리와 다르면 빌드 실패 |
| **T2** | "중복" 판정이 실제로 같은 값인가 | `memGiB`는 gcp·azure에 ×1.024 보정이 걸려 있어 **미러 컬럼 ≠ details 원본값**입니다. `duplicate` 판정은 반드시 **값 대조로 확인**하고 불일치 건수를 함께 적습니다 |
| **T3** | 미러와 외부 문서는 다른 스냅샷 | 값이 다를 때 어느 쪽이 최신인지 알 수 없습니다(gcp 가격에서 이미 겪음). 불일치는 **해소하지 말고 기록**합니다 |
| **T4** | 18,564는 타입 수가 아니라 (타입 × 리전) | 채움률이 리전 편중으로 왜곡될 수 있습니다. **타입 단위 채움률을 함께** 재고, 두 수가 갈리면 그 사실을 적습니다 |
| **T5** | `unmapped-axis`를 자꾸 채택하면 스키마가 카탈로그가 된다 | 채택 기준에 **"이 값이 사용자 질문에 답하는 데 쓰이나"**를 넣습니다. 결정 D2가 이 위협에 대한 장치입니다 |
| **T6** | 분류가 한 사람의 판단이다 | `out-of-axis`·`unmapped-axis`는 사유를 한 줄로 적게 강제합니다. 사유를 못 쓰면 분류가 아니라 방치입니다 |

---

## 6. 단계

| 단계 | 무엇 | 산출 | 코드 변경 |
|---|---|---|---|
| P0 | 인벤토리 측정 (4.1) | 실측표 | 없음 |
| P1 | 분류표 작성 (4.2) | `field_map.py` + 전수성 테스트 | 있음 (데이터 불변) |
| P2 | `_coverage.fields` 기록 (4.4) | 산출물 메타 | 있음 (데이터 불변) |
| P3 | `adopt` 반영 · 외부 매핑 대조 (4.3) | 필드 추가·재판정 | **데이터 변경** |
| P4 | 키 집합 불변식 (T1) | 테스트 | 있음 |

**P0~P2는 데이터를 바꾸지 않습니다.** 지금 값이 무엇인지 먼저 못 박고, 판정은 그 다음에
합니다. 순서를 뒤집으면 "고쳤다"를 증명할 기준선이 없어집니다 — `sources.py`가 핀을
박은 것과 같은 이유입니다.

---

## 7. 이미 잰 것 (P0 부분 완료)

**최상위 키만** 쟀습니다. 중첩 1단·ibm·나머지 다섯·타입 단위 채움률은 **미측정**입니다.

측정 조건: `namespace = system`, cb-tumblebug `v0.12.25`, 2026-07-29.
`값종류`는 **12개에서 수집을 멈춥니다** — `12+`는 "12 이상"이지 정확한 수가 아닙니다.

### aws — 행 18,564 · 최상위 키 24개 · 사용 7개

| 키 | 채움 | 값종류 | 현재 | 분류 초안 |
|---|---:|---:|---|---|
| `BurstablePerformanceSupported` | 100% | 2 | 사용 | `adopt` |
| `CurrentGeneration` | 100% | 2 | 사용 | `adopt` |
| `BareMetal` | 100% | 2 | 사용 | `adopt` |
| `EbsInfo` | 100% | 12+ | 사용 | `adopt` (중첩 1단 미분류) |
| `NetworkInfo` | 100% | 7 | 사용 | `adopt` (**중첩 1단에 `MaximumNetworkInterfaces` 있음**) |
| `ProcessorInfo` | 100% | 12+ | 사용 | `adopt` |
| `VCpuInfo` | 100% | 12+ | 사용 | `adopt` |
| `InstanceStorageInfo` | 39.9% | 12+ | — | **`adopt` 후보** → `localSsdGB` 칸이 비어 있다 |
| `SupportedUsageClasses` | 100% | 4 | — | **`unmapped-axis`** → 스팟 가능 여부 |
| `GpuInfo` | 3.8% | 12+ | — | `duplicate:tumblebug-perf.gpu*` (T2 확인 필요) |
| `InferenceAcceleratorInfo` | 0.6% | 6 | — | `unmapped-axis` |
| `FpgaInfo` | 0.0% | 3 | — | `unmapped-axis` |
| `InstanceType` | 100% | 12+ | — | `duplicate:tumblebug-cost.specName` |
| `MemoryInfo` | 100% | 12+ | — | `duplicate:tumblebug-cost.memGiB` (**T2 확인 필요** — 보정) |
| `Hypervisor` | 90.1% | 2 | — | `unmapped-axis` (nitro/xen — 세대 신호) |
| `AutoRecoverySupported` | 100% | 2 | — | `out-of-axis` |
| `DedicatedHostsSupported` | 100% | 2 | — | `out-of-axis` |
| `FreeTierEligible` | 100% | 2 | — | `out-of-axis` |
| `HibernationSupported` | 100% | 2 | — | `out-of-axis` |
| `InstanceStorageSupported` | 100% | 2 | — | `duplicate:InstanceStorageInfo` 존재 여부 |
| `PlacementGroupInfo` | 100% | 2 | — | `out-of-axis` |
| `SupportedBootModes` | 100% | 3 | — | `out-of-axis` |
| `SupportedRootDeviceTypes` | 100% | 2 | — | `out-of-axis` |
| `SupportedVirtualizationTypes` | 100% | 2 | — | `out-of-axis` |

### azure — 행 34,846 · 최상위 키 **59개** · 사용 7개

가장 큰 격차입니다. 값 종류가 1인 것이 8개나 됩니다.

| 키 | 채움 | 값종류 | 현재 | 분류 초안 |
|---|---:|---:|---|---|
| `ACUs` | 37.7% | 7 | 사용 | `adopt` |
| `vCPUsPerCore` | 100% | 2 | 사용 | `adopt` |
| `UncachedDiskIOPS` | 100% | 12+ | 사용 | `adopt` |
| `CombinedTempDiskAndCachedIOPS` | 99.5% | 12+ | 사용 | `adopt` |
| `AcceleratedNetworkingEnabled` | 100% | 2 | 사용 | `adopt` |
| `PremiumIO` | 100% | 2 | 사용 | `adopt` |
| `Family` | 100% | 12+ | 사용 | `adopt` |
| `MaxNetworkInterfaces` | **100%** | 7 | — | **`adopt`** → 문서표(72.8%)와 대조 후 일치분만 (D3) |
| `RetirementDateUtc` | 10.8% | 4 | — | **`unmapped-axis`** → 수명주기 축 |
| `UncachedDiskBytesPerSecond` | 100% | 12+ | — | `unmapped-axis` (대역폭 — 우리는 IOPS만) |
| `NvmeMaxReadIops` · `NvmeMaxWriteIops` | 17.6% | 12+ | — | `unmapped-axis` |
| `NvmeMaxReadBytesPerSecond` · `…Write…` | 17.6% | 12+ | — | `unmapped-axis` |
| `NvmeDiskSizeInMiB` · `NvmeSizePerDiskInMiB` | 19.7% | 12+ | — | `unmapped-axis` |
| `CachedDiskBytes` | 30.3% | 12+ | — | `unmapped-axis` |
| `CombinedTempDiskAndCached{Read,Write}BytesPerSecond` | 99.5% | 12+ | — | `unmapped-axis` |
| `GPUs` | 1.6% | 4 | — | `unmapped-axis` (azure GPU 칸이 없다) |
| `RdmaEnabled` | 100% | 2 | — | `unmapped-axis` (HPC 신호) |
| `UltraSSDAvailable` | 28.4% | 2 | — | `unmapped-axis` |
| `MaxWriteAcceleratorDisksAllowed` | 7.5% | 6 | — | `unmapped-axis` |
| `ConfidentialComputingType` | 4.7% | 2 | — | `out-of-axis` |
| `DiskControllerTypes` | 43.1% | 4 | — | `out-of-axis` |
| `SupportedEphemeralOSDiskPlacements` | 58.7% | 5 | — | `out-of-axis` |
| `EphemeralOSDiskSupported` | 100% | 2 | — | `out-of-axis` |
| `EncryptionAtHostSupported` | 100% | 2 | — | `out-of-axis` |
| `CapacityReservationSupported` | 100% | 2 | — | `out-of-axis` |
| `SupportedCapacityReservationTypes` | 90.8% | 4 | — | `out-of-axis` |
| `MemoryPreservingMaintenanceSupported` | 100% | 2 | — | `out-of-axis` |
| `HyperVGenerations` | 100% | 2 | — | `out-of-axis` |
| `VMDeploymentTypes` | 100% | 3 | — | `out-of-axis` |
| `LowPriorityCapable` | 100% | 2 | — | `unmapped-axis` (가격 축 — aws `SupportedUsageClasses`와 같은 성격) |
| `ParentSize` | 19.7% | 12+ | — | `out-of-axis` |
| `vCPUsConstraintsAllowed` · `vCPUsAvailable` | 100% | 12+ | — | `out-of-axis` |
| `MemoryInMB` · `MemoryGB` · `NumberOfCores` · `vCPUs` | 100% | 12+ | — | `duplicate:tumblebug-cost.*` (**T2 확인 필요** — 보정) |
| `Name` · `Size` | 100% | 12+ | — | `duplicate:tumblebug-cost.specName` |
| `CpuArchitectureType` | 100% | 2 | — | `duplicate:tumblebug-cost.architecture` |
| `ResourceDiskSizeInMB` · `MaxResourceVolumeMB` | 100% | 12+ | — | `unmapped-axis` (둘이 같은 값 — 확인 필요) |
| `LocationInfo_0_Location` | 100% | 12+ | — | `duplicate:tumblebug-cost.region` |
| `LocationInfo_0_Zone_{0,1,2}` | 78/74/61% | 3 | — | `duplicate:cloud-regions.zones` |
| `OSDiskSizeInMB` | 100% | **1** | — | `constant` (1047552) |
| `OSVhdSizeMB` | 100% | **1** | — | `constant` (1047552) |
| `Tier` | 100% | **1** | — | `constant` (Standard) |
| `ResourceType` | 100% | **1** | — | `constant` (virtualMachines) |
| `TrustedLaunchDisabled` | 11.0% | **1** | — | `constant` (True만) |
| `HibernationSupported` | 6.6% | **1** | — | `constant` (True만) |
| `SupportedVirtualizationTypes` | 1.0% | **1** | — | `constant` |
| `LocationInfo_0_ZoneDetail_0_Capability_0_UltraSSDAvailable` | 59.0% | **1** | — | `constant` |

`constant` 8개는 `ibm-perf`의 `freqency`와 정확히 같은 부류입니다. **지금은 우연히 안
뽑고 있을 뿐, 세어 보고 뺀 것이 아닙니다.**

### gcp — 행 11,622 · 최상위 키 17개 · 사용 4개

| 키 | 채움 | 값종류 | 현재 | 분류 초안 |
|---|---:|---:|---|---|
| `IsSharedCpu` | 100% | 2 | 사용 | `adopt` |
| `MaximumPersistentDisks` | 100% | 6 | 사용 | `adopt` |
| `MaximumPersistentDisksSizeGb` | 100% | 3 | 사용 | `adopt` |
| `Description` | 100% | 12+ | 사용 | `adopt` |
| `Deprecated` | 1.0% | 2 | — | **`unmapped-axis`** → 세대 신호. `project.py` 주석 *"GCP는 세대를 명시하지 않는다"*가 엄밀히는 과장 |
| `BundledLocalSsds` | 12.8% | 12+ | — | `duplicate:tumblebug-perf.localSsdGB` (T2 확인 필요) |
| `Accelerators` | 2.9% | 12+ | — | `duplicate:tumblebug-perf.gpu*` (T2 확인 필요) |
| `Architecture` | 59.8% | 2 | — | `duplicate:tumblebug-cost.architecture` |
| `GuestCpus` · `MemoryMb` | 100% | 12+ | — | `duplicate:tumblebug-cost.*` (**T2** — 보정) |
| `Name` | 100% | 12+ | — | `duplicate:tumblebug-cost.specName` |
| `Zone` | 100% | 12+ | — | `duplicate:cloud-regions.zones` |
| `Id` | 100% | 12+ | — | `out-of-axis` |
| `ImageSpaceGb` | 100% | 2 | — | `out-of-axis` |
| `CreationTimestamp` | 100% | **1** | — | `constant` (1969-12-31 — 값이 무의미) |
| `Kind` | 100% | **1** | — | `constant` |
| `SelfLink` | 100% | **1** | — | `constant` (프로젝트 URL 하나) |

> **분류는 전부 초안입니다.** `duplicate`는 T2(값 대조)를 통과해야 확정되고,
> `out-of-axis`/`unmapped-axis`의 경계는 T6대로 사유를 한 줄 못 쓰면 분류가 아닙니다.

---

## 8. 이 계획으로 뒤집히는 것

미리 적어 둡니다 — 나중에 "왜 이렇게 됐나"의 답이 됩니다.

1. **`azure-compute-docs`(소스 29번)의 존재 근거가 재판정 대상이 됩니다.** `maxNics`는
   미러가 100% 답합니다. 다만 `networkBandwidthMbps`(24,108건)는 미러 `details`에
   **없으므로** 소스 자체는 남을 가능성이 높습니다. 구세대 판정도 마찬가지입니다.
   *즉 소스가 없어지는 게 아니라 **역할이 줄어듭니다.***
2. **azure `maxNics` 커버리지가 25,385 → 24,553으로 줄어듭니다** (D3의 대가).
3. **aws에 `maxNics`·`localSsdGB`가 생깁니다** — 새 소스 없이, 이미 받아 둔 미러에서.
4. `project.py`의 GCP 세대 관련 주석이 정정됩니다.
5. `kb-source-atlas-2026-07-29.md` §1·§29의 처리 설명이 낡습니다. 그 문서는 불변
   기록이므로 고치지 않고, **바뀐 사실은 커밋 메시지와 코드가 기록**합니다.

---

## 9. 아직 안 정한 것

- `unmapped-axis` 후보 중 무엇을 채택할지 (**D2에 따라 이번 라운드 밖**).
  현재 후보 수: aws 4 · azure 14 · gcp 1.
- 나머지 다섯 프로바이더(tencent·ncp·kt·nhn·openstack)의 성능 축을 열지 여부.
  P0에서 키 목록만 세고 판단은 미룹니다.
- `duplicate` 판정 중 T2 확인이 필요한 것: aws `MemoryInfo` · azure `MemoryInMB` 계열 ·
  gcp `GuestCpus`/`MemoryMb`. **보정(×1.024)이 걸린 축이라 같은 값이 아닐 수 있습니다.**
