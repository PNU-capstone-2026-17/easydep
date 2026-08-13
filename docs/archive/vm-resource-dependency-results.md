# Docker-on-VM 리소스 의존성 결과

정본은 [claims.json](../../app/core/cloudkb/depkb/claims.json)이다. 현재 제품 KB는 새로운 배포의
생성과 기능 검증에 필요한 프로비저닝 33개, 런타임 12개만 포함한다.

## 1. 요약

| CSP | 프로비저닝 | 런타임 | 합계 |
|---|---:|---:|---:|
| AWS | 12 | 5 | 17 |
| Azure | 12 | 4 | 16 |
| GCP | 9 | 3 | 12 |
| 합계 | 33 | 12 | 45 |

반복 상태는 성공 38개, 실패 5개, 대기 2개다. 실패·대기 claim은 기록에는 남지만 실제
의존성 폐쇄와 계획 생성에는 사용하지 않는다.

## 2. 정규화된 이름

| 중립 이름 | AWS | Azure | GCP |
|---|---|---|---|
| `network` | VPC | Virtual Network | VPC Network |
| `subnet` | Subnet | Virtual Network Subnet | Subnetwork |
| `firewall` | Security Group | Network Security Group | VPC Firewall Rule |
| `nic` | Elastic Network Interface | Network Interface | VM의 `network_interface` 구성 |
| `publicIp` | Public IPv4/Elastic IP | Public IP Address | External IP/`accessConfig` |
| `vm` | EC2 Instance | Linux Virtual Machine | Compute Engine Instance |
| `disk` | EBS Volume | Managed Disk | Persistent Disk |
| `workloadIdentity` | IAM Role + Instance Profile | Managed Identity | Service Account 결합 |
| `loadBalancer` | ALB/NLB 구성 | Load Balancer/Application Gateway 구성 | Forwarding rule부터 backend까지의 구성 |

이 표는 동일 제품을 뜻하지 않는다. 같은 논리 capability가 CSP마다 여러 native 리소스나
하위 구성 요소로 투영될 수 있다.

## 3. 프로비저닝 관계

표의 `필수`, `조건부`, `비필수`는 각각 `mandatoryForProvisioning`,
`conditionalForProvisioning`, `notMandatoryForProvisioning`을 줄인 것이다.

### AWS

| 관계 | 판정 |
|---|---|
| firewall → network | 비필수 |
| loadBalancer → firewall | 비필수 |
| loadBalancer → subnet | 필수 |
| nic → firewall | 비필수 |
| nic → subnet | 필수 |
| subnet → network | 필수 |
| vm → disk | 비필수 |
| vm → firewall | 비필수 |
| vm → nic | 비필수 |
| vm → sshKey | 비필수 |
| vm → subnet | 비필수 |
| vm → workloadIdentity | 비필수 |

AWS의 비필수 판정에는 default VPC·default Security Group·primary ENI·root volume처럼
provider나 기존 환경이 보완하는 경우가 포함된다. 최소 설계에서 항상 생략하라는 뜻은 아니다.

### Azure

| 관계 | 판정 |
|---|---|
| loadBalancer → publicIp | 비필수 |
| loadBalancer → subnet | 비필수 |
| loadBalancer → subnet/publicIp/publicIPPrefix | 조건부·반복 대기 |
| network → subnet | 비필수 |
| nic → firewall | 비필수 |
| nic → publicIp | 비필수 |
| nic → subnet | 필수 |
| subnet → firewall | 비필수 |
| subnet → network | 필수 |
| vm → disk | 비필수 |
| vm → nic | 필수 |
| vm → workloadIdentity | 비필수 |

Azure VM은 NIC가 필요하고 NIC는 subnet이 필요하다. public IP와 별도 data disk, managed
identity는 요구사항에 따라 선택된다.

### GCP

| 관계 | 판정 |
|---|---|
| firewall → network | 비필수 |
| loadBalancer → network | 비필수 |
| loadBalancer → subnet | 조건부 |
| nic → network | 비필수 |
| nic → subnet | 조건부 |
| subnet → network | 필수 |
| vm → disk | 필수 |
| vm → nic | 필수 |
| vm → workloadIdentity | 비필수 |

GCP의 NIC는 독립 리소스라기보다 VM 구성 블록으로 실현된다. 따라서 중립 관계 하나가
Terraform 리소스 하나와 항상 대응하지 않는다.

## 4. 런타임 관계

| CSP | 관계 | 기능 신호 | 반복 상태 |
|---|---|---|---|
| AWS | subnet → defaultRoute | 외부 HTTPS 송신 | 실패 |
| AWS | subnet → defaultRoute | 외부 TCP 수신 | 성공 |
| AWS | vm → firewall | 외부 TCP 수신 | 성공 |
| AWS | vm → publicIp | 외부 TCP 수신 | 성공 |
| AWS | vm → workloadIdentity | 메타데이터 자격 증명 | 실패 |
| Azure | loadBalancer → vm | LB 서비스 응답 | 실패 |
| Azure | nic → publicIp | 외부 TCP 수신 | 성공 |
| Azure | subnet → firewall | 외부 TCP 수신 | 성공 |
| Azure | vm → disk | 볼륨 쓰기 | 대기 |
| GCP | network → defaultRoute | 외부 TCP 수신 | 실패 |
| GCP | vm → firewall | 외부 TCP 수신 | 실패 |
| GCP | vm → publicIp | 외부 TCP 수신 | 성공 |

여기서 실패는 관계가 없다는 판정이 아니라 반복 실행에서 동결된 기대 결과를 재현하지 못했다는
뜻이다. 해당 관계를 일반 규칙으로 사용하지 않는다.

## 5. 제거한 삭제 전용 관계

과거 KB에는 연결 상태에서 삭제가 차단되는지를 나타내는 11개 claim과 다음 출력 필드가 있었다.

- `deleteBlockedWhileAttached`
- `detachRequiredBeforeDelete`
- `cascadeDeletedWithOwner`

EasyDep의 현재 목적은 새로운 앱과 배포를 만드는 것이다. 삭제 순서는 Terraform/OpenTofu의 상태와
참조 그래프가 담당하므로 제품 KB, `Closure`, `InfraIntent`, provisioning view에서 제거했다.
과거 실험 파일은 보존한다. 같은 실험의 생성 단계가 현재 프로비저닝 claim의 근거로도 사용되기
때문이다.

## 6. 기능 검증과의 관계

리소스 생성은 첫 번째 관문일 뿐이다.

```text
IaC validate/plan
  → 실제 리소스 생성
  → VM·네트워크 준비 상태
  → 컨테이너 실행
  → HTTP/API 업무 기능
  → 영속성·장애 허용 등 요구 기능
```

따라서 DepKB 결과만으로 “애플리케이션이 정상 작동한다”고 주장하지 않는다. 런타임 인프라
신호와 애플리케이션 업무 기능 검사를 별도로 기록한다.
