# VM 배포 자원 어휘와 범위 감사

## 결론

현재 DepKB의 자원 집합은 VM·NIC·네트워크·서브넷·방화벽·공인 IP·디스크·로드밸런서의
핵심 골격을 분석하기에는 충분하지만, **실행 가능한 Docker-on-VM 배포 전체를 완전하게
표현하기에는 부족하다.** 따라서 논문에서는 “VM 배포 핵심 관계 분석”으로만 주장한다.

## 현재 측정 범위

claim의 주체는 `vm`, `nic`, `network`, `subnet`, `firewall`, `loadBalancer` 여섯 종류다.
대상에는 `disk`, `sshKey`, `workloadIdentity`, `defaultRoute`, `publicIp` 등이 추가된다.
`publicIPPrefix`는 Azure 원어이며 일반 `publicIp`와 동일한 자원이라고 간주하지 않는다.

이 어휘는 선택한 CSP의 공식 리소스와 배포 역할을 구분하기 위한 조작적 어휘다. 실제 IaC에는
선택한 CSP의 원어와 리소스 구성을 그대로 보존한다.

## 아직 분석하지 않는 필수 후보

| 후보 | 필요한 이유 | 현재 처리 |
|---|---|---|
| machine image | VM 부팅 입력 | IaC 생성기가 다루지만 DepKB 관계 claim은 없음 |
| route table / non-default route | 세부 통신 경로 | 기본 라우트는 런타임 claim에 포함되지만 일반 라우팅 모델은 없음 |
| LB backend/attachment | VM을 LB 대상으로 연결 | 평가기에는 일부 개념이 있으나 DepKB 어휘에는 없음 |
| listener | 요청 수신 포트·프로토콜 | DepKB 범위 밖 |
| health check | LB가 정상 백엔드를 판정 | DepKB 범위 밖 |
| DNS/TLS certificate | 공인 서비스 이름과 HTTPS | Docker-on-VM 생성 범위 밖; 산출물은 HTTP 전용 |

이 후보를 측정하기 전에는 “모든 VM 배포 자원 의존성을 지원한다”, “생성된 그래프만으로
서비스가 배포 가능하다”라고 주장하지 않는다. 구현물 평가는 최종 Terraform·Dockerfile·
소스코드에 필요한 자원이 실제 포함됐는지를 별도로 검사한다.
