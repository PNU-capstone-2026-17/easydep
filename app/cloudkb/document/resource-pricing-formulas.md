# 클라우드 리소스 비용 계산식

> 기준일: 2026-09-09
> 대상: EasyDep Docker-on-VM 배포 계획에서 생성하거나 사용하는 AWS·Azure·GCP 리소스

이 문서는 [`resource_pricing_rules.json`](../costkb/resource_pricing_rules.json)의 과금식을
사람이 읽기 쉬운 형태로 정리한다. 실제 단가는 CSP 가격 API에서 조회해야 하며, 이 문서의
수식은 설명과 검토를 위한 것이다. 자유 형식 수식 문자열을 실행 코드로 평가하지 않는다.

## 1. 공통 기호와 계산 원칙

| 기호 | 의미 |
|---|---|
| `N` | 리소스 또는 인스턴스 수 |
| `H` | 청구 대상 사용 시간 |
| `T` | 월 또는 다른 과금 기간에서 실제 프로비저닝된 비율 |
| `S` | 저장 용량(GB·GiB) |
| `D` | 처리하거나 전송한 데이터(GB·GiB) |
| `R` | API 호출·트랜잭션·연결 등의 처리 횟수 |
| `p(x)` | 과금 항목 `x`의 단가 |
| `F(x)` | 계정에 실제로 남아 있는 무료 할당량 |

공통 합계는 다음과 같다.

```text
deployment monthly cost = sum(all billable meter costs)
```

사용량을 모르면 `0`으로 처리하지 않고 `unknown`으로 남긴다. Free Tier도 계정의 잔여
할당량을 확인할 수 있을 때만 차감한다. 현재 범위는 온디맨드 소매 정가이며 세금, 지원
플랜, 협상 할인, 예약 할인은 제외한다.

구간별 과금은 다음 함수로 표시한다.

```text
tiered(q) = sum(overlap(q, tier_j) * price(tier_j))
```

## 2. AWS

### 2.1 EC2 온디맨드 VM

규칙: `aws.compute.ec2-on-demand`

```text
runtime cost
  = instance count
  * billable runtime units
  * instance runtime rate
```

```math
C = N_{instance} \times H_{billable} \times p_{VM}
```

EBS, 공인 IPv4, 로드밸런서, NAT, 데이터 송신 및 버스트 CPU 초과 사용료는 별도다.

### 2.2 EBS gp3

규칙: `aws.storage.ebs-gp3`

```text
capacity cost
  = volume count * capacity GiB * provisioned month fraction * GiB-month rate

extra IOPS cost
  = volume count * max(0, provisioned IOPS - 3000)
  * provisioned month fraction * extra IOPS-month rate

extra throughput cost
  = volume count * max(0, provisioned throughput MiB/s - 125)
  * provisioned month fraction * extra throughput-month rate
```

### 2.3 Network Load Balancer

규칙: `aws.network.nlb`

```text
load balancer runtime cost
  = load balancer hours * load balancer hourly rate

NLCU cost
  = sum by protocol and hour(
      max(
        new flows per second / flow capacity,
        active flows / active-flow capacity,
        processed GiB per hour / 1 GiB
      ) * NLCU-hour rate
    )

implicit public IPv4 cost
  = public IPv4 count * load balancer hours * public IPv4 hourly rate
```

### 2.4 NAT Gateway

규칙: `aws.network.nat-gateway`

```text
gateway cost
  = sum(ceil(each partial gateway hour)) * gateway hourly rate

processing cost
  = processed GB * processed-GB rate
```

인터넷 송신 비용은 별도로 더한다.

### 2.5 Public IPv4

규칙: `aws.network.public-ipv4`

```text
address cost
  = address count * allocated hours * public IPv4 hourly rate
```

NLB가 소유하는 암묵적 공인 IP는 NLB 식에서 계산하므로 다시 더하지 않는다.

### 2.6 ECR

규칙: `aws.registry.ecr`

```text
storage cost
  = max(0, stored GB-month - remaining free storage GB)
  * registry storage rate

transfer cost
  = eligible transfer-out GB * registry transfer rate
```

### 2.7 인터넷 송신

규칙: `aws.network.internet-egress`

```text
egress cost
  = tiered(
      max(0,
        account-aggregated eligible egress GB
        - remaining free egress GB
      )
    )
```

### 2.8 기존 Secrets Manager 접근

규칙: `aws.secret.existing-access`

```text
access cost
  = API calls / calls per pricing unit * API-call rate
```

EasyDep가 생성하지 않은 Secret의 저장 비용은 포함하지 않고, 생성된 앱의 접근 비용만
귀속한다.

## 3. Azure

### 3.1 Linux VM 종량제

규칙: `azure.compute.linux-vm-payg`

```text
runtime cost
  = instance count * running hours * VM runtime rate
```

### 3.2 Standard HDD Managed Disk

규칙: `azure.storage.standard-hdd-managed-disk`

```text
capacity-tier cost
  = disk count * provisioned month fraction * disk-tier monthly rate

transaction cost
  = billable transaction batches after the hourly cap
  * transaction-batch rate
```

### 3.3 Standard Load Balancer

규칙: `azure.network.standard-load-balancer`

```text
first-five-rules cost
  = if billable rule count > 0:
      load balancer hours * first-five-rules hourly rate
    else 0

additional-rules cost
  = max(0, billable rule count - 5)
  * load balancer hours
  * additional-rule hourly rate

processing cost
  = processed GB * processed-GB rate
```

### 3.4 NAT Gateway

규칙: `azure.network.nat-gateway`

```text
gateway cost
  = sum(ceil(each partial gateway hour)) * gateway hourly rate

processing cost
  = processed GB * processed-GB rate
```

### 3.5 Public IPv4

규칙: `azure.network.public-ipv4`

```text
address cost
  = address count * rounded allocated hours * public IPv4 hourly rate
```

### 3.6 ACR Basic

규칙: `azure.registry.acr-basic`

```text
tier cost
  = registry days * registry daily rate

excess-storage cost
  = registry days
  * max(0, stored GiB - storage included in selected SKU)
  * excess-storage GiB-day rate
```

### 3.7 인터넷 송신

규칙: `azure.network.internet-egress`

```text
egress cost
  = tiered(max(0, internet egress GB - remaining free egress GB))
```

### 3.8 기존 Key Vault Secret 접근

규칙: `azure.secret.existing-key-vault-access`

```text
access cost
  = secret operations / operations per pricing unit
  * secret-operation rate
```

## 4. GCP

### 4.1 일반 Compute Engine VM

규칙: `gcp.compute.vm-on-demand`

```text
vCPU cost
  = instance count * vCPU count * billable runtime units * vCPU runtime rate

memory cost
  = instance count * memory GiB * billable runtime units * memory-GiB runtime rate
```

```math
C = N_{instance} \times H_{billable}
    \times (N_{vCPU} \times p_{vCPU} + S_{memory} \times p_{memory})
```

### 4.2 Shared-core VM

일반 VM의 vCPU·메모리 식과 동시에 적용하지 않는다.

```text
shared-core cost
  = instance count * billable runtime units * shared-core instance rate
```

### 4.3 Persistent Disk

규칙: `gcp.storage.persistent-disk`

```text
capacity cost
  = disk count * capacity GiB * provisioned time units * disk GiB-time rate
```

### 4.4 Regional External Passthrough Network Load Balancer

규칙: `gcp.network.regional-external-passthrough-nlb`

```text
first-five-forwarding-rules cost
  = if forwarding rule count > 0:
      forwarding-rule hours * first-five-rules hourly rate
    else 0

additional-rules cost
  = max(0, forwarding rule count - 5)
  * forwarding-rule hours
  * additional-rule hourly rate

inbound processing cost
  = inbound processed GiB * inbound processed-GiB rate

outbound processing cost
  = outbound processed GiB * outbound processed-GiB rate
```

### 4.5 Cloud NAT

규칙: `gcp.network.cloud-nat`

```text
gateway runtime cost
  = if assigned VM count <= 32:
      assigned VM count * gateway hours * per-VM gateway hourly rate
    else:
      gateway hours * capped gateway hourly rate

processing cost
  = processed GiB * processed-GiB rate

implicit external-IP cost
  = external IP count * gateway hours * NAT external-IP hourly rate
```

NAT 소유의 암묵적 외부 IP는 이 식에서 계산하므로 별도 외부 IP 비용에 다시 넣지 않는다.

### 4.6 External IPv4

규칙: `gcp.network.external-ipv4`

```text
address cost
  = max(0,
      address count * allocated hours
      - remaining free address hours
    ) * external IPv4 hourly rate
```

### 4.7 Artifact Registry

규칙: `gcp.registry.artifact-registry`

```text
storage cost
  = max(0, stored GiB-month - remaining free storage GiB)
  * artifact storage rate

transfer cost
  = transfer-out GiB * artifact transfer rate
```

### 4.8 인터넷 송신

규칙: `gcp.network.internet-egress`

```text
egress cost
  = tiered(max(0, internet egress GiB - remaining free egress GiB))
```

### 4.9 기존 Secret Manager 접근

규칙: `gcp.secret.existing-access`

```text
access cost
  = max(0, access operations - remaining free access operations)
  / operations per pricing unit
  * access-operation rate
```

## 5. 결과 표현

모든 입력을 알 때만 하나의 숫자로 합산한다.

```text
known subtotal = sum(cost terms with complete rate and usage inputs)
unknown terms  = terms missing a rate, configuration value, or usage value
```

비용 상한이 있을 때의 판정은 다음과 같다.

| 조건 | 판정 |
|---|---|
| 알려진 최소 비용이 상한 초과 | `exceeds` |
| 모든 항목이 계산됐고 합계가 상한 이내 | `within` |
| 미확정 사용량 때문에 결론을 낼 수 없음 | `indeterminate` |

## 6. 공식 근거

- 공통 비용 관계: [FOCUS 1.4 List Cost](https://focus.finops.org/docs/specification/v1-4/columns/cost-and-usage/list-cost/)
- AWS 가격 차원: [AWS service price list 구조](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/reading-service-price-list-file-for-services.html)
- AWS NAT: [Amazon VPC pricing](https://aws.amazon.com/vpc/pricing/)
- AWS 로드밸런서: [Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)
- Azure 가격 API: [Azure Retail Prices REST API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
- GCP 가격식과 티어: [Cloud Billing Catalog API](https://docs.cloud.google.com/billing/docs/reference/rest/v1/services.skus/list)
- GCP NAT: [Google Cloud network pricing](https://cloud.google.com/vpc/network-pricing)
