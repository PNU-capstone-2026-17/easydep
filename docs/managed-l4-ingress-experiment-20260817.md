# 관리형 L4 진입 경로 실험

## 목적과 범위

AWS·Azure·GCP에서 현재 ResourcePlan이 선택하는 L4 Load Balancer가 두 VM의 중립 앱으로 TCP
트래픽을 전달하고, readiness가 실패한 backend를 제외한 뒤 복구된 backend를 다시 사용하는지
확인했다. HTTP는 TCP 전달을 관찰하기 위한 페이로드일 뿐이며 L7 라우팅은 시험하지 않았다.

앱에는 `/health/ready`, `/instance`, 시험 전용 `/__easydep_test/fault`만 있다. 데이터베이스,
영속 디스크, 수강신청 업무 규칙은 포함하지 않았다. 이 분리로 L4 리소스 관계와 앱 복잡도를
섞지 않았다.

## 공통 판정 절차

1. 공개 TCP 80으로 readiness 요청이 성공해야 한다.
2. 반복 `/instance` 요청에서 서로 다른 두 VM이 모두 보여야 한다.
3. 한 앱 프로세스를 종료한 뒤 생존 VM의 응답이 10회 연속 성공해야 한다.
4. 종료한 프로세스만 운영자 명령으로 복구한 뒤 두 VM이 다시 보여야 한다.
5. 실행 ID가 소유한 리소스를 모두 삭제하고 잔여가 0이어야 한다.

## 결과

| CSP | 실제 L4 제품 | 두 backend | 장애 제외 확인 | 두 backend 복원 확인 | 전체 실행 | 정리 |
|---|---|---:|---:|---:|---:|---:|
| AWS | Network Load Balancer | 통과 | 31.388초 | 59.599초 | 521.1초 | 잔여 0 |
| Azure | Standard Load Balancer | 통과 | 17.044초 | 53.038초 | 467.9초 | 잔여 0 |
| GCP | Regional External Passthrough Network Load Balancer | 통과 | 24.955초 | 69.722초 | 432.1초 | 잔여 0 |

시간은 fault 요청 직전부터 잰 개발 관찰값이다. 반복 측정이나 통제된 네트워크 조건이 없으므로
SLA 또는 CSP 간 성능 비교로 사용하지 않는다. AWS 첫 개발 시도는 기존 PostgreSQL 포함 앱이
EC2 user-data 16KiB 제한을 넘어 VM 생성 전에 실패했다. L4에 필요한 endpoint만 가진 공통 앱으로
분리한 뒤 재실행했으며, 첫 시도의 생성 리소스도 잔여 0을 확인했다.

## CSP별 실현 차이

- AWS: 서로 다른 두 가용 영역의 EC2를 TCP target group에 등록했다. NLB Security Group은
  실험 실행자 주소의 TCP 80만 받고, 앱 Security Group은 NLB Security Group의 TCP 8080만 받았다.
- Azure: 두 영역 VM의 NIC를 Standard Load Balancer backend pool에 연결했다. TCP 80을 VM의
  TCP 8080으로 전달하고 Azure Load Balancer probe를 별도로 허용했다.
- GCP: 두 영역의 unmanaged instance group을 regional backend service에 연결했다. passthrough
  방식은 port를 변환하지 않으므로 frontend와 앱이 모두 TCP 80을 사용했다. 원본 client IP가
  보존되므로 실험 중 backend firewall이 TCP 80 client traffic을 허용했고 종료 때 제거했다.

## 주장할 수 있는 것과 없는 것

이번 1회 개발 실험으로 관찰한 것은 L4 전달, HTTP health check, 두 backend 도달, 장애 backend
제외, 운영자 복구 후 재편입, 실행 소유 리소스 정리다. 다음은 측정하지 않았다.

- 관리형 VM 그룹의 자동교체 또는 자동복구
- 가용성 SLA와 n분 이내 복구 보장
- 처리량, 지연시간, backend별 분배 공정성
- TLS, 데이터베이스, 영속성, 리전 장애

원시 로그는 다음 파일에 있다.

- `evaluation/dependency_audit/aws-managed-l4-ingress-result-20260817.json`
- `evaluation/dependency_audit/azure-managed-l4-ingress-result-20260817.json`
- `evaluation/dependency_audit/gcp-managed-l4-ingress-result-20260817.json`
