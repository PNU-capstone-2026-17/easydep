# 배포 템플릿 보안 검증 결과

- 실행 ID: `full-20260905-final2`
- 실행 시각(UTC): `2026-09-05T02:19:10.321552+00:00`
- 검사 조합: 45 / 45
- 최종 결과: **PASS**

이 검사는 문서용 예시가 아니라 사용자가 받는 것과 같은 배포 패키지를 생성한 뒤 
Trivy, OpenTofu, cloud-init, Compose 및 배포 스크립트 검사를 수행한다. OpenTofu는 
실제 리소스를 만들지 않는 `init -backend=false`와 `validate`까지만 실행한다.

## Provider별 요약

| Provider | 조합 수 | 차단 finding | 검토 후 허용 | 실패 조합 |
|---|---:|---:|---:|---:|
| AWS | 15 | 0 | 58 | 0 |
| AZURE | 15 | 0 | 0 | 0 |
| GCP | 15 | 0 | 7 | 0 |

## 전체 조합

| Provider | Template case | Structure | Trivy | Package |
|---|---|---|---|---|
| AWS | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AWS | `standalone-vm-cu1-r1-z1-w2-pw1.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AWS | `standalone-vm-cu2-r1-z1-w2-pw1.relations-separate1.direct-public-ip` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AWS | `managed-vm-group-cu1-r1-z1-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AWS | `managed-vm-group-cu2-r1-z1-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AWS | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AWS | `managed-vm-group-cu2-r2-z1-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 2 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AWS | `managed-vm-group-cu1-r2-z2-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 2 zone(s), loadBalancer | PASS | PASS |
| AWS | `managed-vm-group-cu2-r2-z2-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 2 replica(s), 2 zone(s), loadBalancer | PASS | PASS |
| AWS | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.private-egress-only` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), privateEgressOnly | PASS | PASS |
| AWS | `standalone-vm-cu2-r1-z1-w2-pw1.relations-none.direct-public-ip.bindings-per-replica-storage1` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AWS | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip.bindings-secret1` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AWS | `standalone-vm-cu1-r1-z1-w2-pw0.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AWS | `standalone-vm-cu2-r1-z1-w2-pw0.relations-separate1.direct-public-ip` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AWS | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.private-egress-only` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 1 zone(s), privateEgressOnly | PASS | PASS |
| AZURE | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AZURE | `standalone-vm-cu1-r1-z1-w2-pw1.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AZURE | `standalone-vm-cu2-r1-z1-w2-pw1.relations-separate1.direct-public-ip` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AZURE | `managed-vm-group-cu1-r1-z1-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AZURE | `managed-vm-group-cu2-r1-z1-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AZURE | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AZURE | `managed-vm-group-cu2-r2-z1-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 2 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| AZURE | `managed-vm-group-cu1-r2-z2-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 2 zone(s), loadBalancer | PASS | PASS |
| AZURE | `managed-vm-group-cu2-r2-z2-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 2 replica(s), 2 zone(s), loadBalancer | PASS | PASS |
| AZURE | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.private-egress-only` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), privateEgressOnly | PASS | PASS |
| AZURE | `standalone-vm-cu2-r1-z1-w2-pw1.relations-none.direct-public-ip.bindings-per-replica-storage1` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AZURE | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip.bindings-secret1` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AZURE | `standalone-vm-cu1-r1-z1-w2-pw0.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AZURE | `standalone-vm-cu2-r1-z1-w2-pw0.relations-separate1.direct-public-ip` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| AZURE | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.private-egress-only` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 1 zone(s), privateEgressOnly | PASS | PASS |
| GCP | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| GCP | `standalone-vm-cu1-r1-z1-w2-pw1.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| GCP | `standalone-vm-cu2-r1-z1-w2-pw1.relations-separate1.direct-public-ip` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| GCP | `managed-vm-group-cu1-r1-z1-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| GCP | `managed-vm-group-cu2-r1-z1-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| GCP | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| GCP | `managed-vm-group-cu2-r2-z1-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 2 replica(s), 1 zone(s), loadBalancer | PASS | PASS |
| GCP | `managed-vm-group-cu1-r2-z2-w1-pw0.relations-none.load-balancer` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 2 zone(s), loadBalancer | PASS | PASS |
| GCP | `managed-vm-group-cu2-r2-z2-w2-pw1.relations-separate1.load-balancer` | managed VM group, 2 compute unit(s), 2 workload(s), 2 replica(s), 2 zone(s), loadBalancer | PASS | PASS |
| GCP | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.private-egress-only` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), privateEgressOnly | PASS | PASS |
| GCP | `standalone-vm-cu2-r1-z1-w2-pw1.relations-none.direct-public-ip.bindings-per-replica-storage1` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| GCP | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip.bindings-secret1` | standalone VM, 1 compute unit(s), 1 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| GCP | `standalone-vm-cu1-r1-z1-w2-pw0.relations-none.direct-public-ip` | standalone VM, 1 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| GCP | `standalone-vm-cu2-r1-z1-w2-pw0.relations-separate1.direct-public-ip` | standalone VM, 2 compute unit(s), 2 workload(s), 1 replica(s), 1 zone(s), directPublicIp | PASS | PASS |
| GCP | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.private-egress-only` | managed VM group, 1 compute unit(s), 1 workload(s), 2 replica(s), 1 zone(s), privateEgressOnly | PASS | PASS |

## 실패 및 검토가 필요한 항목

- 없음

## 배포 구조에 따라 허용한 항목

아래 항목은 규칙 전체를 무시한 것이 아니다. ResourcePlan에 공개 진입점이나 
필요한 외부 통신이 명시된 조합에만 같은 조건을 다시 확인한 뒤 허용했다.

- `AWS-0053` 6건 — 조건: AWS Load Balancer with scheme=public; 근거: The selected topology explicitly exposes its managed load balancer as the application's public ingress.
- `AWS-0104` 52건 — 조건: AWS compute + ECR registry + explicit default route + only TCP 80/443 external egress; 근거: Generated hosts need outbound HTTP/HTTPS for bootstrap packages and immutable container image pulls; all-protocol egress remains closed.
- `GCP-0031` 7건 — 조건: GCP publicIngress with ingressKind=directPublicIp; 근거: The selected standalone-VM topology explicitly uses a direct public address as its ingress endpoint.

상세 명령 결과와 파일 digest는 Git에 넣지 않는 
`artifacts/deployment-template-security/full-20260905-final2/` 아래에 있다.
