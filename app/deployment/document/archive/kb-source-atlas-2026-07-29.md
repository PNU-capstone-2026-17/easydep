# 재료 47종의 원형 → 처리 → 산출 전수 (2026-07-29)

`kb-sourcebook-2026-07-28.md`는 **무엇을 담았는지**를 적었습니다. 이 문서는
**어떻게 그 모양이 되었는지**를 적습니다. 소스마다 세 덩어리입니다.

- **원본 형태** — 캐시된 실제 바이트에서 그대로 뽑은 발췌
- **처리** — 어느 모듈이 무엇을 하는가 (파일 경로·함수 이름·버리는 규칙)
- **처리 후 형태** — `data/*.json.gz`에서 그대로 뽑은 실제 레코드

발췌는 전부 이 저장소의 캐시(`.cache/cloudkb/`)와 커밋된 산출물(`data/`)에서
2026-07-29에 직접 읽은 것입니다. 지어낸 예시가 하나도 없습니다.

---

# 0부 — 모든 소스가 공통으로 지나는 길

## 0.1 다섯 단계

```
① 고정      kbcommon/sources.py       "무엇을 받기로 했나" (URL에 태그·커밋이 박혀 있다)
② 수집      kbcommon/fetch.py         받아서 캐시 + <파일>.provenance.json 기록
                                      "실제로 무엇을 받았나" (sha256·크기·시각·ETag)
③ 파싱      <kb>/parsers/<소스>.py    원본 → 레코드. **여기서 버릴 것을 정한다**
④ 모델      Constraint · Edge · Node · Rule · Bundle · 축별 전용 레코드
⑤ 산출      kbcommon/artifact.py      output/<이름>.json → gzip → data/<이름>.json.gz
                                      _source(프로버넌스) · _coverage(무엇을 못 담았나) 동봉
```

②의 실물입니다 — `CloudformationSchema.zip.provenance.json`:

```json
{
  "url": "https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip",
  "sha256": "83b88800e04bb5cfc85bbf7eb261623998bccfe2a25920094c4adf268eb09e47",
  "bytes": 2794161,
  "fetched_at": "2026-07-20T08:31:41+00:00",
  "last_modified": "Sat, 18 Jul 2026 06:29:09 GMT",
  "etag": "\"8e3d9bca9f31ad57776d4b4c50feb984\""
}
```

①은 "무엇을 받기로 했나", ②는 "실제로 무엇을 받았나"입니다. **AWS zip처럼 고정할 수
없는 소스에서 이 둘이 갈립니다** — 재현은 못 해도 바뀐 사실은 이 파일이 잡습니다.

## 0.2 레코드 모델 다섯 가지

산출물의 모양은 다섯 가지뿐입니다. 소스가 47종이어도 도착점은 이 다섯 곳입니다.

**Constraint** (capacitykb) — 속성 하나에 걸린 제약

```json
{"type_id": "aws::AWS::EC2::Volume", "property": "Size", "kind": "max",
 "value": 16384, "value_type": null, "unit": "GiB", "conditional": false,
 "note": null, "evidence": "aws-cross-checked", "basis": "stated",
 "backend": null, "conditions": [{"property": "VolumeType", "op": "eq", "value": "gp2"}]}
```

**Node / Edge** (graphkb) — 타입과 타입 사이의 관계

```json
{"id": "aws::AWS::ACMPCA::Certificate", "layer": "vendor", "provider": "aws",
 "kind": "resource_type", "display_name": "AWS::ACMPCA::Certificate",
 "source": "cloudformation-registry"}

{"from": "aws::AWS::ACMPCA::Certificate", "to": "aws::AWS::ACMPCA::CertificateAuthority",
 "type": "references", "via_property": "CertificateAuthorityArn", "required": true,
 "cardinality": "one", "evidence": "cdk-oob", "basis": "stated",
 "target_property": "Arn", "reviewed": true}
```

**Bundle / Cooccurrence** (bundlekb) — 함께 만들어지는 것 / 함께 나오는 것

```json
{"anchor": "azure::Microsoft.Authorization/roleAssignments",
 "typeId": "azure::Microsoft.Storage/storageAccounts",
 "hits": 50, "samples": 89, "evidence": "aqt-corpus"}
```

**Rule** (sizingkb) — 규모 상수

```json
{"id": "tumblebug::reserved-ips/alibaba", "kind": "reserved_ips", "scope": "alibaba",
 "metric": "reservedIps", "value": 4, "unit": "IPs",
 "evidence": "tumblebug-networkinfo",
 "note": "Number of reserved IPs in the subnet (…)", "caveat": null}
```

**Doc** (patternkb) — 검색되는 산문 한 편. 라이선스와 저작자 표시가 **레코드 안에**
들어갑니다 — NOTICE에만 두면 파일이 저장소를 떠날 때 사라지기 때문입니다.

그 밖에 축 전용 레코드가 있습니다 (costkb의 `specs`, perfkb의 `specs`,
envkb의 `regions`·`pairs`·`products`·`images`·`csps`).

## 0.3 `evidence` → `basis`

레코드마다 붙는 두 꼬리표 중 `basis`는 `evidence`에서 기계적으로 결정됩니다
(`kbcommon/basis.py`의 `BASIS_OF_EVIDENCE`). **등록되지 않은 라벨은 `inferred`로
떨어집니다** — 새 라벨이 조용히 사실이 되지 않게 하려는 것입니다.

| basis | 뜻 | 판정에 쓰나 | 유보 붙나 |
|---|---|---|---|
| `stated` | 원본이 그렇게 적어 놓았다 | ○ | × |
| `inferred` | 우리가 짐작했다 | 검수됐으면 ○ | ○ |
| `observed` | 코퍼스에서 세었다 | **×** | ○ |

## 0.4 빌드 명령 지도

| 명령 | 소스 |
|---|---|
| `python -m capacitykb build --source {cfn,azure,gcp,aws-limits,aws-tf,aws-regions,aws-conditional,aws-endpoints,azure-quota,azure-mutability,azure-secret,azure-operations,alicloud,tencent,ibm,ncp,openstack,nhn,oracle}` | 19개 축 |
| `python -m graphkb build --source {tumblebug,cfn,azure,gcp,mapping,avm,svcmap}` | 7개 그래프 |
| `python -m costkb build` · `build-azure-pricing` · `build-gcp-pricing` · `build-{aws,azure,gcp}-managed` | 가격 6종 |
| `python -m perfkb build` · `build-ibm` | 성능 2종 |
| `python -m bundlekb build --source {avm,tumblebug,aqt,aws-patterns,awscfn,kcc}` | 번들 6종 |
| `python -m sizingkb build` · `python -m patternkb build` · `build-aws-waf` | 규모·산문 |
| `python -m envkb build-{regions,images,latency,carbon,lifecycle,cbspider}` | 환경 6축 |

---

# A. 밑바탕 — cb-tumblebug / cb-spider 계열 (7종)

## 1. `tumblebug-dump` — 스펙·가격 미러의 원천

`https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/v0.12.25/assets/assets.dump.gz`
· 태그 `v0.12.25` · Apache-2.0 · 캐시 `tumblebug-assets-v0.12.25.dump` (34.9 MB)

### 원본 형태

**PostgreSQL 16.14 custom-format 덤프**입니다. `psql`이 못 읽고 `pg_restore` 계열
파서가 필요합니다(`pgdumplib`). 안에 테이블이 셋 있습니다.

```
COPY public.spec_infos    (id, uid, csp_spec_name, name, namespace, connection_name,
  provider_name, region_name, region_latitude, region_longitude, infra_type, architecture,
  os_type, v_cpu, memory_gi_b, disk_size_gb, max_total_storage_ti_b, net_bw_gbps,
  accelerator_model, accelerator_count, accelerator_memory_gb, accelerator_type,
  cost_per_hour, description, order_in_filtered_result, evaluation_status,
  evaluation_score01…10, root_disk_type, root_disk_size, associated_object_list,
  is_auto_generated, system_label, details)  FROM stdin;      ← 42컬럼 · 73,083행

COPY public.image_infos   (…, is_basic_image, is_gpu_image, os_architecture, …)  ← 174,759행
COPY public.latency_infos (source_region, target_region, latency_ms, measured_at, …)  ← 10,890행
```

행 하나를 그대로 펼치면 이렇습니다 (`aws+ap-northeast-2+t3.micro`):

```
id                    = 'aws+ap-northeast-2+t3.micro'
provider_name         = 'aws'          region_name  = 'ap-northeast-2'
csp_spec_name         = 't3.micro'     architecture = 'x86_64'
v_cpu                 = '2'            memory_gi_b  = '1'
cost_per_hour         = '0.013000000268220901'
infra_type            = 'node'         accelerator_count = '0'
details               = '[{"key":"AutoRecoverySupported","value":"true"},
                          {"key":"BareMetal","value":"false"},
                          {"key":"BurstablePerformanceSupported","value":"true"},
                          {"key":"CurrentGeneration","value":"true"},
                          {"key":"EbsInfo","value":"{EbsOptimizedInfo:{BaselineBandwidthInMbps:87,
                             BaselineIops:500,BaselineThroughputInMBps:10.875,
                             MaximumBandwidthInMbps:2085,MaximumIops:11800,
                             MaximumThroughputInMBps:260.625},EbsOptimizedSupport:default,…}"},
                          {"key":"MemoryInfo","value":"{SizeInMiB:1024}"},
                          {"key":"NetworkInfo","value":"{…,MaximumNetworkInterfaces:2,
                             NetworkCards:[{MaximumNetworkInterfaces:2,NetworkCardIndex:0,
                             NetworkPerformance:Up to 5 Gigabit}],
                             NetworkPerformance:Up to 5 Gigabit}"}, …]'
```

**`details`의 함정**: 바깥은 JSON 배열인데 `value` **안쪽은 JSON이 아닙니다.** Go의
`%v` 포맷이라 따옴표가 없고, 값에 공백이 들어가고(`Up to 5 Gigabit`), 중첩·배열이
섞입니다. 표준 파서로 못 읽고 범용 파서를 쓰면 공백에서 깨집니다.

### 처리

`kbcommon/tumblebug_dump.py`가 행을 dict로 흘려보내고, **두 KB가 같은 행의 다른
컬럼**을 봅니다.

```
iter_table_rows(dump, "spec_infos")
  ├─ costkb/parsers/tumblebug.py   → 가격·크기 컬럼   → data/tumblebug-cost.json.gz
  └─ perfkb/parsers/{project,details}.py → details 컬럼 → data/tumblebug-perf.json.gz
iter_table_rows(dump, "image_infos")   → envkb/images.py   → data/basic-images.json.gz
```

1. **컬럼 이름은 `COPY` 문에서 정규식으로 뽑습니다** — `pgdumplib`의 Entry가 컬럼
   목록을 노출하지 않고 `copy_stmt`만 주기 때문입니다. 못 읽으면 조용히 빈 결과를
   내지 않고 크게 실패합니다(덤프 형식이 바뀌었다는 뜻이므로).
2. **상류 버그를 되돌립니다.** CB-Spider의 `ConvertMBToMiBInt64`가 `mb * 1000 / 1024`를
   이미 MiB인 값에 한 번만 적용해서, 순효과가 `참값 / 1.024`입니다. 73,083행 전수로
   영향 범위를 셌더니 **gcp 77.6% · azure 64.2%** 이고 나머지 8개 프로바이더는
   0.0%였습니다. 그래서 **gcp·azure만** ×1.024 합니다.
3. 보정값을 `memGiB` **한 칸에** 넣습니다. 예전엔 미러값과 보정값을 두 칸에 나눠
   뒀는데, 표시는 보정값·필터는 버그값을 쓰다가 "16 GiB 이상"에서 실제로는 만족하는
   3,765건이 조용히 빠졌습니다. 보정 사실은 값이 아니라 **데이터셋 메타데이터**
   (`_corrections`)에 적습니다.
4. `details`는 **통째로 파싱하지 않습니다.** 필요한 키만 정규식으로 뽑고 못 뽑으면
   `None`(fail-open)입니다. 뽑는 키를 소수로 유지하는 것 자체가 안전장치입니다.
   키마다 따로 정규식을 돌리므로 **필드 순서에 의존하지 않습니다** — 실측상 Go의
   `%v`가 구조체를 선언 순서로 찍어서 azure 34,846건이 0% 정렬 상태입니다.

### 처리 후 형태

`data/tumblebug-cost.json.gz` — `specs` 73,083건

```json
{"id": "aws+ap-northeast-2+t3.micro", "provider": "aws", "region": "ap-northeast-2",
 "specName": "t3.micro", "vCPU": 2, "memGiB": 1.0,
 "hourlyUSD": 0.013000000268220901, "architecture": "x86_64",
 "infraType": "node", "acceleratorCount": 0, "acceleratorMemoryGB": 0.0}
```

```json
"_corrections": [{"field": "memGiB", "providers": ["azure", "gcp"], "operation": "×1.024",
  "reason": "Upstream CB-Spider's ConvertMBToMiBInt64 applies the MB→MiB ratio only once,
             and to a value that is already MiB, so the value is recorded 2.4% below reality."}]
```

`data/tumblebug-perf.json.gz` — `specs` 65,032건 (같은 행의 `details` 컬럼)

```json
{"id": "aws+ap-northeast-2+t3.micro", "provider": "aws", "specName": "t3.micro",
 "sustainedCpu": {"value": false,
   "note": "Burstable instance — performance drops to baseline once the CPU credits run out.",
   "evidence": "aws-burstable-field", "basis": "stated"},
 "currentGeneration": true, "clockGHz": 2.5, "threadsPerCore": 2.0,
 "networkPerformance": "Up to 5 Gigabit", "networkIsBurst": true,
 "ebsBaselineMbps": 87.0, "ebsMaxMbps": 2085.0,
 "ebsBaselineIops": 500.0, "ebsMaxIops": 11800.0, "bareMetal": false,
 "cpuVendor": "GenuineIntel", "cpuModel": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
 "cpuClockMHz": 2500, "cpuCacheKB": 36608, "cpuCores": 1, "cpuThreads": 2,
 "memorySpeedMHz": 2933, "hardwareCheckedAt": "2025-12-10",
 "hardwareEvidence": "ec2-hardware-probe"}
```

`BurstablePerformanceSupported: "true"` 한 칸이 `sustainedCpu.value = false`가 되고,
**왜 그렇게 판단했는지**가 `note`·`evidence`·`basis`로 같이 실립니다. `false`인
인스턴스에는 다른 evidence(`aws-non-burstable-inferred`, basis=`inferred`)가 붙습니다 —
`false`는 "버스트가 아니다"가 아니라 **"그 칸에서 빠졌다"**이기 때문입니다.

`data/basic-images.json.gz` — `image_infos` 174,759행 중 **6,033건(3.3%)**

```json
{"provider": "ncp", "regions": ["jpn"], "imageId": "104027588",
 "osType": "Ubuntu 24.04", "osArchitecture": "x86_64",
 "osDistribution": "ubuntu-24.04-base (Hypervisor:KVM)", "kinds": ["is_basic_image"]}
```

**안 담는 것**: `is_gpu_image`는 45.6%(79,617건)에 켜져 있고 그중 79,478건이 AWS
하나입니다. 45%가 켜진 플래그는 큐레이션 신호가 아니라 "GPU 인스턴스에서 돌아갈 수
있음"에 가까우므로 쓰지 않습니다.

---

## 2. `tumblebug-src` — 이름 붙은 부품 세트

`https://codeload.github.com/cloud-barista/cb-tumblebug/tar.gz/refs/tags/v0.12.25`
· 태그 `v0.12.25` · Apache-2.0 · 캐시 36.3 MB

### 원본 형태

tarball 안에서 **네 자리**만 봅니다.

`init/templates/*.json` — 큐레이션된 인프라 템플릿 22개

```json
{
  "resourceType": "infra",
  "name": "infra-across-global",
  "description": "Global Infra test with VMs across multiple CSP regions worldwide",
  "infraDynamicReq": {
    "nodeGroups": [
      {"name": "g1-alibaba-ap-northeast-1", "nodeGroupSize": 2,
       "specId": "alibaba+ap-northeast-1+ecs.e-c4m1.large",
       "imageId": "ubuntu_22_04_x64_20G_alibase_20260615.vhd",
       "rootDiskType": "default", "rootDiskSize": 50},
      {"name": "g3-aws-ap-northeast-3", "nodeGroupSize": 2,
       "specId": "aws+ap-northeast-3+t3.small", "imageId": "ami-070dbbf034ee28f87", …},
      …17개 node group
    ]}}
```

`assets/networkinfo.yaml` — CSP별 서브넷 예약 IP·프리픽스 범위 (손 큐레이션 YAML)

```yaml
# network: Top level key to describe network characteristics or requirements
#   <csp>: Name of the CSP
#     vnet: Virtual network characteristics
#       prefix-length: CIDR prefix length for the virtual network
#         min: Minimum prefix length (e.g., 8)
#         max: Maximum prefix length (e.g., 28)
```

`assets/k8sclusterinfo.yaml` — 클러스터가 요구하는 서브넷 수 등
`src/core/infra/provisioning.go` — 23만 자짜리 Go 소스

### 처리

**두 KB가 같은 tarball의 다른 축을 봅니다** — bundlekb는 *구성*, sizingkb는 *규모*.

1. **템플릿은 기계로 읽습니다.** `init/templates/*.json`은 구조화돼 있어 짐작이
   필요 없습니다. 다만 **원본이 스스로 단 경고를 값과 함께** 담습니다 —
   `sg-default`는 "전 포트를 연다, 프로덕션엔 쓰지 말라"고 자기가 적어 두었습니다.
2. **동적 번들은 파싱하지 않고 사람이 읽었습니다.** `provisioning.go`의
   `getNodeGroupReqFromDynamicReq`(3216~3529행)를 눈으로 읽어 확정한 표를 상수
   (`_DYNAMIC_MEMBERS`)로 둡니다. 정규식으로 긁으면 조건 분기를 놓칩니다. 소스에 핀이
   박혀 있으므로 그 확인은 다음 빌드에서도 유효하고, 언제 읽었는지를 `_READ_AT_PIN`에
   적어 둡니다. **"완벽한 데이터셋이 목표지 완벽한 파서가 아니다."**
3. **비어 있는 칸으로 규칙을 만들지 않습니다.** `networkinfo.yaml`은 CSP 10곳 중
   3곳(alibaba·azure·ibm)만 예약 IP를 적어 뒀습니다. AWS 칸이 비어 있는데 AWS는 실제로
   5개를 예약합니다 — 빈칸을 0으로 읽으면 251대 자리에 256대라고 답하게 됩니다.

### 처리 후 형태

`data/tumblebug-bundles.json.gz` — `bundles` 23건 (동적 1 + 템플릿 22)

```json
{"id": "tumblebug::dynamic-vm", "name": "tumblebug dynamic VM creation",
 "provider": "core", "evidence": "tumblebug-dynamic", "anchor": "core::vm",
 "description": "Resources acquired along with a single VM when you request one
   dynamically from cb-tumblebug. Confirmed by reading provisioning.go as of v0.12.25.",
 "caveat": "**This is what this tool creates, not what the cloud requires.**
   The four resources (vNet·subnet·sshKey·securityGroup) are shared per connection,
   so existing ones are reused.",
 "members": [{"typeId": "core::securityGroup", "tier": "always",
              "note": "shared per connection. A template can change the policy"}, …]}
```

```json
{"id": "tumblebug::infra-across-global", "name": "infra-across-global",
 "provider": "core", "evidence": "tumblebug-template", "anchor": "core::vm",
 "description": "Global Infra test with VMs across multiple CSP regions worldwide",
 "members": [{"typeId": "core::vm", "tier": "always", "note": "17 node groups", "count": 36}]}
```

`data/tumblebug-sizing.json.gz` — `rules` 31건
(`reserved_ips` 3 · `required_count` 8 · `reference_point` 18 · `minimum` 2)

```json
{"id": "tumblebug::reserved-ips/alibaba", "kind": "reserved_ips", "scope": "alibaba",
 "metric": "reservedIps", "value": 4, "unit": "IPs", "evidence": "tumblebug-networkinfo",
 "note": "Number of reserved IPs in the subnet (i.e., the 1st IP address and last 3 …)"}

{"id": "tumblebug::k8s-node-min/memoryGiB", "kind": "minimum", "scope": "k8s-node",
 "metric": "memoryGiB", "value": 4.0, "unit": "GiB", "evidence": "tumblebug-dynamic",
 "note": "fixed by reading recommendation.go at v0.12.25",
 "caveat": "**A minimum this tool enforces**, not a value set by Kubernetes or the cloud."}
```

**비어 있는 7곳(aws·gcp·kt·ncp·nhn·openstack·tencent)에는 레코드를 만들지 않습니다.**
그 공백은 사람이 손으로 채웠고, 그것이 47번 앞에 나오는 `reviewed-sizing`입니다.

---

## 3. `tumblebug-latency` — 지역 간 지연 10,890쌍

`…/cb-tumblebug/v0.12.25/assets/cloudlatencymap.csv` · 태그 `v0.12.25` · Apache-2.0

### 원본 형태

**300×300 대칭 행렬 CSV**입니다. 첫 행·첫 열이 `<provider>-<region>` 라벨입니다.

```csv
,alibaba-ap-south-1,alibaba-ap-southeast-2,alibaba-ap-southeast-3,alibaba-ap-southeast-5,…
alibaba-ap-south-1,0.223,148.493,65.779,69.837,131.215,264.157,281.086,258.683,…
alibaba-ap-southeast-2,148.494,0.188,92.843,222.204,188.343,201.219,256.117,…
alibaba-ap-southeast-3,65.788,92.848,0.212,20.139,167.205,311.574,124.860,…
```

**측정 시각이 파일에 없습니다.** (덤프 `latency_infos`의 `measured_at`은 적재 시각이라
10,890행이 같은 초에 찍혀 있습니다.)

### 처리

`envkb/latency.py`

1. 행렬을 (source, target) 쌍으로 펴고 라벨을 `provider` + `region`으로 가릅니다.
2. `cloudinfo.yaml`의 위경도로 **대권거리**를 계산해 붙입니다.
3. **네 가지를 검증하고 결과를 산출물에 적습니다.**
   - 대칭성 — 양방향 4,851쌍 중 값이 다른 것 4,845(99.9%) → 전치 복사가 아니다
   - 전치 채움 — `benchmark.go`에 `Fill empty with transpose`가 있으나 전부 대칭인
     행은 98개 중 **0개** → 이 스냅샷에서는 거의 안 걸렸다
   - 거리 상관 — r = 0.817, 거리대별 중앙값 단조 증가
   - 물리 하한 — 왕복 광속(광섬유 200,000 km/s)을 어기는 쌍 16/10,791 (0.15%)
4. **하한 위반 16쌍을 지우지 않고 `suspect`로 표시합니다.** 좌표가 틀렸는지 값이
   틀렸는지 모르므로, 조용히 버리면 다음 사람이 같은 것을 다시 발견합니다.

### 처리 후 형태

`data/region-latency.json.gz` — `pairs` 10,890건

```json
{"sourceProvider": "alibaba", "sourceRegion": "ap-south-1",
 "targetProvider": "alibaba", "targetRegion": "ap-south-1",
 "latencyMs": 0.223, "distanceKm": 0.0}
```

`_note`가 **무엇을 잰 값인지**를 답변까지 끌고 갑니다: *"A value cb-tumblebug measured
by actually launching VMs, not a vendor-guaranteed SLA — an observation at some point in
time. The source carries no measurement time."*

---

## 4. `tumblebug-cloudinfo` — 리전 정의 188개

`…/cb-tumblebug/v0.11.8/assets/cloudinfo.yaml` · 태그 `v0.11.8` · Apache-2.0

**이미 핀 박은 저장소의 안 쓰던 파일**입니다. 새 소스를 찾기 전에 이미 받아 둔
소스의 안 쓰는 부분을 먼저 본 사례입니다.

### 원본 형태

```yaml
      asia-northeast2:
        description: Osaka Japan
        location:
          display: Osaka Japan
          latitude: 34.6937
          longitude: 135.5022
        zone:
        - asia-northeast2-a
        - asia-northeast2-b
        - asia-northeast2-c
      asia-northeast3:
        description: Seoul South Korea
        location:
          display: South Korea (Seoul)
          latitude: 37.2
          longitude: 127.0
        zone:
        - asia-northeast3-a
        - asia-northeast3-b
        - asia-northeast3-c
```

### 처리

`envkb/cloudinfo.py` → `envkb/regions.py`

1. 프로바이더 10곳 · 리전 188개를 그대로 옮깁니다(표시이름 188 · 위경도 188 ·
   가용영역 175 — 실측 채움률).
2. **조인 키만 소문자로 맞춥니다.** `kt`·`ncp`·`nhn`은 이 파일이 `KR1`·`KR`로 적는데
   미러는 `kr1`·`kr`로 적습니다. 그대로 조인하면 이 셋이 **0%**가 되고, 소문자로
   맞추면 100%입니다. **`code`는 원본 표기를 남깁니다** — 원본을 고쳐 쓰면 그건 우리
   값이 됩니다.
3. **한국어 별칭은 리전 코드가 아니라 영어 낱말에 붙입니다.**
   `"서울" → ("Seoul", "Korea") → (원본 이름에서 찾기) → 프로바이더별 코드`.
   `"서울": "ap-northeast-2"`로 적으면 리전 코드가 우리 표에 박혀서, 프로바이더가
   이름을 바꾸면 표가 조용히 거짓이 됩니다.
4. **방위 이름은 도시로 매핑하지 않습니다.** `Southeast Asia`(실제로는 싱가포르)를
   채우려면 그건 원본이 말한 것이 아니라 우리 지식입니다 — `서울`→`Seoul`은 같은 것을
   다른 말로 적은 것이지만 `Southeast Asia`→`싱가포르`는 **새 사실을 주장**하는 것입니다.

### 처리 후 형태

`data/cloud-regions.json.gz` — `providers` 10곳

```json
"alibaba": {"description": "Alibaba Cloud", "regions": {
   "ap-northeast-2": {"code": "ap-northeast-2", "name": "South Korea (Seoul)",
     "latitude": 37.36, "longitude": 126.78,
     "zones": ["ap-northeast-2a", "ap-northeast-2b"]}, …}}
```

'서울'이 프로바이더마다 다르다는 것이 이 파일의 값어치입니다:

```
alibaba/aws  ap-northeast-2      gcp      asia-northeast3
azure        koreacentral·south  tencent  ap-seoul
kt           KR1                 ncp      KR          nhn  KR1·KR2
```

---

## 5. `tumblebug-swagger` — core 층의 어휘

`…/cb-tumblebug/v0.11.8/src/interface/rest/docs/swagger.json` · 태그 `v0.11.8` · Apache-2.0

### 원본 형태

Swagger 2.0 `definitions` 262개. **생성 요청 스키마(`model.Tb*Req`)만** 씁니다 —
`required` 배열이 생성 시점 제약을 표현하고, 응답 스키마는 서버 생성 필드 노이즈가
많기 때문입니다.

```json
"model.TbVNetReq": {"type": "object",
  "required": ["connectionName", "name"],
  "properties": {
    "cidrBlock": {"type": "string", "example": "10.0.0.0/16"},
    "connectionName": {"type": "string", "example": "aws-ap-northeast-2"},
    "name": {"type": "string", "example": "vnet00"},
    "subnetInfoList": {"type": "array", "items": {"$ref": "#/definitions/model.TbSubnetReq"}}}}

"model.TbSubnetReq": {"type": "object",
  "required": ["ipv4_CIDR", "name"],
  "properties": {"ipv4_CIDR": {"type": "string", "example": "10.0.1.0/24"}, …}}
```

### 처리

`graphkb/parsers/tumblebug.py` — `$ref`로 딸린 배열 프로퍼티를 **담김 관계**로 읽고,
`required` 여부를 엣지에 싣습니다. v0.11.8을 고정하는 이유는 main 브랜치가
MCI→Infra 개명 + `Tb` 접두사 제거로 스키마 이름이 다르기 때문입니다.

### 처리 후 형태

`data/core-graph.json.gz` — `nodes` 13 · `edges` 19

```json
{"id": "core::vNet", "layer": "core", "provider": "common",
 "kind": "resource_type", "display_name": "vNet", "source": "cb-tumblebug-swagger"}

{"from": "core::subnet", "to": "core::vNet", "type": "contained_in",
 "via_property": "subnetInfoList", "required": true, "cardinality": "one",
 "evidence": "swagger-field", "basis": "stated", "reviewed": true}
```

이 13개가 **벤더 중립 어휘**이고, 7번(`cb-spider-map`)이 여기에 벤더 타입을 잇습니다.

---

## 6. `cb-spider` — 우리 실행 경로의 바닥

`https://codeload.github.com/cloud-barista/cb-spider/tar.gz/refs/tags/v0.12.37`
· 태그 `v0.12.37` · Apache-2.0 · 캐시 2.9 MB

### 원본 형태

드라이버 12곳의 Go 핸들러입니다. 경로 자체가 데이터입니다 —
`cloud-control-manager/cloud-driver/drivers/<csp>/resources/<X>Handler.go`.

```go
// cb-spider-0.12.37/cloud-control-manager/cloud-driver/drivers/aws/resources/VMHandler.go
package resources
…
type AwsVMHandler struct {
	Region     idrv.RegionInfo
	…
}
func (vmHandler *AwsVMHandler) StartVM(vmReqInfo irs.VMReqInfo) (irs.VMInfo, error) { … }
```

### 처리

`envkb/cbspider.py`

1. 경로 정규식 `/cloud-driver/drivers/([a-z0-9]+)/resources/(\w+Handler)\.go$`로
   (CSP, 핸들러) 격자를 만듭니다.
2. **"파일이 있다"와 "구현됐다"는 다릅니다.** cb-spider는 미지원 기능을 "not supported"
   에러를 던지는 스텁으로 두기도 합니다. 그래서 **핸들러의 주 생성 메서드가 실제로
   있는지**까지 봅니다.
3. **메서드 이름이 핸들러마다 다른 것이 함정입니다.** 인터페이스를 직접 읽고
   확인했습니다 — `VMHandler → StartVM`(CreateVM이 아님),
   `MyImageHandler → SnapshotVM`(CreateImage가 아님). `Create`로만 찾으면 이 둘이
   전부 미구현으로 잡힙니다(처음에 그 상태를 만들었습니다).

### 처리 후 형태

`data/cbspider-support.json.gz` — `csps` 12곳

```json
{"csp": "alibaba", "resources": [
  {"core": "vNet", "supported": true, "handler": "VPCHandler", "method": "CreateVPC", "reason": null},
  {"core": "vm", "supported": true, "handler": "VMHandler", "method": "StartVM", "reason": null},
  {"core": "nlb", "supported": true, "handler": "NLBHandler", "method": "CreateNLB", "reason": null},
  {"core": "k8sCluster", "supported": true, …}, …]}
```

매트릭스가 곧 지식입니다: VPC·Security·KeyPair·VM·Disk·MyImage **12/12** ·
NLB **11/12**(oracle 없음) · Cluster(k8s) **8/12**(kt·ktclassic·openstack·oracle 없음).

`_note`가 경계를 못 박습니다: *"This is the tool's coverage, not a fact about the cloud
— unsupported here does not mean 'the CSP lacks that feature'."*

---

## 7. `cb-spider-map` — 사람이 손으로 만든 대응표

`graphkb/parsers/core_vendor_map.json` · **동봉**(git이 곧 버전) · 우리 파일

### 원본 형태

네트워크 소스가 아닙니다. cb-spider 드라이버를 사람이 읽고 검수해 만든 번들입니다.

```json
{
  "description": "코어 타입 ↔ 벤더 네이티브 타입 동치 매핑 (사람 검수 완료).
    1차 근거: cb-spider 드라이버 소스 (…/drivers/{aws,azure,gcp}/resources/).
    등가물이 없는 조합(sshKey/gcp, customImage/aws·gcp …)은 항목을 만들지 않는다.
    … ibm·ncp·openstack·oracle은 근거가 한 단계 얕다 — cb-spider 핸들러 존재와
    TF 타입 실재까지 확인했고 드라이버의 구체 API 호출은 읽지 않았다(confidence 0.9).",
  "mappings": [
    {"core": "vNet", "provider": "aws", "target": "AWS::EC2::VPC", "confidence": 0.95,
     "note": "aws VPCHandler.go: ec2.CreateVpc (IGW/RouteTable 번들 생성 포함)",
     "status": "confirmed"},
    {"core": "vNet", "provider": "azure", "target": "Microsoft.Network/virtualNetworks",
     "confidence": 0.95,
     "note": "azure VPCHandler.go: armnetwork.VirtualNetworksClient.BeginCreateOrUpdate",
     "status": "confirmed"}, …]}
```

### 처리

`graphkb/parsers/mapping.py` — `status: "confirmed"`인 것만 그래프에 넣습니다.
`suggest()`는 이름 유사도로 새 후보(`status: "candidate"`)를 만들어 **사람 검수용
파일**을 뽑습니다. 검수 후 `confirmed`로 바꿔 `--mapping-file`로 넘기면 반영되는
반자동 파이프라인입니다. **손으로 고치는 파일이라 오히려 해시 추적이 필요합니다.**

### 처리 후 형태

`data/mapping-graph.json.gz` — `nodes` 92 · `edges` 82

```json
{"from": "core::vNet", "to": "aws::AWS::EC2::VPC", "type": "equivalent_to",
 "via_property": "", "required": false, "cardinality": "one",
 "evidence": "cb-spider-driver", "basis": "inferred", "reviewed": true}
```

`basis`가 `inferred`인 것이 핵심입니다 — 드라이버 코드를 **사람이 읽고 만든** 매핑이고,
검수됐으니 판정에는 쓰되(`is_fact`) 단언하지는 않습니다(`needs_hedge`).

---

# B. 회사가 기계에 주라고 만든 설명서 (8종)

## 8. `cfn-schema` — AWS 리소스 스키마 ★최대 기여 소스

`https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip`
· **지문(고정 불가)** · 라이선스 미확인 · 캐시 2.79 MB · zip 안에 **1,635개** JSON

### 원본 형태

`aws-ec2-volume.json` (발췌):

```json
{
  "typeName": "AWS::EC2::Volume",
  "description": "Specifies an Amazon Elastic Block Store (Amazon EBS) volume. …",
  "properties": {
    "Size": {"type": "integer",
      "description": "The size of the volume, in GiBs.\n  +  Required for new empty volumes.
        … Supported volume sizes:\n  +  gp2: 1 - 16,384 GiB\n  +  gp3: 1 - 65,536 GiB …"},
    "Iops": {"type": "integer",
      "description": "… Valid ranges:\n  +  gp3: 3,000(default) - 80,000 IOPS
        \n  +  io1: 100 - 64,000 IOPS\n  +  io2: 100 - 256,000 IOPS …"},
    "VolumeType": {"type": "string",
      "description": "The volume type. … General Purpose SSD: gp2 | gp3 …"}
  },
  "readOnlyProperties": ["/properties/VolumeId"],
  "createOnlyProperties": null
}
```

**중요한 숫자가 `description` 산문 안에만 있습니다.** `Size`는 `"type": "integer"`일 뿐
`minimum`/`maximum`이 없고, 실제 한도(gp2 1–16,384)는 문장 속에 있습니다.

### 처리

`capacitykb/parsers/cfn.py`(제약) + `graphkb/parsers/cfn.py`(관계)가 **같은 zip**을
읽습니다.

**제약 세 종류**

1. **값 제약** — `minimum`/`maximum`/`minLength`/`pattern`/`enum`/`default`
   → evidence `cfn-schema` (stated)
2. **변경 제약** — `required` / `createOnlyProperties` /
   `conditionalCreateOnlyProperties` / `readOnlyProperties` → 같은 evidence.
   실측상 이쪽이 훨씬 풍부합니다(스키마 1,628개 중 **86.5%**가
   `createOnlyProperties`를 명시).
3. **산문 제약** — `prose.py`가 설명문에서 숫자를 뽑습니다 → evidence
   `cfn-description` (**inferred**). 산문 오탐 방어가 셋입니다.
   - **R1** 같은 (타입, 프로퍼티, 종류)에 스키마 값이 이미 있으면 산문은 만들지 않는다
   - **R2** 산문 값이 스키마 값과 모순이면(산문 max < 스키마 min 등) 버리고 경고한다
   - **게이트** 산문 범위는 `type`이 integer/number인 프로퍼티에만 적용한다 —
     `Lambda.EphemeralStorage`처럼 산문이 `$ref` 래퍼에 붙고 실제 제약이 한 단계 아래에
     있는 함정을 이 규칙이 막습니다

**관계**는 세 근거를 병합하되 **같은 엣지는 사실인 쪽을 유지**합니다:
`relationshipRef`(87건) → `cdk-oob`(1,191건, 9번) → `heuristic`(1,113건).
`readOnly` 속성은 생성 출력이라 생성 순서와 무관하므로 **모든 소스에서 제외**합니다.

> **담김(`contained_in`) 관계는 여기서 안 나옵니다 — 지어내지 않기로 한 결과입니다.**
> CloudFormation 스키마에는 담김을 말하는 어휘가 아예 없습니다(전수 확인). "필수 참조를
> 담김으로 치면 되지 않나" 싶지만 실측상 aws 엣지 2,391건 중 744건이 필수인데,
> `Certificate → CertificateAuthority`처럼 담김이 맞는 것과 `Instance → Subnet`처럼
> 애매한 것이 섞여 있습니다. 비워 두고 그 사실을 코드에 적습니다.

### 처리 후 형태

`data/aws-capacity.json.gz` — `constraints` 47,070건
(`cfn-schema` **46,911** + `cfn-description` **159**)

```json
{"type_id": "aws::AWS::ACMPCA::Certificate", "property": "ApiPassthrough",
 "kind": "mutability", "value": "create_only", "conditional": false,
 "evidence": "cfn-schema", "basis": "stated", "conditions": []}
```

```json
{"type_id": "aws::AWS::ApiGatewayV2::Integration", "property": "TimeoutInMillis",
 "kind": "min", "value": 50, "value_type": "integer", "unit": "milliseconds",
 "conditional": true,
 "note": "Custom timeout between 50 and 29000 milliseconds for WebSocket APIs and
          between 50 and 30000 milliseconds for HTTP APIs. …",
 "evidence": "cfn-description", "basis": "inferred"}
```

산문에서 뽑은 것은 **원문을 `note`에 그대로 달고** `basis`가 `inferred`로 내려갑니다.

`data/aws-graph.json.gz` — `nodes` 1,638 · `edges` 2,391

```json
{"from": "aws::AWS::ApiGateway::RestApi", "to": "aws::AWS::EC2::VPCEndpoint",
 "type": "references", "via_property": "EndpointConfiguration/VpcEndpointIds",
 "required": false, "cardinality": "many", "evidence": "relationshipRef",
 "basis": "stated", "target_property": "Id", "reviewed": true}
```

### 이 소스의 가장 큰 약점

**AWS가 같은 URL에 zip 하나를 계속 덮어씁니다.** 버전도 태그도 아카이브도 없습니다.
실측된 증거 — 재빌드했을 때 고정된 소스는 결과가 완전히 같았고 **AWS만**
46,810 → 47,109로 변했습니다. 이 창고 최대의 근거(46,911건)가 재현 불가능합니다.
남길 수 있는 것은 프로버넌스의 sha256뿐이고, 실제로 캐시(2,783,390 B)와 라이브
(2,794,161 B)가 이미 달라진 것을 그 기록이 잡았습니다.

---

## 9. `cdk-oob` — AWS 부품 간 관계표

`https://media.githubusercontent.com/media/cdklabs/awscdk-service-spec/@aws-cdk/aws-service-spec@v0.1.196/sources/OobRelationships/relationships.json`
· 태그 `@aws-cdk/aws-service-spec@v0.1.196` · 라이선스 미확인

> Git LFS라 `media` 호스트를 써야 합니다 — `raw`는 포인터 텍스트를 줍니다.

### 원본 형태

타입 353개짜리 dict입니다. **AWS CDK 팀이 손으로 정리해 배포하는 표**입니다.

```json
"AWS::ACMPCA::Certificate": {
  "relationships": {
    "CertificateAuthorityArn": [
      {"cloudformationType": "AWS::ACMPCA::CertificateAuthority",
       "propertyPath": "/properties/Arn"}],
    "CertificateSigningRequest": [
      {"cloudformationType": "AWS::ACMPCA::CertificateAuthority",
       "propertyPath": "/properties/CertificateSigningRequest"}],
    "TemplateArn": [
      {"cloudformationType": "AWS::ACMPCA::CertificateAuthority",
       "propertyPath": "/properties/Arn"}]}}
```

### 처리

`graphkb/parsers/cfn.py`가 8번과 함께 읽습니다.

1. `(속성, 대상 타입, 대상 속성)` 삼중항을 그대로 엣지로 옮깁니다 — 짐작이 없으므로
   `basis=stated`입니다.
2. **`readOnly` 속성 기준의 역방향 항목이 섞여 있어 걸러냅니다.** 이 필터가 없으면
   **방향이 뒤집힌 엣지**가 생깁니다.
3. CFN 스키마에 없는 타입 3개는 노드로 추가합니다(`aws-graph.nodes`의 `cdk-oob` 3건).

### 처리 후 형태

```json
{"from": "aws::AWS::ACMPCA::Certificate", "to": "aws::AWS::ACMPCA::CertificateAuthority",
 "type": "references", "via_property": "CertificateAuthorityArn", "required": true,
 "cardinality": "one", "evidence": "cdk-oob", "basis": "stated",
 "target_property": "Arn", "reviewed": true}
```

**이 소스 덕분에 AWS의 관계 답변은 짐작 비율이 낮습니다** —
1,191/2,391 = 49.8%가 `cdk-oob`(stated)이고 `heuristic`(짐작)은 46.5%입니다.
Azure는 짐작이 아니라 **이름 계층**이 2,223/2,514(88.4%)라 성격이 다릅니다.

---

## 10. `botocore` — AWS SDK 정의

`…/boto/botocore/1.43.52/botocore/data/ec2/2016-11-15/service-2.json`
· 태그 `1.43.52` · Apache-2.0 · shape **4,069개**

### 원본 형태

```json
"CreateVolumeRequest": {"members": {
  "Size": {"shape": "Integer",
    "documentation": "<p>The size of the volume, in GiBs. …</p>
      <p>Valid sizes:</p><ul>
      <li><p>gp2: <code>1 - 16,384</code> GiB</p></li>
      <li><p>gp3: <code>1 - 65,536</code> GiB</p></li>
      <li><p>io1: <code>4 - 16,384</code> GiB</p></li>
      <li><p>io2: <code>4 - 65,536</code> GiB</p></li>
      <li><p>st1 and sc1: <code>125 - 16,384</code> GiB</p></li>
      <li><p>standard: <code>1 - 1024</code> GiB</p></li></ul>"}}}

"Integer": {"type": "integer"}          ← 제약이 없는 공용 shape
```

> **정정 기록**: 예전 주석은 "shape에 min/max가 없다"였는데 **틀렸습니다.** EC2만 봐도
> shape 4,069개 중 min 183 · max 175 · enum 457개가 있습니다. 참인 것은 EBS의
> `Size`·`Iops`·`Throughput`이 **제약 없는 공용 `Integer` shape을 가리킨다**는 것뿐인데
> 그 관찰을 전체로 일반화했습니다 — 우리가 막으려는 '확신에 찬 오답'을 우리 주석이
> 저지른 사례로 남겨 뒀습니다. **EBS 한도에 한해서** 설명문이 유일한 출처입니다.

### 처리 — 25번과 교차 검증해서만 담습니다

`capacitykb/parsers/aws_limits.py`가 **두 소스를 대조**합니다.

```
Price List 벌크 API   maxVolumeSize / maxIopsvolume / maxThroughputvolume
                      구조화돼 있지만 **최댓값만** 있고 값이 반쯤 산문("16 TiB")
botocore 설명문        규칙적인 목록이라 **최솟값까지** 있다(gp2: 1 - 16,384 GiB)
                      shape 자체에는 제약이 없어 산문이 유일한 출처
```

1. 두 소스에서 (볼륨 종류, 종류별 한도)를 각각 뽑습니다.
2. **10쌍 전부 일치한 20건만 담습니다.** 어긋나면 담지 않고 미결로 보고합니다.
3. `gp2`의 IOPS는 한쪽에만 있어서 **안 담았습니다** — 한쪽만 보고 담았으면 **없는
   제약을 만들 뻔했습니다.**
4. **여기가 `conditions`가 처음 쓰이는 곳입니다.** 볼륨 크기 한도는 종류마다 달라서
   min/max 한 쌍으로는 못 담습니다 — 뭉개면 `standard` 볼륨 5,000 GiB처럼 **불가능한
   값이 통과하는** 봉투가 됩니다.

### 처리 후 형태

`data/aws-limits.json.gz` — `constraints` **20건** (evidence `aws-cross-checked`)

```json
{"type_id": "aws::AWS::EC2::Volume", "property": "Size", "kind": "max",
 "value": 16384, "unit": "GiB", "conditional": false,
 "evidence": "aws-cross-checked", "basis": "stated",
 "conditions": [{"property": "VolumeType", "op": "eq", "value": "gp2"}]}
```

`_coverage`: *"a value is included only when both official sources (Price List · botocore)
state the same thing — where they disagree it is dropped and reported."*

이 20건이 "30,000 GiB 넣어도 되나?" 3상태 답변을 만드는 데이터입니다.

---

## 11. `botocore-endpoints` — 리전 표시 이름 + 서비스 소재

`…/boto/botocore/1.43.52/botocore/data/endpoints.json` · 태그 `1.43.52` · Apache-2.0

**새 소스가 아니라 이미 고정해 둔 태그 안의 안 쓰던 파일입니다.**

### 원본 형태

파티션 8개. `aws` 파티션에 리전 34개 · 서비스 307개.

```json
"regions": {"ap-northeast-2": {"description": "Asia Pacific (Seoul)"}}

"services": {
  "ec2": {"endpoints": {"af-south-1": {…}, "ap-east-1": {…}, "ap-northeast-2": {…}, …}},
  "cloudfront": {"endpoints": {"aws-global": {"credentialScope": {"region": "us-east-1"},
                                "hostname": "cloudfront.amazonaws.com", …}},
                 "isRegionalized": false,
                 "partitionEndpoint": "aws-global"}}
```

### 처리 — 있음만 담고 없음은 담지 않습니다

`capacitykb/parsers/aws_endpoints.py`

1. 리전 표시 이름을 그대로 옮깁니다 (`ap-northeast-2` → `Asia Pacific (Seoul)`).
2. (서비스, 리전) 엔드포인트 **9,039쌍**을 담습니다.
3. **가짜 리전 1,510개를 버립니다**(fips-·prod- 같은 접두 변형).
4. **"엔드포인트가 없다"는 "그 리전에서 못 쓴다"가 아닙니다.** CloudFront는
   엔드포인트가 1개인데 us-east-1 전용이어서가 아니라 **글로벌 서비스**라서입니다.
   판별자(`isRegionalized`)가 **307개 중 22개에만** 있고, 엔드포인트가 2개 이하인
   위험군 34개 중 절반인 17개는 판별자가 없습니다. 그래서 **있음만 담습니다.**
   질의 쪽은 세 상태로 답합니다 — 있음 / 글로벌(원본이 밝힌 경우만) / 모름.
5. **서비스 이름은 짐작으로 붙이지 않습니다.** CFN 네임스페이스(`AWS::EC2::` → `ec2`)를
   botocore 서비스 id에 붙이면 281개 중 148개가 맞고, 하이픈을 지우면 34개가 더 붙어
   182개(65%)입니다(`acmpca` → `acm-pca`, 충돌 0건이라 안전). 남는 99개는 철자 문제가
   아니라 **정말 다른 이름**입니다(`cloudwatch`→`monitoring`, `cognito`→`cognito-idp`).
   **못 붙는 것은 못 붙는다고 답합니다.**

### 처리 후 형태

`data/aws-endpoints.json.gz`

```json
"partitions": {"aws": {"name": "AWS Standard",
   "regions": {"ap-northeast-2": {"description": "Asia Pacific (Seoul)"}, …}}}

"services": {"aws": {
   "access-analyzer": {"regions": ["af-south-1", …, "ap-northeast-2", …],
                       "global": null, "partition_endpoint": null},
   "account": {"regions": [], "global": true, "partition_endpoint": "aws-global"}, …}}

"dropped_pseudo_regions": 1510
"note": "records only that an endpoint **exists**. a (service, region) pair that is not
  listed does not mean 'you cannot use it' — it means 'this data does not know'. …
  the marker that tells one apart (isRegionalized) is on only 22 of the 307 services."
```

---

## 12. `cfn-lint` — AWS의 조건부·리전별 허용값

`https://files.pythonhosted.org/packages/ba/f3/5218…/cfn_lint-1.53.1-py3-none-any.whl`
· 태그 `1.53.1` · **MIT-0** · PyPI wheel URL은 경로에 해시가 박혀 있어 **내용 주소**입니다

### 원본 형태

wheel 안 `cfnlint/data/schemas/extensions/` 아래 **100개 파일**. 두 모양입니다.

**(a) 리전별 enum** — `aws_amazonmq_broker/instancetype_enum.json`

```json
{"af-south-1": {"enum": ["mq.m5.2xlarge", "mq.m5.4xlarge", "mq.m5.large", …, "mq.t3.micro"]},
 "ap-east-1":  {"enum": [ … ]},
 …}
```

**(b) 조건부 제약** — `aws_rds_dbcluster/aurora.json`

```json
{"if":   {"properties": {"Engine": {"enum": ["aurora-mysql", "aurora-postgresql"],
                                    "type": "string"}},
          "required": ["Engine"]},
 "then": {"properties": {"AllocatedStorage": false, "DBClusterInstanceClass": false,
                         "Iops": false, "PubliclyAccessible": false,
                         "StorageType": {"if": {"type": "string"},
                                         "then": {"enum": ["aurora", "aurora-iopt1"]}}}}}
```

**AWS 공식 스키마에는 조건부 표현이 아예 없습니다** — `if`/`then` 전수 0건.
메타스키마가 금지하고 있어서 cfn-lint가 별도 파일로 관리합니다.

### 처리

`capacitykb/parsers/cfnlint.py` — 두 산출물로 갈립니다.

1. **함정 — `"all"` 키를 리전으로 읽으면 안 됩니다.**
   `aws_ec2_instance/instancetype_enum.json`의 `"all"`은 **빈 enum**입니다(이 파일
   하나만 그렇습니다). 리전인 줄 알고 담으면 "허용값이 0개"인 제약이 생기고 **모든
   인스턴스 타입이 거부됩니다.** 하필 값이 가장 많은 파일입니다. 그래서 리전
   모양(`us-east-1` 꼴)에 맞는 키만 읽습니다.
2. **속성 이름은 짐작하지 않고 대조합니다.** 파일명(`instancetype_enum.json`)에서
   후보를 만들되 **CFN 스키마에 실재하는지 확인**하고 없으면 담지 않고 셉니다 —
   `elasticsearchclusterconfig_instancetype_enum.json`처럼 중첩 경로를 담은 이름이
   있어 규칙만으로는 못 맞춥니다.
3. 조건부는 `if`의 프로퍼티를 `conditions` **배열**로 폅니다. 대부분 조건이 둘
   (엔진 × 버전)이라 조건 하나로는 못 담습니다.

### 처리 후 형태

`data/aws-regions.json.gz` — `constraints` **385건** ((리전, 값) 쌍 79,809개를 품음)

```json
{"type_id": "aws::AWS::AppStream::Fleet", "property": "InstanceType", "kind": "enum",
 "value": ["Accelerated.g4dn.12xlarge", …],
 "evidence": "cfn-lint-region", "basis": "stated"}
```

`data/aws-conditional.json.gz` — `constraints` **966건** · 타입 4종

```json
{"type_id": "aws::AWS::EC2::Instance", "property": "Iops", "kind": "max",
 "value": 64000, "conditional": false, "evidence": "cfn-lint-conditional",
 "basis": "stated", "conditions": [{"property": "VolumeType", "op": "eq", "value": "io1"}]}
```

손 큐레이션이지만 **원본이 그렇게 적어 놓은 것**이라 `basis=stated`입니다.

---

## 13. `bicep-types-az` — Azure 타입·플래그 ★2위 기여 소스

`https://raw.githubusercontent.com/Azure/bicep-types-az/ef7421bb…/generated`
· 커밋 `ef7421bb…` · **MIT** · 태그가 `v0.1`/`v0.0-test`뿐이라 커밋 SHA로 고정

### 원본 형태

**색인 한 개 + 프로바이더별 상세 다수**입니다. `index.json`에 **33,542개** 리소스
버전이 들어 있고 각각 상세 파일의 배열 인덱스를 가리킵니다.

```json
"Microsoft.Compute/virtualMachines@2026-03-01":
    {"$ref": "compute_1/microsoft.compute/2026-03-01/types.json#/827"}
```

상세 파일은 **`$type` 태그가 붙은 평평한 배열**이고 `$ref`가 배열 인덱스입니다
(`azure-network_microsoft.network_2025-07-01_types.json`, 4,241개 항목):

```json
[0]    {"$type": "StringType", "maxLength": 128}
[15]   {"$type": "IntegerType", "minValue": 8}
[9]    {"$type": "UnionType", "elements": [{"$ref": "#/7"}, {"$ref": "#/8"}, {"$ref": "#/2"}]}
[36]   {"$type": "ArrayType", "itemType": {"$ref": "#/22"}}
[4]    {"$type": "ObjectType",
        "name": "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies",
        "properties": {
          "id":         {"type": {"$ref": "#/2"}, "flags": 10, "description": "The resource id"},
          "name":       {"type": {"$ref": "#/0"}, "flags":  9, "description": "The resource name"},
          "type":       {"type": {"$ref": "#/1"}, "flags": 10, …},
          "apiVersion": {"type": {"$ref": "#/3"}, "flags": 10, …},
          "properties": {"type": {"$ref": "#/5"}, "flags":  0, …},
          "etag":       {"type": {"$ref": "#/2"}, "flags":  2, …}}}
[1180] {"$type": "ResourceType",
        "name": "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2025-07-01",
        "body": {"$ref": "#/4"}, "readableScopes": 8, "writableScopes": 8}
```

### 처리

**여러 파서가 이 소스를 공유합니다** — capacitykb(제약), graphkb(관계), 그리고
azure_mutability·azure_secret·azure_operations가 **타입 색인 대조용**으로 씁니다.

**제약** (`capacitykb/parsers/azure.py`)

```
IntegerType  minValue/maxValue                → min / max
StringType   minLength/maxLength/pattern      → min_length / max_length / pattern
ArrayType    minLength/maxLength              → min_items / max_items
UnionType    (전부 StringLiteralType일 때)     → enum
flags 비트    1=required · 2=read_only         → required / mutability
```

> **주의 — `flags` 8(DeployTimeConstant)은 불변성이 아닙니다.** name/type/apiVersion에만
> 붙는 배포 시점 상수 표시라 mutability로 쓰면 안 됩니다. 위 발췌의 `flags: 10`은
> `2|8` = read_only + DeployTimeConstant, `9`는 `1|8` = required + DeployTimeConstant입니다.

**bicep에는 CFN의 `createOnlyProperties`에 해당하는 불변 정보가 없습니다.** 원본에
없어서가 아니라 **생성기가 버립니다** — `azure-rest-api-specs`의
`x-ms-mutability: ["read","create"]`가 생성 불변성인데, bicep 생성기가 이를
writable&readable로 접어 `flags: None`으로 만듭니다(`ObjectTypePropertyFlags`에
`Immutable` 멤버 자체가 없습니다). 우리 캐시에서 `x-ms-mutability` 출현은 **0건**입니다.
그래서 14번을 따로 받습니다.

> 다만 "bicep이 제약을 잃는다"는 일반화는 **틀렸습니다.** `pattern` 920 ·
> `maxLength` 827 · `minValue` 446 · `maxValue` 337은 그대로 있고 이 파서가 전부
> 소비합니다. 잃는 건 **불변성 하나**입니다.

**관계** (`graphkb/parsers/azure.py`)

1. **ARM 타입명이 곧 계층입니다.** `Microsoft.Network/virtualNetworks/subnets`는
   `virtualNetworks`의 자식 — 이름만으로 `contained_in` 엣지가 나옵니다
   (evidence `arm-hierarchy`, **규칙 위반 0/2,223 확인** → stated).
2. swagger의 arm-id 참조 메타데이터는 bicep 생성 과정에서 소실되므로, **ObjectType
   이름을 정규화**(Common 접두사 제거, 단수/복수 보정)해 리소스 타입과 **유일 매칭**되면
   참조 엣지로 봅니다 (evidence `bicep-ref` → 짐작, 검수표로 확정).
3. 그 밖 `*Id` 문자열 프로퍼티는 CFN과 같은 이름 휴리스틱 (evidence `heuristic`).
4. 노드·계층은 `index.json` 한 파일로 커버하고, **참조 엣지는 용량 때문에 선택된
   프로바이더**(기본 network/compute/containerservice)의 상세만 받습니다.

### 처리 후 형태

`data/azure-capacity.json.gz` — `constraints` 42,831건
(`bicep-flags` **36,724** + `bicep-type` **6,107**) · 타입 3,371종

```json
{"type_id": "azure::Microsoft.Addons/supportProviders/supportPlanTypes", "property": "id",
 "kind": "mutability", "value": "read_only",
 "evidence": "bicep-flags", "basis": "stated"}

{"type_id": "azure::Microsoft.Advisor/assessments", "property": "name",
 "kind": "pattern", "value": "^[-0-9a-zA-Z_]{1,63}$", "value_type": "string",
 "evidence": "bicep-type", "basis": "stated"}
```

`data/azure-graph.json.gz` — `nodes` 3,382 · `edges` 2,514
(`arm-hierarchy` 2,223 · `bicep-ref` 248 · `heuristic` 42 · `human-review` 1)

```json
{"from": "azure::Microsoft.Advisor/recommendations/suppressions",
 "to": "azure::Microsoft.Advisor/recommendations", "type": "contained_in",
 "via_property": "", "required": true, "cardinality": "one",
 "evidence": "arm-hierarchy", "basis": "stated", "reviewed": true}
```

---

## 14. `azure-rest-api-specs` — 필드 **셋만** 캐냅니다

`https://codeload.github.com/Azure/azure-rest-api-specs/tar.gz/76ca9f3e…`
· 커밋 `76ca9f3e…` · 라이선스 미확인 · tarball **200.5 MB**

191MB짜리 저장소에서 필드 셋을 캐는 것이라, "왜 이것만 쓰나"가 기록돼 있지 않으면
다음 사람이 헷갈립니다. 나머지 제약은 13번이 이미 담고 있습니다.

### 원본 형태

`specification/analysisservices/…/stable/2017-08-01/analysisservices.json`:

```json
"properties": {
  "id":   {"type": "string", "readOnly": true, "description": "An identifier that …"},
  "location": {"type": "string", "description": "Location of the Analysis Services resource.",
               "x-ms-mutability": ["create", "read"]},          ← ① 불변성
  …}

"put": {…, "responses": {"200": {…}, "202": {"description": "Preparing. The operation is
        still completing.", …}},
        "x-ms-long-running-operation": true}                     ← ② 작업 시간
```

```json
"ValueSecretInfo": {"properties": {
   "value": {"x-nullable": true, "description": "The actual value of the secret.",
             "type": "string", "x-ms-secret": true}}}            ← ③ 비밀 여부
```

### 처리 — 세 파서, 세 산출물

세 파서 모두 **네임스페이스별 최신 stable 하나**만 봅니다(`_latest_stable`).
실측: stable 스펙 6,842개 → 최신만 1,422개.

**① `azure_mutability.py`** — `x-ms-mutability`

- `create`가 있으면 담되 `update` 유무로 `create_only` / `updatable`을 가릅니다.
  예전엔 `update`가 있으면 그냥 버렸는데, 그러면 **원본이 명시한 사실이 "우리가
  모른다"와 같은 모양**이 됩니다.
- `read`만 있는 것은 담지 않습니다 — 읽기 전용은 `bicep-flags`가 이미 4,704건 담고
  있어 중복이고, **라벨 하나에 성격 하나**라는 규칙상 섞으면 안 됩니다.
- 값 조합 실측: `[create,read]` 1,133 · `[create,read,update]` 208 ·
  `[create,update]` 198 · `[create]` 152 · `[read]` 134 · `[read,update]` 22 · `[update]` 5.
  **생성 후 불변**(create 있고 update 없음)이 1,285건 / 428종.

> **ARM 규약을 일반화하지 않은 이유 — 반례를 셌습니다.** "ARM은 이름과 리전을 못
> 바꾼다"를 규약으로 선언해 4,358건을 채우자는 안이 있었습니다. 원본을 세어 보니
> `name`은 반례 0건이지만 `location`은 **반례 2건**이 있었습니다
> (`Microsoft.DocumentDB/cassandraClusters`, `Microsoft.Capacity/reservationOrders`가
> `[create,read,update]`). 규약으로 채웠다면 최소 2종에서 거짓을 단언했을 것이고,
> 어느 2종인지 알 방법도 없었을 것입니다. **표시가 붙은 것만 담습니다.**

**② `azure_operations.py`** — `x-ms-long-running-operation`

- **모순은 담지 않습니다.** 같은 타입·같은 메서드를 파일마다 다르게 말하는 것이
  15종 있습니다(산출물의 `conflicting` 필드). 어느 쪽이 맞는지 모르는데 하나를
  고르면 그건 우리 짐작입니다.
- **POST 액션 경로는 마지막 마디를 떼고 타입을 구합니다.**
  `/providers/Microsoft.Compute/virtualMachines/{vm}/start`에서 `start`는 액션이지
  타입이 아닙니다. 그대로 부르면 타입/이름 교대 규칙상
  `Microsoft.Compute/locations/virtualMachinesBulkCancel` 같은 없는 타입이 나옵니다.

**③ `azure_secret.py`** — `x-ms-secret`

- **PUT 본문에 있는 것만 담습니다.** 응답에만 나오는 secret은 오히려 **읽을 수 있는**
  것이라 "다시 못 읽는다"는 이 축의 뜻과 어긋납니다.
- 실측: PUT 본문 안 + 우리 Azure 색인에 조인되는 것 **288건 / 106종**. 상위 이름이
  뜻을 그대로 드러냅니다 — `password` 39 · `adminPassword` 16 ·
  `administratorLoginPassword` 13 · `storageAccountAccessKey` 10 · `clientSecret` 8.
  담지 않은 definitions 전체(1,107건)에는 응답 전용 키가 섞여 있습니다.

### 처리 후 형태

`data/azure-mutability.json.gz` — `constraints` **1,275건** / 486종

```json
{"type_id": "azure::Microsoft.AnalysisServices/servers", "property": "location",
 "kind": "mutability", "value": "create_only",
 "evidence": "swagger-mutability", "basis": "stated"}
```

이게 없을 때 **Azure 타입 3,371종 전부가 "변경 불가로 알려진 속성이 없습니다"**라고
답하고 있었습니다 — 데이터 부재가 사실 부재로 읽히는 최대 규모 사례였습니다.

`data/azure-operations.json.gz` — `types` **1,839건** · `conflicting` 15

```json
{"type_id": "azure::Microsoft.AAD/domainServices", "create": true, "delete": true, "update": true}
```

값이 없으면 **"모른다"이지 "빠르다"가 아닙니다.** 변별력이 있어서 담을 값어치가
있습니다 — 전부 LRO인 타입 517종 vs 전부 동기인 타입 512종으로 절반씩 갈립니다.

`data/azure-secret.json.gz` — `constraints` **230건** / 106종

```json
{"type_id": "azure::Microsoft.App/connectedEnvironments",
 "property": "properties.daprAIConnectionString", "kind": "secret", "value": true,
 "evidence": "swagger-secret", "basis": "stated"}
```

`x-ms-mutability`와 **겹치지 않습니다** — 그쪽은 "바꾸면 재생성되나", 이쪽은 "읽을 수
있나"입니다. 두 축이 직교하므로 같은 속성에 둘 다 붙을 수 있고 그래도 중복이 아닙니다.

---

## 15. `kcc-crd` — GCP 리소스 정의

`https://raw.githubusercontent.com/GoogleCloudPlatform/k8s-config-connector/v1.153.0`
· 태그 `v1.153.0` · Apache-2.0 · CRD 553개 캐시 + 소스 tarball 39.5 MB

### 원본 형태

**(a) CRD YAML** — `config/crds/resources/<kind>.yaml`

```yaml
          spec:
            description: ComputeSubnetworkSpec defines the desired state of ComputeSubnetwork
            properties:
              description:
                description: Immutable. An optional description of this resource.
                  Provide this property when you create the resource. This field can
                  be set only at resource creation time.          ← 불변성이 산문 접두사로
                type: string
              networkRef:
                description: The network this subnet belongs to. Only networks that
                  are in the distributed mode can have subnetworks.
                oneOf:
                - not: {required: [external]}
                  required: [name]
                - not: {anyOf: [{required: [name]}, {required: [namespace]}]}
```

**`networkRef`가 어느 kind를 가리키는지 CRD 안에는 구조화 메타데이터가 없습니다.**

**(b) ServiceMapping** — `config/servicemappings/<service>.yaml` (그 답이 여기 있습니다)

```yaml
    - name: google_compute_backend_bucket
      kind: ComputeBackendBucket
      resourceReferences:
      - key: bucketRef
        tfField: bucket_name
        description: |-
          Reference to the bucket.
        gvk:
          kind: StorageBucket                ← 구조화된 명시 메타데이터
          version: v1beta1
          group: storage.cnrm.cloud.google.com
```

**(c) 샘플** — `config/samples/resources/<kind>/<시나리오>/*.yaml`

```yaml
apiVersion: alloydb.cnrm.cloud.google.com/v1beta1
kind: AlloyDBCluster
metadata:
  name: alloydbcluster-sample-regular
spec:
  location: asia-south2
  networkConfig:
    networkRef:
      name: alloydbcluster-dep-regular
  projectRef:
    external: PROJECT_ID
```

### 처리 — 세 파서가 다른 축을 봅니다

**제약** (`capacitykb/parsers/gcp.py`)

> **먼저 알아야 할 것 — 여기서 수치 한도는 나오지 않습니다.** CRD 510개의 `spec`
> 서브트리를 전수로 센 결과입니다.
>
> ```
> required            2,631건 / 474종
> Immutable. 접두사   2,187건 / 363종
> enum                   17건 /  12종
> pattern                 6건 /   5종
> default                 7건 /   2종
> maxLength               1건 /   1종
> minimum · maximum       0건 /   0종   ← 하나도 없다
> ```
>
> 이 파서가 메우는 것은 **커버리지**이지 "얼마까지 되나"가 아닙니다. 나중에 "GCP 제약이
> 왜 이것뿐이냐"는 질문에 **안 뽑아서가 아니라 원본에 없어서**라고 답할 수 있어야 합니다.

불변성은 **두 표기를 합집합으로** 읽습니다. 둘이 일치하지 않기 때문입니다(실측):

```
둘 다                55건
CEL만 (접두사 없음)  19건   ← 접두사만 읽으면 놓친다
접두사만          2,132건
```

접두사가 있는데 CEL이 변경을 허용하는 **모순은 0건**입니다. 즉 접두사는 과다 보고를
하지 않고 **누락만** 합니다 — 그래서 짐작이 아니라 명시로 취급하고, 어느 쪽이 근거인지는
evidence로 남깁니다(`kcc-immutable-prefix` vs `kcc-cel-immutable`). CEL 규칙은 98건 전부
`self == oldSelf` 한 가지 모양입니다.

**관계** (`graphkb/parsers/gcp.py`) — `*Ref` 필드의 대상 kind를 **3단계로** 해석합니다.

```
① servicemappings의 (kind, key) → gvk.kind      evidence=kcc-ref          stated
② description 정규식                             evidence=kcc-description  inferred
   "Allowed value: The `selfLink` field of a `ComputeNetwork` resource."
   "externally managed ComputeNetwork resource" / "The name of a X resource"
③ 필드명 휴리스틱 (networkRef → *Network 유일 매칭)  evidence=heuristic       inferred
```

DCL 기반 CRD는 description이 generic이라 ①번 없이는 대상을 알 수 없습니다.
`projectRef`/`folderRef`/`organizationRef`/`billingAccountRef`는 GCP 자원 계층이라
별도 라벨(`kcc-hierarchy`, stated — 설명문이 "belongs to"라고 말하고 projectRef 273/296)입니다.

**번들** (`bundlekb/parsers/kcc.py`) — `config/samples/resources/`

- 시나리오 **443개** 중 kind가 2개 이상인 **296개**만 담습니다(단일 리소스 147개는
  '리소스 군'이 아닙니다).
- `Namespace`·`Secret`·`ConfigMap`과 `apiVersion`이 `k8s`로 시작하는 것은 **쿠버네티스
  살림이지 클라우드 리소스가 아니라** 뺍니다.
- kind가 우리 graphkb 노드 id와 **그대로 맞습니다**(`gcp::AlloyDBCluster`).

> **왜 이 소스인가 — 두 번 헛짚고 찾았습니다.** ① `cloud-foundation-fabric` 모듈은
> 86개 중 63개가 무조건 리소스 0개였습니다(Terraform이 주 리소스에도
> `count = var.x_create ? 1 : 0`을 걸기 때문). ② 변수 기본값까지 추적해도 리소스 선언
> 719개 중 **46개(6.4%)만** 풀렸습니다. 억지로 밀지 않고 **이미 핀이 박힌 소스의 안 쓰던
> 디렉터리**를 봤습니다.

### 처리 후 형태

`data/gcp-capacity.json.gz` — `constraints` 6,923건 · 타입 509종

```json
{"type_id": "gcp::AccessContextManagerAccessLevelCondition",
 "property": "devicePolicy.osConstraints.osType", "kind": "required", "value": true,
 "evidence": "kcc-crd-schema", "basis": "stated", "backend": "tf2crd"}      ← 2,418건

{"type_id": "gcp::AccessContextManagerAccessLevelCondition", "property": "resourceID",
 "kind": "mutability", "value": "create_only",
 "evidence": "kcc-immutable-prefix", "basis": "stated", "backend": "tf2crd"}  ← 1,060건

{"type_id": "gcp::AIStreamsCluster", "property": "location",
 "kind": "mutability", "value": "create_only",
 "evidence": "kcc-cel-immutable", "basis": "stated", "backend": "direct"}     ← 62건
```

**`backend` 칸이 이 소스의 함정을 드러냅니다.** 하나처럼 보이지만 내부적으로
세 파이프라인(`tf2crd` · `dcl2crd` · `direct`)이 섞여 있고, `tf2crd`는 vendoring된
terraform-provider-google **4.84.0(2023-09-26)** 기준입니다. 그래서 레코드마다 어느
파이프라인에서 왔는지를 **등급이 아니라 사실로** 적습니다 — 위아래를 이름에 박으면
나중에 판단이 바뀔 때 이름이 거짓이 됩니다. (그 낡음을 메우는 것이 17번입니다.)

`data/gcp-graph.json.gz` — `nodes` 527 · `edges` 1,052
(`kcc-hierarchy` 375 · `kcc-ref` 292 · `kcc-description` 255 · `heuristic` 112 · `human-review` 18)

```json
{"from": "gcp::AccessContextManagerAccessLevel", "to": "gcp::AccessContextManagerAccessPolicy",
 "type": "references", "via_property": "accessPolicyRef", "required": true,
 "cardinality": "one", "evidence": "kcc-ref", "basis": "stated",
 "target_property": "name", "reviewed": true}
```

`data/kcc-bundles.json.gz` — `bundles` **296건**

```json
{"id": "kcc::accesscontextmanageraccesslevel", "name": "kcc/accesscontextmanageraccesslevel",
 "provider": "gcp", "evidence": "kcc-sample",
 "anchor": "gcp::AccessContextManagerAccessLevel",
 "caveat": "**This is the minimal working configuration Google picked as an example.**
   Applying it creates all of them, but that does not mean the API enforces this set.",
 "members": [{"typeId": "gcp::AccessContextManagerAccessLevel", "tier": "always"},
             {"typeId": "gcp::AccessContextManagerAccessPolicy", "tier": "always"}]}
```

---

# C. 회사 대신 커뮤니티가 만든 설명서 — Terraform provider (9종)

공개 스키마가 없는 클라우드가 많습니다. 그런 곳은 Terraform provider가 유일한
경로입니다. 전부 MPL-2.0이고, 원본 형태가 **Go 소스 코드**라는 점이 공통입니다.

> **셋 다 같은 경고를 답니다.** `ForceNew`는 "Terraform이 재생성한다"이지 "API가
> 거부한다"가 아니고, `validation.IntBetween`은 **프로바이더 작성자의 주장**입니다.
> 그래서 표시 문구는 반드시 `"바꾸면 리소스 재생성"`이어야 하고 `"API가 거부한다"`면
> 거짓이 됩니다.

## 16. `tpaws-provider` — AWS 교차 필드 조건

`https://github.com/hashicorp/terraform-provider-aws/archive/refs/tags/v6.55.0.tar.gz`
· 태그 `v6.55.0` · MPL-2.0 · tarball **108.3 MB**

### 원본 형태

**(a) 리소스 스키마** — `internal/service/<서비스>/*.go` (사람이 쓴 Go)

```go
// terraform-provider-aws-6.55.0/internal/service/ec2/ec2_instance.go
"capacity_reservation_preference": {
    Type:             schema.TypeString,
    Optional:         true,
    ValidateDiagFunc: enum.Validate[awstypes.CapacityReservationPreference](),
    ExactlyOneOf:     []string{"capacity_reservation_specification.0.capacity_reservation_preference",
                               "capacity_reservation_specification.0.capacity_reservation_target"},
},
"capacity_reservation_id": {
    Type:          schema.TypeString,
    Optional:      true,
    ConflictsWith: []string{"…capacity_reservation_target.0.capacity_reservation_resource_group_arn"},
},
```

**(b) 서비스 이름표** — `names/data/names_data.hcl`

```hcl
service "accessanalyzer" {
  sdk   { id = "AccessAnalyzer"
          arn_namespace = "access-analyzer" }        ← CFN 타입과 잇는 열쇠
  names { provider_name_upper = "AccessAnalyzer"
          human_friendly      = "IAM Access Analyzer" }
  resource_prefix { correct = "aws_accessanalyzer_" }
}
```

### 처리

`capacitykb/parsers/tpaws.py`

1. **왜 필요한가** — CloudFormation은 조건부를 표현할 방법이 없습니다(메타스키마가
   `if`/`then`을 금지하고 `oneOf`는 값이 아니라 존재 조건만 말합니다). 그래서 우리 AWS
   데이터에는 **교차 필드 조건이 0건**이고 **조건부 불변도 0건**이었습니다.
   프로바이더에는 둘 다 있습니다 — 실측 v6.55.0: 교차 조건 1,219 · `ForceNewIf` 56.
2. **`tpg`와 성격이 다릅니다.** 생성 코드 비율이 google 100% / aws **19%**입니다.
   google은 "선언은 있는데 생성 중 증발"이 문제였지만(빈 목록 194건), aws는 사람이 쓴
   Go라 빈 목록이 **0건**입니다. 대신 손 큐레이션이라 **다르게 틀릴 수 있어서**
   근거 라벨을 나눕니다(`tpaws-schema` vs `tpg-schema`).
3. **이름 잇기.** TF 리소스 이름과 CFN 타입 이름은 규칙이 다릅니다
   (`aws_prometheus_scraper` → `AWS::APS::Scraper`,
   `aws_vpc_dhcp_options` → `AWS::EC2::DHCPOptions`). `names_data.hcl`의
   `arn_namespace`로 서비스를 잇고 나머지는 이름 후보로 맞춥니다. **실측 매칭률 50%**,
   못 맞춘 것은 버리되 셉니다 — 상당수는 매핑 실패가 아니라 **CFN에 그 리소스가 아예
   없는 것**입니다(`aws_account_alternate_contact` 등).

### 처리 후 형태

`data/aws-tf.json.gz` — `constraints` **2,756건** · 타입 338종

```json
{"type_id": "aws::AWS::AccessAnalyzer::Analyzer", "property": "InternalAccess",
 "kind": "mutability", "value": "create_only",
 "evidence": "tpaws-schema", "basis": "stated"}
```

`_coverage`: *"**this is Terraform's judgment, not the API's** — ForceNew means
'it recreates', and IntBetween is the provider author's claim."*

---

## 17. `tpg-provider` — GCP 최신값

`https://github.com/hashicorp/terraform-provider-google/archive/refs/tags/v7.40.0.tar.gz`
· 태그 `v7.40.0` · MPL-2.0 · tarball 16.9 MB

### 원본 형태

`google/services/<서비스>/resource_*.go` — **Magic Modules가 생성한** Go 코드입니다.

```go
// terraform-provider-google-7.40.0/google/services/compute/resource_compute_disk.go
"size": {
    Type:     schema.TypeInt,
    Computed: true,
    Optional: true,
    Description: `Size of the persistent disk, specified in GB. …

~>**NOTE** If you change the size, Terraform updates the disk size
if upsizing is detected but recreates the disk if downsizing is requested.`,
},
"snapshot": {
    Type:             schema.TypeString,
    Optional:         true,
    ForceNew:         true,
    DiffSuppressFunc: tpgresource.CompareSelfLinkOrResourceName,
    …
},
```

### 처리

`capacitykb/parsers/tpg.py` — Go를 통째로 파싱하지 않고 **주석을 걷어낸 뒤**
스키마 리터럴 블록만 정규식으로 집습니다.

**왜 Magic Modules YAML이 아니라 생성된 프로바이더인가** — 셋입니다.

1. **핀을 박을 수 있습니다.** MM 저장소는 태그가 **0개**이고 하루 3.6건씩 바뀝니다.
   프로바이더는 주간 릴리스 태그가 417개 있습니다.
2. **선언과 현실이 다릅니다.** MM이 적어 놓은 교차 필드 조건 중 상당수가 생성 과정에서
   **빈 목록으로 증발합니다**(중첩 객체의 형제 이름이 루트 기준 해석에 실패하는데 조용히
   버려집니다). 실측: ExactlyOneOf 127 · ConflictsWith 53 · RequiredWith 13 ·
   AtLeastOneOf 1. **출력을 읽으면 실제로 강제되는 것만 담기고**, YAML을 읽으면
   현실보다 엄격한 KB가 됩니다.
3. **YAML에 없는 축이 출력에는 있습니다.** `customdiff.ForceNewIfChange`가 그것으로,
   `compute_disk.size`·`subnetwork.ip_cidr_range`가 걸립니다. 뜻은 **"늘리는 건 되고
   줄이면 재생성"** — 불변/가변 이분법으로는 안 담기는 축이고 MM YAML에는 흔적조차
   없습니다(로직이 Go 코드 안에 있습니다).

**정체성은 KCC를 따릅니다.** 여기서 뽑은 것은 KCC가 아는 속성에만 붙이고, 못 붙인 것은
버리지 않고 셉니다. 그래서 15번(2023-09 vendoring)의 낡음을 **덮어쓰지 않고 보강**합니다.

### 처리 후 형태

`data/gcp-capacity.json.gz`의 일부 — `tpg-schema` **3,383건**
(같은 파일 안에서 `kcc-*` 3,540건과 나란히 놓입니다)

```json
{"type_id": "gcp::AccessContextManagerAccessLevel", "property": "resourceID",
 "kind": "mutability", "value": "create_only",
 "evidence": "tpg-schema", "basis": "stated"}
```

---

## 18–24. `tp-alicloud` · `tp-tencent` · `tp-oracle` · `tp-ibm` · `tp-nhn` · `tp-ncp` · `tp-openstack`

**한 파서가 일곱을 처리합니다** (`capacitykb/parsers/tpcsp.py`). 대상 CSP에 공개 리소스
스키마가 없어서 Terraform provider가 유일한 경로이고, 그래서 **타입 축 자체를 여는**
작업입니다 — 실측으로 graphkb 노드가 alibaba 0개, tencent 0개였습니다.

| # | 키 | 태그 | tarball | 타입 | 제약 |
|---|---|---|---:|---:|---:|
| 18 | `tp-alicloud` | `v1.285.0` | 6.9 MB | 1,134 | 9,167 |
| 19 | `tp-tencent` | `v1.83.13` | 27.8 MB | 1,320 | 11,222 |
| 20 | `tp-oracle` | `v8.23.0` | 28.5 MB | 986 | 14,614 |
| 21 | `tp-ibm` | `v2.4.0` | 7.4 MB | 558 | 1,987 |
| 22 | `tp-nhn` | `v1.0.9` | 0.36 MB | 110 | 779 |
| 23 | `tp-ncp` | `v4.0.6` | 0.41 MB | 33 | 427 |
| 24 | `tp-openstack` | `v3.4.0` | 0.53 MB | 108 | 725 |

### 원본 형태 — 등록표 두 가지 모양

**(a) 맵 리터럴** (여섯 곳) — `<provider>/provider.go`

```go
// terraform-provider-alicloud-1.285.0/alicloud/provider.go
ResourcesMap: map[string]*schema.Resource{
    "alicloud_apig_plugin_class":                 resourceAliCloudApigPluginClass(),
    "alicloud_cr_artifact_lifecycle_rule":        resourceAliCloudCrArtifactLifecycleRule(),
    "alicloud_oss_bucket_inventory":              resourceAliCloudOssBucketInventory(),
    "alicloud_wafv3_address_book":                resourceAliCloudWafv3AddressBook(),
    … 1,161종
```

**(b) 함수 호출** (oracle 혼자) — `internal/provider/register_resource.go`

```go
// terraform-provider-oci-8.23.0/internal/provider/register_resource.go
func init() {
	if common.CheckForEnabledServices("adm")             { tf_adm.RegisterResource() }
	if common.CheckForEnabledServices("aidataplatform")  { tf_ai_data_platform.RegisterResource() }
	…
}
```
서비스 패키지 안에서 `RegisterResource("oci_adm_knowledge_base", …)` 형태로 996건이
등록됩니다. 그래서 등록부의 `form` 설정이 `"call"`이면 파서가 다른 정규식을 씁니다.

**(c) 리소스 스키마** — 어느 프로바이더든 같은 SDK 문법입니다.

```go
// terraform-provider-alicloud-1.285.0/alicloud/resource_alicloud_instance.go
"instance_type": {
    Type:         schema.TypeString,
    Optional:     true,
    ValidateFunc: StringMatch(regexp.MustCompile(`^ecs\..*`), "prefix must be 'ecs.'"),
    AtLeastOneOf: []string{"instance_type", "launch_template_id", "launch_template_name"},
    Computed:     true,
},
"credit_specification": {
    Type:         schema.TypeString,
    Optional:     true,
    ValidateFunc: StringInSlice([]string{string(CreditSpecificationStandard),
                                         string(CreditSpecificationUnlimited)}, false),
},
"network_interface_id": {
    Type:     schema.TypeString,
    ForceNew: true,          ← 바꾸면 재생성
    …
},
```

### 처리

1. **등록표에서 타입 목록**을 뽑습니다. `/vendor/`와 `_test.go`는 제외합니다.
2. 각 리소스 파일의 스키마 리터럴에서 다섯 축을 읽습니다.
   ```
   ForceNew: true              → mutability = create_only
   Required: true              → required
   MaxItems / MinItems         → max_items / min_items
   StringInSlice([…])          → enum
   IntBetween(a, b)            → min / max
   ```
   alicloud v1.285.0 실측: ForceNew 4,115 · Required 3,992 · MaxItems 669 ·
   StringInSlice 1,065 · IntBetween 110.
3. **타입 이름은 Terraform 것을 그대로 씁니다.** 다른 프로바이더는 벤더 공식 이름을
   쓰지만(`AWS::EC2::Subnet`) 이 일곱은 그런 공개 스키마가 없습니다.
   `alibaba::alicloud_instance`처럼 **id에 `alicloud_`가 그대로 보이는 것이 의도**입니다 —
   이게 Terraform의 이름이라는 사실이 id에 드러나야 나중에 공식 스키마가 생겼을 때
   무엇을 바꿔야 하는지 알 수 있습니다. openstack은 타입 이름에 API 버전이 붙는데
   (`openstack_networking_network_v2`) 벤더 규약이라 그대로 씁니다.
4. **대조할 짝이 없다는 사실을 산출물에 적습니다.** aws는 CFN, gcp는 KCC라는 독립
   소스가 있어 어긋남을 셀 수 있었지만 이 일곱은 단일 소스입니다.

### 처리 후 형태

`data/<csp>-capacity.json.gz` · `data/<csp>-graph.json.gz` — 일곱 쌍이 같은 모양입니다.

```json
{"type_id": "alibaba::alicloud_ack_one_cluster", "property": "cluster_name",
 "kind": "mutability", "value": "create_only",
 "evidence": "tpcsp-schema", "basis": "stated"}

{"type_id": "oracle::oci_adm_knowledge_base", "property": "compartment_id",
 "kind": "required", "value": true,
 "evidence": "tpcsp-schema", "basis": "stated"}
```

```json
{"id": "alibaba::alicloud_ack_one_cluster", "layer": "vendor", "provider": "alibaba",
 "kind": "resource_type", "display_name": "alicloud_ack_one_cluster",
 "source": "tpcsp-schema"}
```

> **`edges`가 전부 0건입니다.** 이 일곱 CSP는 **노드만 있고 관계가 없습니다.** 스키마에
> 참조 메타데이터가 없어서이지 우리가 빠뜨린 것이 아닙니다. `core` 매핑도 alibaba·
> tencent·ibm·ncp·openstack·oracle 일부만 손 검수로 이어져 있습니다(7번 참조).
> 즉 "이걸 만들려면 뭐가 먼저 필요한가?"는 **AWS·Azure·GCP에서만** 답할 수 있습니다.

### 소스별로 따로 적어 둔 것

**20 `tp-oracle`** — 태그 API가 개발용 버전(`vdev-version`)을 먼저 주므로 releases를
봐야 실제 최신을 압니다.

**21 `tp-ibm`** — 이 저장소의 `provider_metadata.json`은 저장소 태그와 내용이 어긋난
전례가 있습니다. 우리는 그 파일이 아니라 `provider.go` 등록표와 Go 스키마를 직접
읽으므로 영향받지 않습니다.

**22 `tp-nhn`** — 정직한 기록 둘이 붙어 있습니다.

- "NHN은 공개 프로바이더가 없다"고 적어 뒀던 것이 **틀렸습니다** — 받아 보니
  `ResourcesMap`에 110종이 있었습니다(2026-07-22 확인).
- **OpenStack 프로바이더를 리브랜딩한 것입니다** — 구현 파일 111개 중 90개가
  `resource_openstack_*.go` 그대로입니다(GitHub fork 플래그는 False지만 복사본입니다).
  그래서 24번과 이름이 **84/110 겹치는 것은 교차 검증이 아니라 같은 코드**입니다.
  **독립된 두 소스로 세면 안 됩니다.**

**23 `tp-ncp`** — 절반이 Plugin Framework로 이전해 우리 파서가 못 읽습니다. 그래서
타입이 33종뿐이고, 그 사실이 `core_vendor_map.json`의 `description`에 적혀 있습니다.

---

# D. 가격 (3종)

## 25. `aws-price-list` — AWS 가격표 API ★재배포 금지

`https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/20260721012550/ap-east-2/index.json`
· 태그(버전 URL) `20260721012550` · 라이선스 미확인 · **재배포 거부(`denied`)**

### 원본 형태

한 리전 오퍼 파일에 `products` 17,365개. **디스크 한도가 가격 데이터의 속성으로**
들어 있습니다.

```json
"6NFX9BJ63GJDFVBS": {
  "sku": "6NFX9BJ63GJDFVBS", "productFamily": "Storage",
  "attributes": {
    "servicecode": "AmazonEC2", "location": "Asia Pacific (Taipei)",
    "storageMedia": "SSD-backed", "volumeType": "General Purpose",
    "maxVolumeSize": "16 TiB",                    ← 값이 반쯤 산문
    "maxIopsvolume": "16000",
    "maxIopsBurstPerformance": "3000 for volumes <= 1 TiB",
    "maxThroughputvolume": "250 MiB/s",
    "volumeApiName": "gp2"}}
```

관리형 서비스 쪽은 별도 오퍼 파일(서비스별 12개를 캐시)이고 모양이 다릅니다:

```json
"XRUG9X8484P22XMM": {"productFamily": "Database Instance",
  "attributes": {"servicecode": "AmazonRDS", "location": "Asia Pacific (Seoul)",
    "instanceType": "db.r5b.2xlarge.tpc1.mem2x", "vcpu": "8", "memory": "128 GiB",
    "databaseEngine": "Oracle", "deploymentOption": "Single-AZ", …}}

"terms": {"OnDemand": {"VAAZT7R5GZ3MM7SG.JRTCKXETXF": {
   "priceDimensions": {"…": {
      "description": "USD 13.68 per db.r5.12xlarge Multi-AZ instance hour (…) running MySQL",
      "unit": "Hrs", "pricePerUnit": {"USD": "13.6800000000"}}}}}}
```

### 처리 — 두 갈래

**(a) 디스크 한도 교차 검증** (`capacitykb/parsers/aws_limits.py`) — 10번에서 설명.
값이 반쯤 산문(`"16 TiB"`)이라 단위를 파싱해 GiB로 맞추고, botocore 설명문과
**둘이 같은 값을 말했을 때만** 담습니다. 볼륨 종류 속성은 리전 불변이라 작은
리전(ap-east-2)에서 받습니다(43 MB).

**(b) 관리형 과금 축** (`costkb/parsers/aws_managed.py`) — **로컬 빌드 전용**입니다.

축 판정이 **단위 문자열의 함정**을 지납니다:

```
Hrs (맨 시간)               instanceHour   RDS 인스턴스·EKS 클러스터
*CapacityUnit*-Hrs          capacityRate   DynamoDB RCU/WCU — 단위 수가 사이징 결과
GB-Mo · *-months            capacityRate   저장 용량 × 시간
LCU-Hrs · Requests · GB …   usage          부하가 소비하는 단위 — 트래픽 의존
```

DynamoDB의 `ReadCapacityUnit-Hrs`는 단위가 시간이지만 **용량 단위 수에 비례**합니다.
LCU는 반대로 이름이 용량이지만 **부하가 소비**하므로 사용량입니다. 모르는 단위는
`usage`로 둡니다 — 시간당 단가를 지어내는 방향보다 안전합니다.

큐레이션 예외 둘: `AWSELB`는 ALB·GWLB·CLB가 한 파일이라 **Load Balancer-Network**만
취하고, `AmazonS3`는 Data Transfer(이그레스) family가 섞여 있어 뺍니다. OnDemand 텀만
읽고 Outposts·Local Zone 행은 `locationType`으로 거릅니다.

### 처리 후 형태

`data/aws-limits.json.gz`의 20건 (10번 참조) — **한도만 남고 가격은 남지 않습니다.**

관리형 축은 **`data/`에 존재하지 않습니다**:

```
python -m costkb build-aws-managed          # 각자 로컬에서
data/aws-managed-pricing.json.gz            # 존재하면 안 된다 — 테스트가 막는다
```

**법적 처리를 구조로 했습니다.** 약관이 가격 데이터 재배포를 **명시적으로 금지**합니다
(`redistribution="denied"` — 문구가 *없는* Azure·IBM과 달리 *안 된다고 적혀 있는*
경우). 그래서 `pack` 명령이 파일 이름을 보고 거절하고, `data/`에 그 파일이 있으면
테스트가 실패합니다. "커밋하지 마세요"라는 주석 대신 **구조로** 막은 것입니다.
클론 직후 aws 관리형 축이 비어 있는 것이 **정상**이고, 도구가 빌드 명령을 안내합니다.

---

## 26. `azure-retail-prices` — Azure 스팟·예약·저축

`https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview`
· **지문(버전 없음)** · 라이선스 `not-stated` · 재배포 `not-stated`(NOTICE에 공시)

리전마다 `$filter=serviceName eq 'Virtual Machines' and armRegionName eq '<리전>'`로
질의하고 `NextPageLink`를 따라갑니다. 리전 39곳, 한 리전이 8,000행 안팎입니다.

### 원본 형태 — **함정 셋이 한 화면에 보입니다**

같은 SKU(`Standard_B16als_v2`, australiacentral)에 대한 세 행입니다.

```json
{"retailPrice": 0.299, "unitOfMeasure": "1 Hour", "type": "Consumption",
 "meterName":   "B16als v2 Low Priority",
 "productName": "Basv2 Series Cloud Services",        ← ② 구형 PaaS. VM이 아니다
 "skuName": "B16als v2 Low Priority", "armSkuName": "Standard_B16als_v2"}

{"retailPrice": 0.6057, "unitOfMeasure": "1 Hour", "type": "Consumption",
 "meterName":   "B16als v2 Spot",
 "productName": "Virtual Machines Basv2 Series",      ← 진짜 VM
 "skuName": "B16als v2 Spot", "armSkuName": "Standard_B16als_v2"}

{"retailPrice": 3478.0, "unitOfMeasure": "1 Hour",    ← ③ 기간 총액인데 '1 Hour'라고 적혀 있다
 "type": "Reservation", "reservationTerm": "1 Year",
 "productName": "Virtual Machines Basv2 Series",
 "skuName": "B16als v2", "armSkuName": "Standard_B16als_v2"}
```

**함정 ①**은 발췌에 안 보이는 행입니다 — 같은 `armSkuName`에 `productName`이
`… Windows`로 끝나는 값이 따로 있고, 안 거르면 라이선스 값이 섞여 두 배 가까이 뜁니다.

①과 ②를 안 거르면 **SKU의 93.4%가 값이 여럿**이 되고, 어느 값이 잡히는지는 응답
순서가 정합니다. 처음엔 `isPrimaryMeterRegion`이 범인이라고 짐작했는데 **틀렸습니다** —
둘 다 primary였습니다. 판별자는 `productName`입니다.

### 처리

`costkb/parsers/azure_pricing.py`(할인가) · `azure_managed.py`(관리형 축)

1. `_is_vm()` — `productName`이 `Virtual Machines`로 시작하고 `Windows`로 끝나지 않는
   행만 남깁니다. 둘 다 거르면 **값이 여럿인 SKU가 0이 됩니다.**
2. `kind_of()` — 종류는 별도 필드가 아니라 **이름에 들어 있습니다.**
   `skuName`/`meterName`에 `Spot`이면 spot, `Low Priority`면 lowpriority,
   `type == "Reservation"`이면 reserved, `type == "DevTestConsumption"`이면 devtest.
3. **예약은 기간 시간으로 나눕니다** (1년 8,760 / 3년 26,280).
   `3478.00 ÷ 8760 = 0.3970/h`. 그대로 시간당으로 읽으면 **5,165배** 틀립니다.
   나눈 값이 온디맨드의 0.590(1년)·0.380(3년)이라 Azure가 공표하는 RI 할인율과 맞습니다.
   **`savingsPlan`은 반대로 진짜 시간당입니다** — 같은 응답 안에서 두 단위가 섞여
   있으므로 한 규칙으로 처리하면 한쪽이 틀립니다.
4. `_one()` — **값이 하나일 때만 씁니다.** 여럿이면 무엇이 맞는지 우리가 모릅니다.
   거를 것을 다 거르면 여럿인 경우가 0이라, 여럿이 나오면 그건 **우리가 모르는 새 축이
   생겼다**는 뜻입니다.
5. **레코드마다 미러와 대조합니다**(허용 오차 0.5%). 실측에서 API의 온디맨드가 미러와
   완전히 일치했습니다 — koreasouth 겹침 551종·어긋남 0, eastus 1,219종·어긋남 0.
   그래서 이건 보강인 동시에 **미러의 교차 확인**입니다. 다만 일치를 **가정하지 않고
   확인해서** `matchesMirror`에 담습니다.
6. **담지 않는 것**: `DevTestConsumption`(Dev/Test 구독에서만 적용 — 일반 사용자에게
   그 값을 가격이라고 말하면 거짓), `Low Priority`(스팟과 별개 미터인데 어디에
   적용되는지 이 데이터로는 알 수 없음). 건수만 `_coverage`에 적습니다.

관리형 축(`azure_managed.py`)은 **`serviceName`이 아키타입 경계를 안 지킨다**는 문제를
따로 겪습니다 — `Azure Database for PostgreSQL` 안에 `Azure Cosmos DB for PostgreSQL`이
섞여 있습니다(실측). 판별자는 여기서도 `productName`이고, 큐레이션 표가 그 일을 합니다.

### 처리 후 형태

`data/azure-discount-pricing.json.gz` — `records` **32,073건** · 리전 39곳

```json
{"specName": "standard_b12ms", "region": "australiacentral",
 "ondemandUSD": 0.634, "mirrorUSD": 0.6340000033378601, "matchesMirror": true,
 "spotUSD": null, "reserved1yUSD": null, "reserved3yUSD": null,
 "savings1yUSD": 0.461996, "savings3yUSD": 0.320297}
```

```json
"_coverage": [{"records": 32073, "mirrorAgreed": 32073, "mirrorDisagreed": 0,
  "ambiguousSkus": 0,
  "dropped": {"not-vm-or-windows": 99222, "devtest": 112026, "lowpriority": 52924,
              "not-in-mirror": 12940, "no-price": 120, "unknown-term": 2}}]
```

**버린 것을 이유별로 세어 밝힙니다** — 279,234행을 버리고 32,073행을 담았습니다.

`data/azure-managed-pricing.json.gz` — `records` **23,563건**

```json
{"archetype": "loadBalancer", "service": "Load Balancer", "product": "Load Balancer",
 "sku": "Standard", "meter": "Standard Data Processed", "unit": "1 GB",
 "axis": "usage", "unitPriceUSD": 0.005, "region": "Global"}
```

축별로 `usage` 8,717 · `instanceHour` 6,585 · `capacityRate` 8,261.
**`_note`가 "단가 한 칸"이 아니라고 못 박습니다** — instanceHour만 유효한 시간당
요율이고, capacityRate는 곱할 수량(vCore·RU·GB)이 **사이징 결과**이며, usage는
트래픽을 알아야 비용이 생깁니다. **사용량 축에 숫자 하나를 붙이는 것이 곧 모르는 것을
채우는 실패**이므로 합계는 어디서도 만들지 않습니다.

---

## 27. `cyclenerd-gcp-pricing` — GCP 스팟·약정

`https://raw.githubusercontent.com/Cyclenerd/google-cloud-pricing-cost-calculator/574d8fbb68fa/pricing.yml`
· 커밋 `574d8fbb68fa` · **Apache-2.0**(파일 안 `about.copyright`에 명시) · 3.8 MB

### 원본 형태

한 덩어리 YAML입니다. 인스턴스 × 리전 격자에 여섯 값이 붙습니다.

```yaml
    n2-highmem-8:
      cost:
        asia-east1:
          hour:        0.6068       ← 온디맨드 (다른 스냅샷)
          hour_spot:   0.214704
          month:       354.4597928
          month_1y:    279.07608
          month_3y:    199.3484
          month_spot:  156.73392
        asia-northeast1:
          hour:        0.67176
          hour_spot:   0.176352
          …
      cpu: 64
      ram: 512
```

같은 파일의 안 쓰던 섹션에 관리형 축이 있습니다:

```yaml
storage:
    balanced:
      cost:
        asia-east1:
          month: 0.1
        asia-northeast3:
          month: 0.13
```

### 처리 — 미러를 대체하지 않고 보강만 합니다

`costkb/parsers/gcp_pricing.py`(스팟·약정) · `gcp_managed.py`(관리형 축) ·
`perfkb/parsers/gcp_series.py`(GPU 수·로컬 SSD GB)

1. **온디맨드는 건드리지 않습니다.** tumblebug의 `hourlyUSD`가 그대로 남고, 스팟·약정만
   별도 산출물에 담습니다. 미러 파일은 읽기만 하고 쓰지 않습니다.
2. **두 소스는 다른 스냅샷입니다 — 그래서 괴리를 기록합니다.** Cyclenerd의 온디맨드는
   tumblebug와 **리전×패밀리 단위로** 어긋납니다(실측: `n2d`/`asia-south1` 전 크기가
   정확히 2.405배, `g4`/`asia-south2`가 0.385배). 크기마다 배율이 같으므로 이건
   **가격 스냅샷 시점 차이**이지 다른 스펙을 가리키는 게 아닙니다.
3. 그래서 레코드에 **Cyclenerd 온디맨드(`hourRefUSD`)를 함께 담고** tumblebug와의 비율
   (`mirrorRatio`)을 기록합니다. tumblebug 온디맨드에 Cyclenerd 스팟을 나란히 놓으면
   "온디맨드 $0.17인데 스팟 $0.044(=26%)"처럼 보이지만, 그 스팟은 Cyclenerd 온디맨드
   $0.147 기준입니다.
4. **자기정합성은 확인됐습니다** — 스팟이 온디맨드의 32%, 1년 63%, 3년 45%로 GCP 공식
   할인율과 일치하고 스팟>온디맨드 이상치 0건. 그래서 스팟·약정 값 자체는 믿을 수
   있습니다. 문제는 두 스냅샷을 **섞는** 것입니다.
5. **조인되는 것만 담습니다** — 미러의 gcp 스펙 `(specName, region)`에 붙는 것만.
6. 관리형 축은 **두 축을 함께 담아야 합니다.** 저장(GB/월)은 용량-비례형이고
   검색(nearline·coldline·archive의 GB당 요금)은 사용량형입니다. 검색 축을 떼면
   archive($0.0025/GB/월)가 standard($0.023)보다 싸 보이는 것으로 답이 끝나 버립니다.
   다중/이중 리전 16종은 리전 스킴이 달라(asia1, asia-multi) 담지 않습니다.

### 처리 후 형태

`data/gcp-spot-commit.json.gz` — `records` **11,193건**

```json
{"provider": "gcp", "specName": "n2-highmem-8", "region": "asia-east1",
 "hourSpotUSD": 0.214704, "month1yUSD": 279.07608, "month3yUSD": 199.3484,
 "hourRefUSD": 0.6068, "mirrorRatio": 1.235, "snapshotMatchesMirror": false}
```

`snapshotMatchesMirror: false`가 **"이 스팟 값은 미러와 다른 가격 세계의 것"**이라고
레코드 스스로 말합니다.

`data/gcp-managed-pricing.json.gz` — `records` **731건** · 리전 43곳

```json
{"archetype": "objectStorage", "region": "africa-south1", "service": "Cloud Storage",
 "product": "Cloud Storage archiv", "sku": "archiv", "meter": "retrieval",
 "unit": "GB (per retrieval)", "axis": "usage", "unitPriceUSD": 0.05}
```

`_note`가 **없는 것도 밝힙니다**: *"that is all the source has (no Cloud SQL,
Memorystore or Pub/Sub in Cyclenerd pricing.yml — measured) … standard has no retrieval
charge (absent in the source — that does not mean it is free, it means the axis is not there)."*

---

# E. 하드웨어·성능 (4종)

## 28. `ec2-hardware` — AWS 하드웨어 실측치

`…/vantage-sh/ec2instances.info/4ef36cd2…/scraper/aws/ec2/extras/manually_fetched_data.json`
· 커밋 `4ef36cd2…` · **MIT** · 1.4 MB

### 원본 형태

인스턴스 타입을 키로 하는 dict입니다.

```json
"t3.micro": {
  "ran_at": "2025-12-10T19:42:44.436913877Z",
  "coremark": {"total_ticks": 20640, "iterations_second": 29069.767442,
               "total_time_seconds": 20.64},          ← 담지 않는다
  "ffmpeg": null,
  "nvidia_gpus": [],
  "memory": {"total_mb": 904, "speed_mhz": 2933},
  "cpu": {"vendor": "GenuineIntel",
          "model": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
          "speed": 2500, "cache": 36608, "cpus": 1, "cores": 1, "threads": 2},
  "numa": {"numa_node_count": 1, "is_numa": false, …}}
```

### 처리

`perfkb/parsers/hardware.py` — 미러 스펙에 `(provider=aws, specName)`으로 붙입니다.
실측(커밋 4ef36cd2): 1,093종 중 우리 aws 스펙과 매칭 **1,069종**(우리 aws 1,349종의
79%). GPU 정보가 있는 것은 50종, 모델 표기는 11종으로 이미 정규화돼 있습니다.

**벤치마크 점수를 담지 않습니다.** 그건 "누군가 한 번 잰 값"이라 우리 데이터의 다른
값들과 성격이 다르고, 비교에 쓰는 순간 위험합니다.

- 원점수는 인스턴스 크기에 거의 정비례합니다(m7i 2vCPU 47k → 48vCPU 1,068k).
  그대로 보여주면 **큰 인스턴스가 항상 이기는** 답이 나옵니다.
- vCPU당으로 나누면 세대·아키텍처 차이가 잘 보이지만(Intel 18.7k · AMD 21.1k ·
  Graviton 25.7k), 어느 쪽을 보여주느냐가 **답을 뒤집습니다.**
- coremark는 정수 연산이라 메모리·I/O 중심 작업에는 안 맞습니다.

여기서 담는 것은 전부 **사실**입니다 — `p4d.24xlarge`에 A100이 8장 달려 있다는 건
측정이 아니라 사양입니다. **저장소가 서빙하는 209MB짜리 `instances.json`은 쓰지
않습니다** — 커밋돼 있지 않고 AWS Price List 파생물이라 고정도 재배포도 안 됩니다.

> **왜 필요했나.** 우리는 가속기(GPU) 정보를 **한 건도** 갖고 있지 않았습니다.
> 실측에서 "ap-northeast-2에서 쓸 수 있는 GPU 인스턴스 알려줘"에 모델이 표를 통째로
> 지어냈고(`g5g`를 AMD라고 했습니다 — NVIDIA입니다), 그것을 **우리 지식베이스에서
> 조회한 결과라고 명시**했습니다. **빈칸이 지어내기를 부릅니다.**

### 처리 후 형태

`data/tumblebug-perf.json.gz`의 aws 레코드에 병합됩니다 (1번 발췌 참조):

```json
{"cpuVendor": "GenuineIntel",
 "cpuModel": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
 "cpuClockMHz": 2500, "cpuCacheKB": 36608, "cpuCores": 1, "cpuThreads": 2,
 "memorySpeedMHz": 2933,
 "hardwareCheckedAt": "2025-12-10",              ← 원본의 ran_at
 "hardwareEvidence": "ec2-hardware-probe"}
```

**단일 소스입니다** — 교차 검증할 짝이 없습니다. 대신 원본이 측정 시점(`ran_at`)을 함께
적어 두어 신선도를 우리가 판단할 수 있고, 그 시각을 레코드에 그대로 싣습니다.

---

## 29. `azure-compute-docs` — VM 크기 문서의 **표**

`https://raw.githubusercontent.com/MicrosoftDocs/azure-compute-docs/9c18d88d…`
· 커밋 `9c18d88d…` · **CC-BY-4.0**(저작자 표시 의무) · md 156편 캐시

### 원본 형태

`articles/virtual-machines/sizes/<계열>/<시리즈>-series.md`. **산문 사이에 표가 있고
표만 읽습니다.**

```markdown
### [Network](#tab/sizenetwork)

Network interface info for each size

| Size Name | Max NICs (Qty.) | Max Network Bandwidth (Mbps) |
| --- | --- | --- |
| Standard_D2s_v5 | 2 | 12,500 |
| Standard_D4s_v5 | 2 | 12,500 |
…

#### Table definitions
- <sup>1</sup>Some sizes support [bursting](../../disk-bursting.md) to temporarily
  increase disk performance. …
```

생애주기 문서는 다른 모양입니다 — `sizes/lifecycle/previous-gen-sizes-list.md`:

```markdown
# Previous generation Azure VM size series

This article provides a list of all size series that are considered *previous-gen*.
Status is listed as *next-gen available* or *capacity limited* based on capacity.
```

### 처리

`perfkb/parsers/azure_sizes.py`

1. **`maxNics`·`networkBandwidthMbps` 둘만** 담습니다 — 스키마에 이미 있고(IBM이 쓰던
   칸) azure가 비어 있던 칸입니다. 표에 함께 있는 vCPU·메모리·디스크 IOPS는 **미러가
   이미 갖고 있어** 담지 않습니다 — 두 소스의 값이 섞이면 어느 스냅샷의 값인지 알 수
   없게 됩니다.
2. 각주 표기(`<sup>1</sup>`)와 `Not Supported` 칸은 실측에서 확인한 형식입니다 —
   **숫자가 아닌 칸은 담지 않고 셉니다.**
3. **구세대 판정**도 여기서 나옵니다. 생애주기 문서의 시리즈 라벨 37종을 사람이 만든
   정규식 표로 옮기되, **문서 라벨과 손 표가 어긋나면 빌드가 죽는 상호 대조**를
   내장했습니다. 구세대 146종에 표시를 붙이되 **목록에 없다고 "최신"이라고 하지
   않습니다** — 부재를 최신 주장으로 승격하는 것이 침묵 오독입니다.
4. 문서라 재편될 수 있어 **최소 매칭 수**로 감지합니다.

### 처리 후 형태

`data/tumblebug-perf.json.gz`의 azure 레코드에 병합 —
`maxNics` **25,385건** · `networkBandwidthMbps` **24,108건**
(evidence `azure-sizes-doc`, basis **stated** — 문서가 표로 명시한 것을 옮겼습니다).

> **왜 필요했나.** perfkb의 azure 34,846건은 미러에서 온 4필드
> (sustainedCpu·diskIops·acu·acceleratedNetworking)뿐이었습니다 — aws는 거의 전 필드가
> 차 있는데 azure는 네트워크 대역폭도 NIC 수도 없었습니다.

---

## 30. `gcloud-machine-types` — GCP 시리즈 특성

`https://raw.githubusercontent.com/Cyclenerd/google-cloud-compute-machine-types/add204f1…`
· 커밋 `add204f1…` · **Apache-2.0** · SQL 34개 캐시

### 원본 형태

`instances/series/*.sql` — **UPDATE 문의 나열**입니다. 생성기가 아니라 사람이 쓰지만
형식이 일정합니다.

```sql
/* N2 General-purpose */
/* https://cloud.google.com/compute/docs/machine-types#machine_type_comparison */
UPDATE instances SET
series      = 'n2',
family      = 'General-purpose',
cpuPlatform = 'Cascade Lake, Ice Lake',
localSsd    = '1',
sud         = '1',
spot        = '1'
WHERE name LIKE 'n2-%';
UPDATE instances SET bandwidth = '10' WHERE name LIKE 'n2-%-2';
UPDATE instances SET bandwidth = '16' WHERE name LIKE 'n2-%-8';
UPDATE instances SET bandwidth = '32', tier1 = '50'  WHERE name LIKE 'n2-%-32';
UPDATE instances SET bandwidth = '32', tier1 = '100' WHERE name LIKE 'n2-%-96';
```

### 처리

`perfkb/parsers/gcp_series.py`

1. **문장 단위 정규식**으로 `SET k='v', …  WHERE name LIKE '패턴'`을 읽고, LIKE 패턴을
   `fnmatch`로 옮깁니다(`%` → `*`). SQL 파싱이지만 **문법이 핀돼 있어서** 안전합니다.
2. **`tier1`은 담지 않습니다** — Tier_1 네트워킹을 활성화했을 때의 대역폭이라 기본
   구성의 값이 아닙니다. 담으면 기본 대역폭처럼 읽힙니다.
3. 같은 저자의 27번 `pricing.yml`에서 GPU 수·로컬 SSD GB를 함께 읽습니다.
   **모르는 GPU 키는 승격하지 않고 셉니다.**
4. **커뮤니티 큐레이션이라 `basis=inferred`**(`cyclenerd-gcp-catalog`)입니다. 원문이
   Google 문서 URL을 달아 두어 사람이 확인할 수 있지만 옮긴 것은 커뮤니티입니다 —
   29번(문서 표를 직접 파싱, stated)과 등급이 다르다는 것이 두 라벨을 가른 이유입니다.

### 처리 후 형태

`data/tumblebug-perf.json.gz`의 gcp 레코드에 병합 — `cpuPlatform`·`family`·
`networkBandwidthMbps`·GPU·로컬 SSD가 **98% 채워집니다**(evidence
`cyclenerd-gcp-catalog`, basis `inferred`).

> **왜 필요했나.** perfkb의 gcp 11,622건은 **`sustainedCpu` 하나뿐**이었습니다 —
> 세 프로바이더 중 최악의 공백. 기계 판독 가능한 무인증 공식 카탈로그가 없어서입니다
> (Billing Catalog API는 인증 필요, 문서는 사이트 렌더링뿐).

---

## 31. `ibm-global-catalog` — IBM 성능 신호

`https://globalcatalog.cloud.ibm.com/api/v1?q=is.instance`
· **지문(버전 없음)** · 라이선스 `not-stated` · 재배포 `not-stated`(NOTICE에 공시)

### 원본 형태

무인증 200으로 프로필 **312개**가 옵니다.

```json
{"_id": "bx2-128x512", "active": true,
 "catalog_crn": "crn:v1:bluemix:public:globalcatalog::::instance.profile:bx2-128x512",
 "geo_tags": ["au-syd","br-sao","ca-tor","eu-de","eu-es","eu-gb","in-che","in-mum",
              "jp-osa","jp-tok","us-east","us-south"],
 "id": "bx2-128x512", "kind": "instance.profile",
 "metadata": {"other": {
    "certifications": ["SAP_NETWEAVER"],
    "geography": {"regions": [{"name": "au-syd", "zones": [{"universal_name": "au-syd-syd01-a"}, …]}, …]},
    "default_config": {"bandwidth": …, "port_speed": …, "max_nics": …,
                       "freqency": 2000,          ← 원본 철자 그대로. 310건 전부 같은 값
                       "vcpu_manufacturer": "Intel", "family": "balanced",
                       "vcpu_tenancy": "dedicated", …}}}}
```

**페이지네이션 함정**: `limit`은 무시되고 `_limit`/`_offset`을 써야 합니다 — 첫 페이지만
보면 커버리지가 14%로 보입니다.

### 처리

`perfkb/parsers/ibm_catalog.py`

1. **담을 것을 채움률이 아니라 변별력으로 정했습니다.** `default_config`에 칸이 30개
   가까이 있지만 **값이 하나뿐인 칸은 담지 않습니다.**

   ```
   freqency        310건 100%  값 1가지(2000)      ← 안 담는다
   status          287건  93%  값 1가지(current)
   vcpu_architecture / os_architecture / reservation_terms / iothreads … 전부 1가지

   bandwidth        98.1%  18가지     port_speed      92.6%   3가지
   max_nics         92.6%   5가지     numa_count      62.6%   2가지
   vcpu_manufacturer 82.9%  2가지     cpu_family      52.3%   3가지
   vcpu_tenancy     92.6%   3가지     provisioning_timeout_seconds 92.6% 7가지
   ```

   `freqency`를 담았으면 **모든 IBM 스펙이 "2.0 GHz"로 나왔을 것**이고, 실제 클럭은
   세대마다 다른데 우리는 그걸 모릅니다.
2. **`sustainedCpu`를 만들어 내지 않습니다.** `vcpu_tenancy`가 `dedicated`인 걸 보고
   "상시 CPU 보장"이라고 적고 싶어지지만 **그건 우리 추론이지 원본이 한 말이
   아닙니다.** `vcpuTenancy`를 그대로 담고, 조회 계층이 **"레코드는 있으나 버스트·세대
   신호가 없다"**를 별도 상태로 답합니다.
3. **교차 확인**: 미러의 IBM 287종과 **287/287 조인**되고 cpu·ram이 287건 전부
   일치합니다 — 두 독립 소스가 같은 값을 말합니다.

### 처리 후 형태

`data/ibm-perf.json.gz` — `specs` **2,002건** (프로필 312 × 리전)

```json
{"id": "ibm+au-syd+bx2-128x512", "provider": "ibm", "specName": "bx2-128x512",
 "networkBandwidthMbps": 80000, "portSpeedMbps": 25000, "maxNics": 15,
 "provisioningTimeoutSeconds": 600, "cpuVendor": "Intel",
 "family": "balanced", "vcpuTenancy": "dedicated"}
```

```json
"_coverage": [{"profiles": 312, "joined": 287, "records": 2002,
  "mirrorAgreed": 287, "mirrorDisagreed": 0, "unmatched": 23,
  "droppedConstantFields": ["freqency", "status", "vcpu_architecture", "os_architecture",
                            "reservation_terms", "network_bandwidth_mode", "iothreads"]}]
```

**버린 칸의 이름을 산출물에 적습니다** — 다음 사람이 "왜 클럭이 없냐"고 물었을 때
답이 데이터 안에 있어야 합니다.

> **왜 IBM만인가.** perfkb는 aws·azure·gcp 셋뿐이었고 나머지 일곱(스펙 8,051건)은
> **0건**이었습니다. 조사에서 그 일곱 중 **공개 성능 소스가 실재하는 것은 IBM뿐**으로
> 확인됐습니다 — Terraform provider는 타입 축만 주고 스펙 카탈로그를 주지 않습니다.
> 나머지는 "부재 확정"으로 기록했습니다.

---

# F. 환경·수명 (3종)

## 32. `gcp-carbon` — GCP 지역별 무탄소 에너지 비율

`…/GoogleCloudPlatform/region-carbon-info/49f3f26bfd68/data/yearly/2024.csv`
· 커밋 `49f3f26bfd68` · **Apache-2.0**

### 원본 형태

네 칸짜리 CSV입니다.

```csv
Google Cloud Region,Location,Google CFE,Grid carbon intensity (gCO2eq / kWh)
africa-south1,Johannesburg,0.15,656.85
asia-east1,Taiwan,0.17,439.29
asia-east2,Hong Kong,0.01,505.02
asia-northeast1,Tokyo,0.17,452.85
asia-northeast2,Osaka,0.46,296.19
asia-northeast3,Seoul,0.37,356.57
asia-south1,Mumbai,0.09,678.76
```

### 처리

`envkb/carbon.py`

1. 단위가 이미 g/kWh라 **그대로** 씁니다. **구글이 직접 발표한 값이라 `stated`**이고
   `method: "provider-published"`가 붙습니다.
2. **연도를 고정합니다(2024)** — 연도별 파일이 있는데 최신 연도를 따라가면 **값이 조용히
   바뀝니다.**
3. 우리 gcp 리전 43개 중 **42개(98%)**와 조인됩니다.

### 처리 후 형태

`data/region-carbon.json.gz`의 gcp 부분 — **44건**

```json
{"provider": "gcp", "region": "africa-south1", "gramsPerKWh": 656.85,
 "carbonFreeEnergy": 0.15, "location": "Johannesburg",
 "method": "provider-published", "source": "gcp-carbon"}
```

---

## 33. `ccf-emissions` — AWS·Azure 지역별 배출계수

`…/cloud-carbon-footprint/cloud-carbon-footprint/f584c549ee35/packages/`
· 커밋 `f584c549ee35` · **Apache-2.0** · 파일 **5개**를 함께 읽습니다

### 원본 형태 — 참조를 풀어야 값이 나옵니다

TypeScript 상수입니다. **계수 표가 다른 파일의 상수를 참조**합니다.

`ccf-aws_regions.ts` — 리전 enum

```typescript
export enum AWS_REGIONS {
  US_EAST_1 = 'us-east-1',
  AP_NORTHEAST_2 = 'ap-northeast-2',
  …
}
```

`ccf-core.ts` — 미국 NERC 지역 상수

```typescript
export const US_NERC_REGIONS_EMISSIONS_FACTORS: {[nercRegion: string]: number} = {
  RFC:  0.0003761283186,
  SERC: 0.0003651277965,
  WECC: 0.0002986502059,
  TRE:  0.0003341569631,
  MRO:  0.0003909916334,
}
```

`ccf-aws_factors.ts` — 계수 표 (**둘을 다 봐야 풀립니다**)

```typescript
AWS_EMISSIONS_FACTORS_METRIC_TON_PER_KWH: CloudConstantsEmissionsFactors = {
  [AWS_REGIONS.US_EAST_1]: US_NERC_REGIONS_EMISSIONS_FACTORS.SERC,   ← 참조
  [AWS_REGIONS.US_WEST_2]: US_NERC_REGIONS_EMISSIONS_FACTORS.WECC,
  [AWS_REGIONS.AF_SOUTH_1]: 0.00075744,                              ← 직접 값
  [AWS_REGIONS.AP_EAST_1]:  0.00067348,
  [AWS_REGIONS.AP_SOUTH_1]: 0.00095182,
  …
}
```

### 처리

`envkb/carbon.py`

1. 세 파일(+azure 2개)을 정규식으로 읽고 **참조를 풉니다.** 못 풀면 그 리전은 **담지
   않고 셉니다.** 실측으로 AWS는 39개 전부 풀렸습니다.
2. **단위를 맞춥니다** — CCF는 메트릭톤/kWh, GCP는 g/kWh입니다. 산출물은 g/kWh로
   맞추고(×1,000,000) 원본 단위는 `_note`에 남깁니다.
3. `method: "grid-estimate"`를 붙입니다.

> ⚠ **32번과 같은 축에 놓고 비교하면 안 됩니다.** 이쪽은 공개 그리드 데이터 추정이고
> 32번은 구글 자체 발표라 **방법론이 다릅니다.** 실측이 이걸 분명히 보여줍니다
> (gCO2eq/kWh):
>
> ```
> 서울   aws 477.4 · gcp 356.6 · azure 415.6    ← gcp가 최저
> 도쿄   aws 439.8 · gcp 452.9 · azure 465.8    ← aws가 최저
> ```
>
> **같은 도시, 같은 전력망인데 값이 다르고 순서까지 뒤집힙니다.** 그래서 이 축은
> **프로바이더 안에서만 비교**하고, 레코드마다 `method`를 남겨 어느 방법론인지 밝힙니다.

### 처리 후 형태

`data/region-carbon.json.gz` — `regions` 161건 (gcp 44 + ccf **117**)

```json
{"provider": "aws", "region": "us-east-1", "gramsPerKWh": 365.128,
 "carbonFreeEnergy": null, "location": null,
 "method": "grid-estimate", "source": "ccf-emissions"}
```

`carbonFreeEnergy`가 `null`인 것이 중요합니다 — CFE 비율은 **구글만 발표**하므로
AWS·Azure 쪽은 **빈칸이지 0이 아닙니다.**

---

## 34. `endoflife-date` — 지원 종료일

`…/endoflife-date/endoflife.date/2ffcafdaa788/products/` · 커밋 `2ffcafda…` · **MIT**
(산문 부분 CC BY-SA 3.0은 배제) · 클라우드 제품 md **17개** 캐시

### 원본 형태

**YAML frontmatter + 마크다운 산문**입니다.

```markdown
---
title: Amazon EKS
addedAt: 2021-07-25
category: service
tags: amazon managed-kubernetes
permalink: /amazon-eks
releasePolicyLink: https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html
eolColumn: End of Support
eoesColumn: true

auto:
  methods:
    - amazon-eks: https://web.archive.org/web/…/platform-versions.html # 1.19
    …
releases:
  - releaseCycle: "1.36"
    releaseDate: 2026-06-02
    eol: 2027-08-02
    eoes: 2028-08-02
    latest: "1.36-eks-6"
  …
---

(여기서부터 산문 — Wikipedia에서 각색, CC BY-SA 3.0)
```

### 처리

`envkb/lifecycle.py`

1. **`---` 사이의 frontmatter에서 `releases:`만 읽고 뒤의 산문은 버립니다.**
   저장소는 MIT이지만 README가 본문 설명을 *"adapted from Wikipedia, CC BY-SA 3.0"*
   이라고 밝힙니다. **파서가 본문을 아예 읽지 않는 것이 의도적인 경계**이고, 그래야
   우리가 담는 날짜·버전 문자열이 MIT 범위 안에 남습니다.
2. **날짜 필드가 여럿입니다.**
   ```
   eoas  active support 종료   (버그 수정이 끝나는 날)
   eol   지원 종료             (보안 패치도 끝나는 날 — 이게 기준선)
   eoes  extended support 종료 (돈을 더 내면 연장되는 날)
   ```
   `eoes`가 있으면 "연장 지원은 여기까지"로 함께 밝힙니다 — 연장이 있는데 없다고 하면
   **아직 쓸 수 있는 버전을 못 쓴다**고 답하게 됩니다.
3. **`false`가 올 수 있습니다.** endoflife.date는 "종료일이 아직 안 정해졌다"를 `false`로
   적습니다. 날짜로 읽으면 안 되고, **"종료 예정일 미정"과 "이미 종료"는 다른 답**입니다.
4. **타입 매핑은 손으로 합니다.** 제품 이름(`amazon-eks`)과 우리 타입 id
   (`aws::AWS::EKS::Cluster`)는 규칙으로 못 잇습니다. 15개 안팎이라 손으로 적되
   **그 타입이 실재하는지는 빌드가 graphkb 노드와 대조**합니다 — 손매핑은 틀리면 엉뚱한
   타입의 수명을 답하게 되므로 검증 없이 두지 않습니다.

### 처리 후 형태

`data/service-lifecycle.json.gz` — `products` **17건**(소스에는 클라우드 제품 23종) ·
`unmapped_types` 0

```json
{"product": "amazon-eks", "title": "Amazon EKS", "type_id": "aws::AWS::EKS::Cluster",
 "releases": [
   {"cycle": "1.36", "released": "2026-06-02", "eol": "2027-08-02",
    "eoas": null, "eoes": "2028-08-02", "latest": "1.36-eks-6"},
   {"cycle": "1.33", "released": "2025-05-28", "eol": "2026-07-29",
    "eoas": null, "eoes": "2027-07-29", "latest": "1.33-eks-41"}, …]}
```

`_note`: *"Only `releases` from the YAML frontmatter is kept — the prose body carries a
different license (CC BY-SA) and is not read. A null eol is not 'already ended', it is
**end date undetermined**."*

---

# G. 문서에서 **표만** 뽑은 것 (2종)

원칙("사람이 읽는 문서를 긁지 않는다")의 예외입니다. 산문이 아니라 표의 칸만 읽습니다.
(세 번째 예외인 29번은 E에 있습니다.)

## 35. `azure-limits-doc` — Azure 한도 문서

`…/MicrosoftDocs/azure-docs/355bbdc30800…/includes` · 커밋 `355bbdc3…` · **CC-BY-4.0**
· `*-limits.md` **95개**를 읽습니다

### 원본 형태

```markdown
---
 title: include file
 description: include file
 ms.date: 10/16/2025
---
### <a name="azure-resource-manager-virtual-networking-limits"></a>Networking limits - Azure Resource Manager
The following limits apply only for networking resources managed through
**Azure Resource Manager** per region per subscription. …

> [!NOTE]
> We have increased all default limits to their maximum limits. …

| Resource | Limit |
| --- | --- |
| Virtual networks |1,000 |
| Subnets per virtual network |3,000 |
| Virtual network peerings per virtual network |500 |
| [Virtual network gateways (VPN gateways) per virtual network](../articles/…#benchmark) |1 |
```

표가 **두 형태**입니다:

```
| Resource | Limit |                        → 값 하나 (default)
| Resource | Default limit | Maximum limit |  → 기본값/최대값 분리
```

### 처리

`capacitykb/parsers/azure_quota.py`

1. **세 클라우드 중 Azure만 자격증명 없이 기계 판독이 가능합니다.** AWS Service Quotas
   API는 자격증명이 필요하고(대안 awslimitchecker는 AGPL + 2021년 이후 정체),
   GCP는 문서 저장소 자체가 비공개(HTML만)입니다.
2. **실측 함정을 전부 처리합니다** — 천단위 콤마(`1,000`), 각주(`<sup>1</sup>`),
   셀 안의 마크다운 링크, 비수치 값(`Contact support`, `/28`,
   `256 * N (N is number of NICs on VM)`, `500,000, up to 1,000,000 for two or more NICs.`).
3. **라벨이 갈리면 라벨을 쪼갭니다.** 표의 숫자는 `azure-limits-doc`(stated),
   각주·`varies` 같은 비수치 표현은 `azure-limits-note`(**inferred**)로 나눕니다 —
   한 evidence 라벨은 성격이 하나여야 한다는 규칙입니다.
4. **타입 연결은 사람이 검수한 표(`azure_quota_types.json`)에 있을 때만** 합니다.
   나머지는 이름 검색으로만 찾습니다.
5. **타임스탬프는 기록하지 않습니다** — 산출물을 재현 가능하게 유지하기 위함입니다
   (신선도는 `--refresh`로 관리).

### 처리 후 형태

`data/azure-quota.json.gz` — `quotas` **542건**
(`azure-limits-doc` **321** + `azure-limits-note` **221**)

```json
{"provider": "azure", "name": "Frontend IP configurations", "scope": null,
 "default": 4, "maximum": null, "unit": null, "type_id": null,
 "source_doc": "application-gateway-limits.md",
 "evidence": "azure-limits-doc", "basis": "stated"}
```

```json
{"provider": "azure", "name": "Configuration store requests for Free tier",
 "default": "1,000 requests per day",              ← 숫자로 접히지 않는 값
 "source_doc": "app-configuration-limits.md",
 "evidence": "azure-limits-note", "basis": "inferred"}
```

`_coverage`: *"**quotas are included for Azure only** — AWS Service Quotas and GCP Cloud
Quotas both need credentials, so this build cannot fetch them."*

---

## 36. `ms-architecture-center` — 서비스 비교표

`…/MicrosoftDocs/architecture-center/11c3681605cf…` · 커밋 `11c36816…` · **CC-BY-4.0**
· `docs/aws-professional/*.md` + `docs/gcp-professional/*.md` (+ 패턴 산문은 42번 항목)

### 원본 형태

```markdown
## Compare AWS and Azure compute services

### Virtual machines and servers

| AWS service | Azure service | Description |
| --- | --- | --- |
| [Amazon Lightsail](https://aws.amazon.com/lightsail/) | [Azure App Service](/azure/app-service/overview), [Azure Virtual Machines](…) (B-series) | Amazon Lightsail provides simplified, predictably priced VMs … |
| [Amazon EC2](https://aws.amazon.com/ec2/) | [Azure Virtual Machines](…) | … |
```

### 처리

`graphkb/parsers/svcmap.py` — 46번(`mingrammer-diagrams`)과 **둘이 한 산출물**을 만듭니다.

1. 표의 각 행에서 **서비스 이름 수준의 대응**을 뽑습니다('Amazon RDS ↔ Azure SQL').
2. **그걸 타입 id(`AWS::RDS::DBInstance`)에 붙이는 것은 우리 손 검수입니다.**
   그래서 basis가 `stated`가 아니라 **`inferred`(검수됨)**입니다 — 다리를 건너면 등급이
   떨어진다는 규칙 그대로입니다.
3. **어떤 독립 근거가 뒤에 있는가**로 라벨이 갈립니다.
   ```
   MS 표 + diagrams 둘 다  →  svcmap-cross-checked   31건
   MS 표만                 →  ms-learn-comparison     5건
   diagrams만              →  mingrammer-taxonomy    17건
   손 검수만               →  svcmap-reviewed        16건
   ```
4. **문서라 표가 재편됩니다.** `aws-professional/services.md` 하나였던 것이 카테고리별
   파일 6개로 쪼개져 **404를 직접 겪었습니다.** 지금은 파서가 행 수를 세어 급감하면
   알립니다.

### 처리 후 형태

`data/svcmap-graph.json.gz` — `nodes` 81(앱 개념 13종 + 벤더 타입) · `edges` **69**

```json
{"id": "app::relationalDatabase", "layer": "app", "provider": "app",
 "kind": "app_concept", "display_name": "relational database", "source": "svcmap"}

{"from": "app::relationalDatabase", "to": "aws::AWS::RDS::DBInstance",
 "type": "equivalent_to", "via_property": "", "required": false, "cardinality": "one",
 "evidence": "svcmap-cross-checked", "basis": "inferred", "reviewed": true}
```

`_coverage`에 **원문 표의 행을 그대로 실어** 사람이 대조할 수 있게 합니다:

```json
{"concept": "app::relationalDatabase", "display": "relational database",
 "bindings": [{"provider": "aws", "typeId": "aws::AWS::RDS::DBInstance",
   "evidence": "svcmap-cross-checked",
   "msRow": "| Relational database | [Amazon RDS](https://aws.amazon.com/rds) |
             [Azure SQL Database](…)<br/><br/>[Azure Database for MySQL](…) …"}]}
```

> **왜 필요했나.** core 층 13개는 전부 인프라(vm·vNet·subnet…)라 **앱 설계도가 말하는
> 것들 — DB·큐·캐시·객체 스토리지 — 에 대응이 없었습니다.**
>
> **담지 않은 것도 적어 둡니다**: tencent(MS 표에도 diagrams에도 없음),
> GCP CDN(Cloud CDN은 백엔드 서비스에 붙는 **플래그**라 1:1 타입이 없음 — 억지로
> `ComputeBackendBucket`을 대면 "CDN을 만들었다"로 읽힘).
>
> **실행 경계**: 이 대응은 **안내이지 배포 가능이 아닙니다.** cb-tumblebug 실행 경로는
> VM·k8s까지라 관리형 서비스를 만들지 못하고, `agent_api.equivalent_types`가 app:: 층을
> 지나면 그 사실을 답에 붙입니다.

---

# H. 실제 배포 사례 뭉치 — "세어 본 것"(observed) (5종)

여기서 나오는 값은 전부 **`observed`**입니다. 뜻은 **"이 사례 뭉치에서 그렇게 나왔다"**
이지 **"클라우드가 그렇게 강제한다"가 아닙니다.**

> **판정에는 안 씁니다** — 100%가 "없으면 안 된다"를 증명하지 못합니다. 대신 100% 미만은
> 아예 안 담습니다("자주 함께"는 지식이 아니라 인상이라서). **표본 수를 반드시 함께
> 담습니다** — 템플릿이 17개뿐인 앵커의 35.3%는 6건입니다.

## 37. `azure-quickstart-templates` — ARM 템플릿 1,152개

`https://codeload.github.com/Azure/azure-quickstart-templates/tar.gz/331d6f394416…`
· 커밋 `331d6f39…` · **MIT** · tarball **326 MB**(빌드 때만 받고 산출물엔 파생 표만)

### 원본 형태

`quickstarts/<네임스페이스>/<시나리오>/azuredeploy.json`:

```json
"resources": [
  {"type": "Microsoft.Network/networkInterfaces",
   "apiVersion": "2023-09-01",
   "name": "[variables('networkInterfaceName')]",
   "properties": {"ipConfigurations": [{"properties": {
      "subnet": {"id": "[reference(resourceId('Microsoft.Network/virtualNetworks', …)).subnets[0].id]"},
      "publicIPAddress": {"id": "[resourceId('Microsoft.Network/publicIPAddresses', …)]"}}}],
      "networkSecurityGroup": {"id": "[resourceId('Microsoft.Network/networkSecurityGroups', …)]"}},
   "dependsOn": ["[resourceId('Microsoft.Network/networkSecurityGroups', …)]",
                 "[resourceId('Microsoft.Network/publicIPAddresses', …)]", …]},
  {"type": "Microsoft.Compute/virtualMachines", …},
  …]
```

### 처리

`bundlekb/parsers/aqt.py` — **각 템플릿의 `type` 집합만** 뽑아 동시 출현을 셉니다.

1. 앵커 타입 T가 있는 템플릿을 모수로 두고, 그 안에 함께 나온 타입의 비율을 냅니다.
2. **`MIN_SAMPLES`(20) 아래 앵커는 아예 안 담습니다.** 타입 530종 중 앵커 43개만
   남고 487개가 빠집니다.
3. **표본 편향은 데이터가 아니라 고지로 다룹니다.** Quickstart는 데모·튜토리얼 쪽으로
   기웁니다 — VM과 스토리지 계정이 53.6%로 같이 나오는 것은 **옛 부트 진단 관행의
   흔적**입니다. **값을 손보지 않고 `_coverage`에 적습니다** — 보정하면 그게 짐작이
   됩니다.

> **왜 이 축이 필요한가.** graphkb는 스키마의 참조를 따라가므로 "가능한 것"은 다 주지만
> **"실제로 필요한 것"을 못 가릅니다**(`EC2::Instance`에서 `KMS::ReplicaKey`까지 이어
> 줍니다). 실측이 판별자를 줬습니다:
>
> ```
> VM이 있는 템플릿 330개 중
>   100.0%  networkInterfaces      ← 사실상 필수
>    92.4%  virtualNetworks
>    72.4%  networkSecurityGroups  ← 강한 관행
>     5.8%  routeTables            ← 드묾
>     5.5%  bastionHosts
> ```
>
> **분포에 큰 골이 있습니다.** 100%·92% 무리와 5~7% 꼬리가 뚜렷이 갈립니다.

### 처리 후 형태

`data/aqt-cooccurrence.json.gz` — `cooccurrence` **1,253건**

```json
{"anchor": "azure::Microsoft.Authorization/roleAssignments",
 "typeId": "azure::Microsoft.Storage/storageAccounts",
 "hits": 50, "samples": 89, "evidence": "aqt-corpus"}
```

**비율이 아니라 `hits`/`samples`를 담습니다** — 비율만 담으면 숫자에 없는 확신을 줍니다.

---

## 38·39. `aws-cfn-templates` · `widdix-cf-templates` — AWS 코퍼스 둘

| 키 | URL | 핀 | 라이선스 | 템플릿 |
|---|---|---|---|---:|
| `aws-cfn-templates` | `codeload…/aws-cloudformation/aws-cloudformation-templates/tar.gz/a0f43bc6…` | 커밋 `a0f43bc6…` | Apache-2.0 | 299 |
| `widdix-cf-templates` | `codeload…/widdix/aws-cf-templates/tar.gz/1a9f04f9…` | 커밋 `1a9f04f9…` | Apache-2.0 | 63 |

### 원본 형태

CloudFormation YAML입니다 (`EC2/EC2InstanceWithSecurityGroupSample.yaml`):

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: 'AWS CloudFormation Sample Template EC2InstanceWithSecurityGroupSample: …'
Metadata:
  License: Apache-2.0
Parameters:
  InstanceType:
    Type: String
    AllowedValues: [t2.nano, t2.micro, …, m4.large]
Resources:
  EC2Instance:
    Type: AWS::EC2::Instance          ← 필요한 것은 이 한 줄뿐
    Properties: {…}
  InstanceSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    …
```

### 처리

`bundlekb/parsers/awscfn.py` — 37번과 **같은 방법을 AWS에 적용**합니다.

1. **YAML을 통째로 파싱하지 않습니다.** CFN YAML은 `!Ref`·`!GetAtt` 같은 커스텀 태그
   때문에 표준 파서로 못 읽습니다. 필요한 것은 `Type: AWS::X::Y` 한 줄뿐이라 **그 모양만
   집습니다** — perfkb가 Go의 `%v` 포맷에 쓴 것과 같은 방식입니다.
2. **코퍼스가 둘인 이유를 밝힙니다.** AWS 공식 샘플 하나로는 템플릿 299개·앵커
   22종뿐이었습니다(Azure는 1,152개·43종). widdix를 더해 **362개·30종**이 됩니다.
3. **성격이 다른 둘을 섞는다는 사실을 적습니다** — AWS 샘플은 서비스별 데모이고
   widdix는 운영용 스택이라 후자에서 CloudWatch 경보가 55.6%로 나옵니다. 편향의 방향이
   다르므로 섞으면 어느 쪽 편향인지 알 수 없습니다. 그래서 `_coverage`에 둘의 규모를
   **따로** 적습니다.
4. **RDS(15건)·EKS(2건)는 그래도 임계에 못 미칩니다.** 채운 척하지 않고 구멍으로
   남깁니다 — `MIN_SAMPLES`를 낮춰 맞추면 그 문턱을 둔 이유가 사라집니다.

> **통했지만 다르게 통했습니다.**
> ```
> AWS::Lambda::Function  → AWS::IAM::Role         100.0% (38/38)   구조적 필수
> AWS::EC2::Instance     → AWS::EC2::SecurityGroup  90.2%
>                          AWS::EC2::Subnet         78.0%
>                          AWS::EC2::KeyPair        75.6%
> ```
> Lambda는 실행 역할이 **없으면 안 되므로** 100%가 나옵니다. EC2는 기본 VPC·기본 SG를
> 쓸 수 있어서 100%가 안 나옵니다 — **분포가 "구조적 필수"와 "관행"을 갈라 보여줍니다.**
> 값을 손보면 그 정보가 사라지므로 그대로 담습니다.

### 처리 후 형태

`data/awscfn-cooccurrence.json.gz` — `cooccurrence` **1,147건** · 템플릿 362개

```json
{"anchor": "aws::AWS::AutoScaling::AutoScalingGroup",
 "typeId": "aws::AWS::EC2::SecurityGroup",
 "hits": 30, "samples": 30, "evidence": "awscfn-corpus"}
```

```json
"_coverage": [{"templates": 362,
  "note": "Co-occurrence counted over 362 CFN templates
    ({'aws-cfn-templates': 299, 'widdix-cf-templates': 63}). Of 182 types, only the 30
    anchors with 20 or more samples are included. **Still thinner than the Azure corpus
    (1,152 templates · 43 anchors)** — ratios alone make the two corpora read as equally
    weighty, so the difference is recorded here."}]
```

---

## 40. `aws-solutions-constructs` — AWS 공식 패턴

`https://codeload.github.com/awslabs/aws-solutions-constructs/tar.gz/refs/tags/v2.103.0`
· 태그 `v2.103.0` · **Apache-2.0** · tarball 40.8 MB

### 원본 형태

**디렉터리 이름 자체가 데이터입니다.** 코드를 파싱하지 않아도 신호가 나옵니다.

```
source/patterns/@aws-solutions-constructs/
    aws-alb-fargate/          aws-alb-lambda/
    aws-apigateway-dynamodb/  aws-apigateway-iot/
    aws-apigateway-kinesisstreams/  aws-apigateway-lambda/
    aws-apigatewayv2websocket-sqs/  aws-cloudfront-apigateway/
    aws-cloudfront-apigateway-lambda/  aws-cloudfront-oai-s3/
    aws-cloudfront-s3/  aws-cognito-apigateway-lambda/  … 81개
```

### 처리

`bundlekb/parsers/aws_patterns.py`

1. 이름을 `-`로 쪼개 서비스 낱말을 얻습니다. 실측: 패턴 **83개** · 등장 서비스 41종
   (lambda 36 · s3 13 · fargate 12 · apigateway 11).
2. **조합은 AWS가 말했고, 타입 매핑은 우리가 했습니다.** 이름은 **서비스**를 말하지
   리소스 **타입**을 말하지 않습니다. `lambda`가 `AWS::Lambda::Function`인 것은
   분명하지만 `fargate`가 무엇인지는 갈립니다(ECS 서비스? 태스크 정의? 클러스터?).
3. **모호하지 않은 것만 매핑하고 나머지는 담지 않습니다.** 안 담은 서비스는 개수와
   이유를 `_coverage`에 적습니다 — 조용히 빠뜨리지 않습니다.

> **왜 이 소스인가.** 번들의 나머지 소스는 전부 Azure 쪽입니다(AVM·Quickstart). AWS에는
> 그만한 **선언적** 번들 카탈로그가 없습니다 — CDK는 TypeScript, SAM은 파이썬 변환
> 규칙이라 둘 다 코드입니다. Solutions Constructs는 **패턴 이름 자체가 조합**입니다.

### 처리 후 형태

`data/aws-pattern-bundles.json.gz` — `bundles` **52건** (83개 중)

```json
{"id": "awscon::aws-alb-lambda", "name": "aws-alb-lambda", "provider": "aws",
 "evidence": "aws-solutions-construct",
 "description": "AWS Solutions Constructs pattern — alb + lambda",
 "caveat": "AWS officially grouped the combination, and **mapping the service names to
   resource types is ours**. Attachments the pattern actually creates, such as IAM roles
   and log groups, are not included.",
 "members": [{"typeId": "aws::AWS::ElasticLoadBalancingV2::LoadBalancer",
              "tier": "always", "note": "'alb' in the pattern name"},
             {"typeId": "aws::AWS::Lambda::Function",
              "tier": "always", "note": "'lambda' in the pattern name"}]}
```

`_coverage`에 **못 붙인 이름을 개수까지** 적습니다: `fargate`(12) · `dynamodbstreams`(3) ·
`elasticsearch`(2) · `kibana`(2) · `pipes`(2) · `route53`(2) ·
`apigatewayv2websocket`(1) · `oai`(1).

---

## 41. `avm-bicep` — Azure Verified Modules

`https://codeload.github.com/Azure/bicep-registry-modules/tar.gz/b7c2b1a25b33…`
· 커밋 `b7c2b1a2…` · **MIT** · tarball 11.4 MB

**저장소 전체 태그가 없습니다** — 태그가 모듈별 semver(`storage/storage-account/3.0.1`)라
저장소 상태를 가리키지 못합니다. 그래서 커밋 SHA로 고정합니다.

### 원본 형태

컴파일된 ARM 템플릿 `avm/{res,ptn}/<그룹>/<모듈>/main.json` **522개**:

```json
"storageAccount": {
  "type": "Microsoft.Storage/storageAccounts",
  "properties": {"…": "[union(createObject('encryption', …), …)]"},
  "dependsOn": ["cMKKeyVault", "cMKKeyVault::cMKKey"]           ← 배포 순서
},
"storageAccount_diagnosticSettings": {
  "copy": {"name": "storageAccount_diagnosticSettings",
           "count": "[length(coalesce(parameters('diagnosticSettings'), createArray()))]"},
                                                                 ← 빈 배열 폴백 = 선택
  "type": "Microsoft.Insights/diagnosticSettings",
  "apiVersion": "2021-05-01-preview",
  "scope": "[resourceId('Microsoft.Storage/storageAccounts', parameters('name'))]",
  …}
```

### 처리 — **두 파서가 다른 축을 봅니다**

**(a) 배포 순서** (`graphkb/parsers/avm.py`) — `dependsOn`에서 타입 쌍을 뽑습니다.

성격이 다릅니다. 우리 Azure 그래프는 이름의 계층(`arm-hierarchy` 2,223)과 스키마의
참조(`bicep-ref` 248)로 되어 있는데 둘 다 "구조가 그렇다"는 사실입니다. AVM은
**실제로 배포할 때 무엇을 먼저 만드는가**를 줍니다. 실측(storage-account 모듈 하나):
타입 쌍 6개 중 **5개가 우리 그래프에 없는 관계**였습니다.

```
Microsoft.Insights/diagnosticSettings   → Microsoft.Storage/storageAccounts
Microsoft.Authorization/roleAssignments → Microsoft.Storage/storageAccounts
Microsoft.Storage/storageAccounts       → Microsoft.KeyVault/vaults
```

마지막 것이 이 소스의 성격을 잘 보여줍니다 — 스토리지 계정이 KeyVault를 **스키마상**
요구하지는 않습니다. 고객 관리 키를 쓸 때만 필요하고, AVM은 그 **실무 구성**을 담고
있습니다.

**걸러내는 것**: `Microsoft.Resources/deployments`(AVM이 사용량 집계용으로 넣는
텔레메트리 배포 — 모든 모듈에 있어서 담으면 **모든 타입이 여기 의존하는 가짜 허브**가
생깁니다) · 자기 자신을 가리키는 쌍 · 우리 Azure 색인에 없는 타입(담지 않고 셉니다).

**(b) 모듈이 무엇을 배포하는가** (`bundlekb/parsers/avm.py`) — **판별자를 두 번 틀리고
세 번째에 확정했습니다.**

```
condition 있음                      → 선택 (파라미터에 따라)
copy.count에 coalesce/createArray   → 선택 (빈 배열 폴백이 있다)
copy.count에 폴백 없음              → **필수** (값을 반드시 줘야 한다)
둘 다 없음                          → 무조건
```

- **1차 실패** — `condition`만 봤더니 패턴 하나가 104종을 "항상 배포"한다고 나왔고,
  한 Cosmos 계정에 Cassandra·Gremlin·Mongo·SQL이 **동시에** 들어 있었습니다.
  `copy`(파라미터 배열 루프)를 안 센 탓입니다.
- **2차 실패** — `defaultValue` 부재를 '필수'로 읽었더니
  `Insights/diagnosticSettings`가 **173개 중 89개 모듈의 필수 동반자**로 나왔습니다.
  AVM은 Bicep의 nullable 파라미터를 `defaultValue` 없이 컴파일하고 사용처에서
  `coalesce(x, createArray())`로 처리합니다 — 기본값이 파라미터 선언이 아니라
  **본문**에 있습니다.
- **중첩 배포는 재귀로 풀어야 합니다.** VM의 NIC은
  `deployments → deployments → networkInterfaces`로 **두 단** 중첩입니다. 한 단만 보면
  VM의 **유일한 진짜 필수 동반자를 통째로 잃고** "VM은 아무것도 필요 없다"는 사실과
  정반대인 KB가 됩니다.

### 처리 후 형태

`data/azure-deploy-graph.json.gz` — `nodes` 175 · `edges` **421**

```json
{"from": "azure::microsoft.insights/diagnosticSettings",
 "to": "azure::Microsoft.AAD/domainServices", "type": "references",
 "via_property": "", "required": false, "cardinality": "one",
 "evidence": "avm-dependson", "basis": "stated"}
```

`data/avm-bundles.json.gz` — `bundles` **207건** (res 169 · ptn 38)

```json
{"id": "avm::ptn/aca-lza/hosting-environment", "name": "avm/ptn/aca-lza/hosting-environment",
 "provider": "azure", "evidence": "avm-module",
 "description": "This Azure Container Apps pattern module represents an Azure Container
   Apps deployment aligned with the cloud adoption framework",
 "members": [{"typeId": "azure::Microsoft.App/managedEnvironments", "tier": "always"},
             {"typeId": "azure::Microsoft.ContainerRegistry/registries", "tier": "always"},
             {"typeId": "azure::Microsoft.KeyVault/vaults", "tier": "always"},
             {"typeId": "azure::Microsoft.ManagedIdentity/userAssignedIdentities", "tier": "always"}, …]}
```

> **이것은 모듈 저자의 설계이지 API의 강제가 아닙니다.** `avm-dependson`은 **"AVM 모듈이
> 이 순서로 배포한다"**이지 **"API가 이 순서를 강제한다"**가 아닙니다 — `tpg-schema`의
> ForceNew와 같은 구분입니다. 다만 검증되는 두 사례
> (virtual-machine → networkInterfaces, virtual-network-gateway → publicIPAddresses)는
> 클라우드 사실과도 일치합니다. **일치를 확인한 것과 일치한다고 가정하는 것은 다릅니다.**

---

# I. 설계 지침 산문 — **자문 전용** (4종)

수치로 환원되지 않는 지식입니다. 사실 축에 넣으면 **지침이 사실 행세**를 하므로 축을
아예 분리했습니다. 넷 다 evidence가 `pattern-advisory`(basis **inferred**)이고,
**검수해도 클라우드 사실이 되지 않는 성격이라 `reviewed`를 붙이지 않는 것이 규약**입니다.

**검색은 벡터(임베딩)가 아니라 단어 일치(FTS5)입니다.** 임베딩은 두 번 검토하고 두 번
기각했습니다 — 1차엔 검색 실패가 0건이라 없는 문제였고, 2차엔 재현율을 실측
(엄격 75% · 주제 적합 ~90%)해 자문 용도에 충분했습니다. **다시 검토할 조건까지 기록해
뒀습니다**("실사용에서 자문 오답이 실제 문제로 실측되면").

**라이선스가 데이터에 실립니다.** CC-BY-4.0의 저작자 표시는 NOTICE에만 두면 파일이
저장소를 떠날 때 사라집니다. 문서마다 `license`·`attribution` 칸을 넣어 **인용이 출처와
함께 다니게** 합니다.

## 42. `azure-well-architected` — 199편

`…/MicrosoftDocs/well-architected/1353bbb66e53…` · 커밋 `1353bbb6…` · **CC-BY-4.0**

### 원본 형태

`well-architected/<기둥>/<문서>.md` — 5기둥 지침과 **트레이드오프 문서**
("비용을 아끼면 신뢰성에서 무엇을 잃는가").

### 처리 · 처리 후 형태

`patternkb/parsers/corpus.py`가 md 본문을 통째로 담고 라이선스·저작자를 붙입니다.

```json
{"id": "well-architected/ai/application-design",
 "title": "Application Design for AI Workloads on Azure",
 "path": "well-architected/ai/application-design.md",
 "source": "azure-well-architected", "section": "well-architected",
 "license": "CC-BY-4.0",
 "attribution": "Microsoft Corporation — MicrosoftDocs/well-architected, CC BY 4.0",
 "url": "https://github.com/MicrosoftDocs/well-architected/blob/1353bbb6…/well-architected/ai/application-design.md",
 "text": "# Application design for AI workloads …"}
```

---

## 43. `gcp-architecture-framework` — 57편 ★유일한 렌더링 HTML 소스

`https://cloud.google.com/architecture/framework` · **지문(저장소 없음)** · **CC-BY-4.0**

### 원본 형태

**이 저장소에서 웹페이지를 직접 받는 유일한 소스**입니다. devsite HTML이라 본문 주위가
전부 껍데기입니다(한 페이지 223,393자).

```html
<article class="devsite-article">
  <div class="devsite-article-meta nocontent" role="navigation" data-nosnippet>
    <ul class="devsite-breadcrumb-list" aria-label="Breadcrumb">
      <li class="devsite-breadcrumb-item">
        <a href="https://docs.cloud.google.com/" class="devsite-breadcrumb-link …">Home</a>
      …
```

### 처리

`patternkb/parsers/gcp_framework.py`

1. **`<article>` 영역만** 취하고 script·style·nav류는 버립니다.
2. **표·코드 블록의 구조는 보존하지 않습니다** — FTS 코퍼스라 본문 텍스트면 충분하고,
   구조에 기대는 순간 HTML 변주가 파서를 깨뜨립니다(데이터셋 > 파서).
3. `printable` 변형은 필러 전체의 중복이라 뺍니다.
4. 색인 페이지에서 하위 66링크가 **기계로 열거**됩니다. 재편은 patternkb의 **최소 편수
   불변식**이 잡습니다.

> **왜 여기만 허용되나.** 처음엔 "사람이 읽는 문서를 긁지 않는다"는 원칙으로 기각했지만
> 재조사에서 게이트가 전부 열렸습니다 — 푸터가 CC-BY-4.0을 명시하고, robots.txt 차단이
> 없고, 하위 링크가 기계 열거되며, digest 핀 선례가 이미 있습니다. 남는 것은 원칙의
> 형식뿐이었고, **용도가 산문 검색이라 구조 파싱이 필요 없다**는 근거로 사용자 승인을
> 받아 예외를 열었습니다(2026-07-24). ⚠ **사실 축에는 이 방식을 쓰면 안 됩니다.**

### 처리 후 형태

```json
{"id": "gcp-framework/cost-optimization",
 "title": "Well-Architected Framework: Cost optimization pillar",
 "path": "architecture/framework/cost-optimization",
 "source": "gcp-architecture-framework", "section": "gcp-framework",
 "license": "CC-BY-4.0",
 "attribution": "Google LLC — cloud.google.com/architecture/framework, CC BY 4.0",
 "url": "https://cloud.google.com/architecture/framework/cost-optimization",
 "text": "Home\nDocumentation\nCloud Architecture Center\nWell-Architected Framework: …"}
```

---

## 44. `twelve-factor` — 15편

`https://raw.githubusercontent.com/heroku/12factor/1385d2c80bac…` · 커밋 `1385d2c8…` · **MIT**

### 원본 형태

```markdown
## IV. Backing services
### Treat backing services as attached resources

A *backing service* is any service the app consumes over the network as part of its
normal operation.  Examples include datastores (such as [MySQL](http://dev.mysql.com/)
or [CouchDB](http://couchdb.apache.org/)), messaging/queueing systems …
```

### 처리 · 처리 후 형태

`content/en`의 md만 취하고 `toc.md`는 목차라 뺍니다(15편).

```json
{"id": "twelve-factor/admin-processes", "title": "Admin processes",
 "path": "content/en/admin-processes.md", "source": "twelve-factor",
 "section": "twelve-factor", "license": "MIT",
 "attribution": "Adam Wiggins — heroku/12factor, MIT",
 "url": "https://github.com/heroku/12factor/blob/1385d2c8…/content/en/admin-processes.md",
 "text": "## XII. Admin processes\n### Run admin/management tasks as one-off processes\n…"}
```

요구사항 단계의 관심사 축이 여기 상당수를 인용합니다.

---

## 45. `aws-well-architected` — 화이트페이퍼 PDF 177편 ★법적으로 가장 예민

`https://docs.aws.amazon.com/pdfs/wellarchitected/latest/framework/wellarchitected-framework.pdf`
· **지문** · 라이선스 **all-rights-reserved** · 재배포 **fair-use** · 14.2 MB

### 원본 형태

**1,002쪽 PDF**입니다. 구조를 실측했습니다 — **PDF 책갈피가 1,334개**이고 계층·쪽 번호가
붙어 있습니다.

### 처리

`patternkb/parsers/aws_waf.py`

1. **산문 휴리스틱이 아니라 책갈피로 자릅니다.** 목차 구조가 기계로 주어지는데 본문을
   정규식으로 자르는 것은 함정을 자초하는 일입니다.
2. **깊이 3까지**를 문서 경계로 씁니다 — 깊이 4부터는 베스트 프랙티스 낱개(647개)라
   문서가 너무 잘게 쪼개집니다. 검색 단위는 "질문/절" 수준이 알맞습니다(42번 199편과
   같은 입도).
3. `pypdf`는 **지연 import**입니다 — 코퍼스를 읽기만 하는 환경에 PDF 의존성을 강요하지
   않습니다.

> **사연이 깁니다.** HTML 문서는 사이트 약관이 자동 수집을 금지해 막혔습니다. 나중에
> **공식 배포 PDF**(docs.aws.amazon.com이 내려받으라고 배포하는 정식 산출물이라 수집이
> 정당)를 통해 열렸습니다 — *"권리 장벽" 판정이 소스 형태 하나에 갇혀 있었던 사례*입니다.
>
> **라이선스 부여가 없습니다.** 법적 고지가 'All rights reserved'뿐입니다(실측, 2쪽) —
> CC-BY가 명시된 42·43번과 결정적으로 다른 점입니다. 그래서 수록 근거가 라이선스가
> 아니라 **교육 목적 공정이용 판단**이고, 그 판단은 **허가가 아니므로** 세 곳
> (NOTICE · 산출물 `_note` · 문서별 attribution)에 사실을 명시하고 **권리자가 요청하면
> 제거**합니다. "허가받은 것처럼 굴지 않는다"는 문장까지 테스트가 강제합니다.

### 처리 후 형태

`data/aws-pattern-corpus.json.gz` — `docs` **177건** (별도 파일입니다)

```json
{"id": "aws-well-architected/aws-well-architected-framework/introduction@p6",
 "title": "AWS Well-Architected Framework › Introduction",
 "path": "wellarchitected-framework.pdf#page=6",
 "source": "aws-well-architected", "section": "aws-well-architected",
 "license": "All-rights-reserved",
 "attribution": "Amazon Web Services, Inc. — AWS Well-Architected Framework whitepaper,
                 All rights reserved (교육 목적 공정이용 수록 — 요청 시 제거)",
 "url": "https://docs.aws.amazon.com/pdfs/…/wellarchitected-framework.pdf#page=6",
 "text": "Publication date: November 6, 2024 (Document revisions)\nThe AWS Well-Architected
          Framework helps you understand the pros and cons of decisions you make …"}
```

`path`·`url`에 **쪽 번호가 박혀 있어** 인용이 원문 위치까지 되돌아갑니다.

---

## 42~44번이 함께 만드는 산출물

`data/pattern-corpus.json.gz` — `docs` **346건**

```
patterns/                   44편   ms-architecture-center
guide/architecture-styles/   7편
guide/design-principles/    11편
best-practices/             13편
twelve-factor               15편
well-architected           199편   azure-well-architected
gcp-framework               57편   gcp-architecture-framework
```

**선별의 기준**: architecture-center `docs/`는 502편인데 전부 담지 않습니다. 이 축이
답할 질문은 "이 설계 상황에 알려진 지침이 있나"이고 거기 맞는 하위 4곳만 담습니다.
Azure 참조 아키텍처류는 **안 담습니다** — 특정 벤더 제품 조합의 안내라 svcmap·guideline
축과 역할이 겹치고, 산문으로 담으면 그 축들의 **근거 규율(타입 id 대조)을 우회**하게
됩니다.

**문서 재편 감지**: 하위별 **최소 편수**를 불변식으로 박아, 트리에서 급감하면 빌드가
죽습니다 — 36번의 행수 검사와 같은 규율입니다.

---

# J. 그 밖 (2종)

## 46. `mingrammer-diagrams` — 서비스 분류(교차 검증용)

`https://raw.githubusercontent.com/mingrammer/diagrams/v0.24.4` · 태그 `v0.24.4` · **MIT**
· 프로바이더별 분류 파이썬 **48개** 캐시

### 원본 형태

**모듈 구조가 곧 분류 체계**입니다.

```python
# diagrams/aws/database.py  — This module is automatically generated by autogen.sh.
from . import _AWS

class _Database(_AWS):
    _type = "database"
    _icon_dir = "resources/aws/database"

class Aurora(_Database):        _icon = "aurora.png"
class DatabaseMigrationService(_Database):  _icon = "database-migration-service.png"
class DocumentdbMongodbCompatibility(_Database):  _icon = "documentdb-mongodb-compatibility.png"
class DynamodbDax(_Database):   _icon = "dynamodb-dax.png"
…
```

### 처리

`graphkb/parsers/svcmap.py` — 36번의 **독립 교차 소스**입니다.

1. 클래스 이름과 `_type`(카테고리)을 뽑아 "이 프로바이더에 이 서비스가 존재한다"는
   신호로 씁니다.
2. **36번과 이것이 같은 대응을 말하면 `svcmap-cross-checked`로 승급**합니다.
3. **덮는 범위가 다릅니다** — MS 표가 안 덮는 **ibm·alibabacloud·oci·openstack**을
   이쪽이 덮습니다. **tencent·ncloud 모듈은 없습니다(실측).**

### 처리 후 형태

`data/svcmap-graph.json.gz`의 일부 — `mingrammer-taxonomy` **17건**

```json
{"from": "app::relationalDatabase", "to": "alibaba::alicloud_db_instance",
 "type": "equivalent_to", "evidence": "mingrammer-taxonomy",
 "basis": "inferred", "reviewed": true}
```

---

## 47. `bitnami-charts` — 컨테이너 규모 프리셋

`…/bitnami/charts/33201f7e944a…/bitnami/common/templates/_resources.tpl`
· 커밋 `33201f7e…` · **Apache-2.0**(파일 헤더 기준. 저장소 메타는 NOASSERTION이라 파일
쪽을 근거로 삼음)

### 원본 형태

Helm(Go) 템플릿입니다. **원본이 스스로 경고를 달아 두었습니다.**

```
{{/*
Copyright Broadcom, Inc. All Rights Reserved.
SPDX-License-Identifier: APACHE-2.0
*/}}

{{/*
Return a resource request/limit object based on a given preset.
These presets are for basic testing and not meant to be used in production
*/}}
{{- define "common.resources.preset" -}}
{{- $presets := dict
  "nano" (dict
      "requests" (dict "cpu" "100m" "memory" "128Mi" "ephemeral-storage" "50Mi")
      "limits"   (dict "cpu" "150m" "memory" "192Mi" "ephemeral-storage" "2Gi")
   )
  "micro" (dict
      "requests" (dict "cpu" "250m" "memory" "256Mi" "ephemeral-storage" "50Mi")
      "limits"   (dict "cpu" "375m" "memory" "384Mi" "ephemeral-storage" "2Gi")
   )
  …
```

### 처리

`sizingkb/parsers/presets.py`

1. **Go 템플릿을 파싱하지 않습니다.** 완전한 파싱이 비싸고, 필요한 것은
   `dict "cpu" "100m" "memory" "128Mi"` 꼴의 **평평한 키-값**뿐이라 그 모양만 정규식으로
   집습니다. **못 읽으면 0건으로 두고 밝힙니다** — 반쯤 읽은 값을 담는 것보다 낫습니다.
2. **원본의 경고 문장을 값과 함께 담습니다.** 떼면 테스트용 숫자가 권장값으로
   둔갑합니다 — tumblebug `sg-default`에서 겪은 것과 같은 함정입니다.

### 처리 후 형태

`data/container-presets.json.gz` — `rules` **28건** (프리셋 7종 × requests/limits × cpu/memory)

```json
{"id": "bitnami::2xlarge/limits/cpu", "kind": "preset", "scope": "container:2xlarge",
 "metric": "limits.cpu", "value": "6.0", "unit": null,
 "evidence": "bitnami-preset", "note": "limits of the '2xlarge' preset",
 "caveat": "These presets are for basic testing and not meant to be used in production"}
```

**`caveat`가 레코드마다 붙습니다.** 컨테이너 규모이지 인스턴스 규모가 아닙니다.

---

## 부록. 소스가 아닌 산출물 하나 — `reviewed-sizing`

`data/reviewed-sizing.json.gz`는 **어느 소스에서도 오지 않습니다.** 2번에서 비어 있던
칸을 사람이 손으로 채운 것입니다.

```json
{"id": "reviewed::reserved-ips/aws", "kind": "reserved_ips", "scope": "aws",
 "metric": "reservedIps", "value": 5, "unit": "IPs", "evidence": "human-review",
 "note": "network address · VPC router · Amazon DNS · reserved for future use · broadcast",
 "caveat": "**A hand-entered value — there is no machine-readable source.**
   Check it against: AWS VPC User Guide 'Subnet CIDR blocks' (no machine-readable source —
   awsdocs/amazon-vpc-user-guide was archived 2023-06-15 and emptied)"}
```

**기계 판독 소스를 찾았고 없었습니다.**

```
awsdocs/amazon-vpc-user-guide       2023-06-15 아카이브 · 파일 7개로 비워짐
hashicorp/terraform-provider-aws    subnet/vpc 문서에 '예약' 언급 0건
GoogleCloudPlatform/compute-docs    404
tumblebug networkinfo.yaml          해당 칸이 빈칸
```

**값이 의심스러워서가 아니라 출처가 기계 판독이 아니라서** 따로 담습니다. 산출물이
갈려 있으면 나중에 진짜 소스가 생겼을 때 **이 파일만 지우면 됩니다.** 각 항목에
**사람이 확인할 수 있는 곳**을 적습니다 — 핀은 못 박아도 검증은 되게 합니다.

---

# K. 한 표로 — 소스 47종 → 산출물

`§`는 이 문서의 절 번호입니다. 레코드 수는 **파일 최상위 리스트/딕트의 항목 수 합**이라,
중첩 구조로 담는 파일(`aws-endpoints` 파티션 8 + 서비스 8, `cloud-regions` 프로바이더 10,
`cbspider-support` CSP 12)은 작아 보입니다 — 그 안에 각각 엔드포인트 9,039쌍 · 리전 188개 ·
(CSP, 리소스) 격자가 들어 있습니다.

| § | 소스 | 핀 | 라이선스 | 산출물 (파일 전체 레코드 수) |
|---:|---|---|---|---|
| 1 | `tumblebug-dump` | 태그 | Apache-2.0 | `basic-images` 6,033 · `tumblebug-cost` 73,083 · `tumblebug-perf` 65,032 |
| 2 | `tumblebug-src` | 태그 | Apache-2.0 | `tumblebug-bundles` 23 · `tumblebug-sizing` 31 |
| 3 | `tumblebug-latency` | 태그 | Apache-2.0 | `region-latency` 10,890 |
| 4 | `tumblebug-cloudinfo` | 태그 | Apache-2.0 | `cloud-regions` 10 |
| 5 | `tumblebug-swagger` | 태그 | Apache-2.0 | `core-graph` 32 |
| 6 | `cb-spider` | 태그 | Apache-2.0 | `cbspider-support` 12 |
| 7 | `cb-spider-map` | 동봉 | bundled-own | `mapping-graph` 174 |
| 8 | `cfn-schema` | **지문** | **미확인** | `aws-capacity` 47,070 · `aws-graph` 4,029 |
| 9 | `cdk-oob` | 태그 | **미확인** | `aws-graph` 4,029 |
| 10 | `botocore` | 태그 | Apache-2.0 | `aws-limits` 20 |
| 11 | `botocore-endpoints` | 태그 | Apache-2.0 | `aws-endpoints` 16 |
| 12 | `cfn-lint` | 태그 | MIT-0 | `aws-conditional` 966 · `aws-regions` 385 |
| 13 | `bicep-types-az` | 커밋 | MIT | `azure-capacity` 42,831 · `azure-graph` 5,896 |
| 14 | `azure-rest-api-specs` | 커밋 | **미확인** | `azure-mutability` 1,275 · `azure-operations` 1,839 · `azure-secret` 230 |
| 15 | `kcc-crd` | 태그 | Apache-2.0 | `gcp-capacity` 6,923 · `gcp-graph` 1,579 · `kcc-bundles` 296 |
| 16 | `tpaws-provider` | 태그 | MPL-2.0 | `aws-tf` 2,756 |
| 17 | `tpg-provider` | 태그 | MPL-2.0 | `gcp-capacity` 6,923 |
| 18 | `tp-alicloud` | 태그 | MPL-2.0 | `alibaba-capacity` 9,167 · `alibaba-graph` 1,134 |
| 19 | `tp-tencent` | 태그 | MPL-2.0 | `tencent-capacity` 11,222 · `tencent-graph` 1,320 |
| 20 | `tp-oracle` | 태그 | MPL-2.0 | `oracle-capacity` 14,614 · `oracle-graph` 986 |
| 21 | `tp-ibm` | 태그 | MPL-2.0 | `ibm-capacity` 1,987 · `ibm-graph` 558 |
| 22 | `tp-nhn` | 태그 | MPL-2.0 | `nhn-capacity` 779 · `nhn-graph` 110 |
| 23 | `tp-ncp` | 태그 | MPL-2.0 | `ncp-capacity` 427 · `ncp-graph` 33 |
| 24 | `tp-openstack` | 태그 | MPL-2.0 | `openstack-capacity` 725 · `openstack-graph` 108 |
| 25 | `aws-price-list` | 태그 | **미확인** | `aws-limits` 20 |
| 26 | `azure-retail-prices` | **지문** | not-stated | `azure-discount-pricing` 32,073 · `azure-managed-pricing` 23,563 |
| 27 | `cyclenerd-gcp-pricing` | 커밋 | Apache-2.0 | `gcp-managed-pricing` 731 · `gcp-spot-commit` 11,193 |
| 28 | `ec2-hardware` | 커밋 | MIT | `tumblebug-perf` 65,032 |
| 29 | `azure-compute-docs` | 커밋 | CC-BY-4.0 | `tumblebug-perf` 65,032 |
| 30 | `gcloud-machine-types` | 커밋 | Apache-2.0 | `tumblebug-perf` 65,032 |
| 31 | `ibm-global-catalog` | **지문** | not-stated | `ibm-perf` 2,002 |
| 32 | `gcp-carbon` | 커밋 | Apache-2.0 | `region-carbon` 161 |
| 33 | `ccf-emissions` | 커밋 | Apache-2.0 | `region-carbon` 161 |
| 34 | `endoflife-date` | 커밋 | MIT | `service-lifecycle` 17 |
| 35 | `azure-limits-doc` | 커밋 | CC-BY-4.0 | `azure-quota` 542 |
| 36 | `ms-architecture-center` | 커밋 | CC-BY-4.0 | `pattern-corpus` 346 · `svcmap-graph` 150 |
| 37 | `azure-quickstart-templates` | 커밋 | MIT | `aqt-cooccurrence` 1,253 |
| 38 | `aws-cfn-templates` | 커밋 | Apache-2.0 | `awscfn-cooccurrence` 1,147 |
| 39 | `widdix-cf-templates` | 커밋 | Apache-2.0 | `awscfn-cooccurrence` 1,147 |
| 40 | `aws-solutions-constructs` | 태그 | Apache-2.0 | `aws-pattern-bundles` 52 |
| 41 | `avm-bicep` | 커밋 | MIT | `avm-bundles` 207 · `azure-deploy-graph` 596 |
| 42 | `azure-well-architected` | 커밋 | CC-BY-4.0 | `pattern-corpus` 346 |
| 43 | `gcp-architecture-framework` | **지문** | CC-BY-4.0 | `pattern-corpus` 346 |
| 44 | `twelve-factor` | 커밋 | MIT | `pattern-corpus` 346 |
| 45 | `aws-well-architected` | **지문** | all-rights-reserved | `aws-pattern-corpus` 177 |
| 46 | `mingrammer-diagrams` | 태그 | MIT | `svcmap-graph` 150 |
| 47 | `bitnami-charts` | 커밋 | Apache-2.0 | `container-presets` 28 |

분포: 태그 23 · 커밋 18 · **지문 5** · 동봉 1 = 47.

---

# L. 이 문서를 쓰면서 세어 본 것 — 07-28 판과 어긋난 곳

전수 대조 중 **문서가 코드보다 자신 있게 말한 자리**가 하나 나왔습니다. 규율대로
적어 둡니다(옛 문서는 불변 기록이라 고치지 않습니다).

| 항목 | `kb-sourcebook-2026-07-28.md` | 2026-07-29 실측 (`unlicensed()`) |
|---|---|---|
| 라이선스 미확인 소스 | **13종** | **4종** — `cfn-schema` · `cdk-oob` · `aws-price-list` · `azure-rest-api-specs` |

`kbcommon/sources.py`는 그 문서와 **같은 커밋(9e3f047)** 이후 바뀌지 않았습니다. 즉
등록부에는 이미 43종의 라이선스가 적혀 있었는데 문서가 13종이라고 적은 것입니다.
07-28 판에서 "기록 없음"으로 표시된 것들(`bicep-types-az` MIT · `botocore` Apache-2.0 ·
`botocore-endpoints` Apache-2.0 · `azure-limits-doc` CC-BY-4.0 · `kcc-crd` Apache-2.0 ·
`tpaws-provider`·`tpg-provider` MPL-2.0 등)은 **등록부에 값이 있습니다.**

`unpinnable()`(지문 5종)과 핀 분포(23/18/5/1)는 문서와 코드가 일치했습니다.

---

# M. 한 장으로 줄이면 — 처리 규칙 아홉

47종을 한 줄씩 읽고 나면 **같은 규칙이 반복해서 나옵니다.** 소스가 달라도 처리가 닮은
이유입니다.

1. **한 방향만 말하는 칸을 양방향으로 읽지 않는다.**
   `BurstablePerformanceSupported`의 `false`는 "버스트가 아니다"가 아니라 "그 칸에서
   빠졌다"입니다(1번). `x-ms-mutability`가 없는 것도, cb-spider에 핸들러가 없는 것도,
   엔드포인트가 없는 것도 같습니다(6·11·14번).

2. **부재를 주장으로 승격하지 않는다.**
   `networkinfo.yaml`의 빈칸으로 규칙을 만들지 않고(2번), 구세대 목록에 없다고
   "최신"이라고 하지 않고(29번), standard 클래스에 검색 요금이 없다고 "무료"라고 하지
   않습니다(27번).

3. **값이 하나일 때만 담는다.**
   Azure 가격은 `_one()`이 여럿이면 버립니다(26번). Azure 작업 시간은 파일마다 다르면
   담지 않습니다(14번). AWS 한도는 두 소스가 어긋나면 담지 않습니다(10번).

4. **변별력이 없는 칸은 채움률이 100%여도 버린다.**
   IBM의 `freqency`는 310건 전부 2000이라 담지 않았습니다 — 담았으면 모든 IBM 스펙에
   "2.0 GHz"라는 확신에 찬 오답이 붙었을 것입니다(31번). 벤치마크 점수도 같은 이유로
   버립니다(28번).

5. **원본이 스스로 단 경고는 값과 함께 옮긴다.**
   bitnami "not meant to be used in production"(47번), tumblebug `sg-default`의 전 포트
   개방 경고(2번). 떼면 테스트용 숫자가 권장값이 됩니다.

6. **전체를 파싱하지 않고 필요한 모양만 집는다 — 그리고 그게 안전장치다.**
   Go `%v` 포맷(1번) · CFN YAML의 커스텀 태그(38번) · Helm 템플릿(47번) ·
   Go 스키마 리터럴(16~24번) · devsite HTML(43번). **데이터셋이 목표지 파서가 아닙니다.**

7. **다리를 건너면 등급이 떨어진다.**
   MS 표가 명시하는 것은 서비스 **이름** 대응이고, 타입 id에 붙이는 마지막 걸음은 우리
   손 검수라 `basis`가 `inferred`가 됩니다(36·46번). cb-spider 드라이버 매핑도 같습니다(7번).

8. **버린 것을 세어서 밝힌다.**
   Azure 가격 279,234행 드롭을 이유별로(26번), IBM의 버린 칸 이름을(31번),
   Solutions Constructs의 못 붙인 서비스를 개수까지(40번), cfn-lint의 못 맞춘 속성을(12번).
   **조용한 누락이 구조적으로 불가능하게** 만드는 것이 목적입니다.

9. **고정할 수 없으면 기록이라도 남긴다.**
   지문 핀 5종은 재현이 원리적으로 불가능합니다. 대신 `provenance.json`의 sha256이
   **바뀐 사실은** 잡습니다 — 실제로 AWS zip이 캐시와 라이브에서 달라진 것을 그 파일이
   찾아냈습니다(0.1·8번).
