# Azure Application Gateway HTTP 기능 경로 실험

## 결론

2026년 8월 17일 Korea Central에서 도메인 중립 테스트 앱을 사용해 다음 경로를 한 번 실제
배포했다.

```text
공개 HTTP Listener
→ Azure Application Gateway
→ Backend Pool
→ HTTP Health Probe
→ App VM의 8080 포트
```

기준선에서 `/readyz`와 `/business`가 각각 세 번 연속 성공했다. Backend Pool을 도달할 수 없는
사설 주소로 바꾸자 두 기능이 세 번 연속 실패했고, 원래 App VM 주소를 복원하자 다시 세 번
연속 성공했다. 따라서 현재 ResourcePlan이 선택하는 Azure Application Gateway의 HTTP
listener–backend 관계가 실제 앱 기능으로 이어지는 것을 1회 개발 관찰로 확인했다.

## 결과

| 항목 | 결과 |
|---|---:|
| 실행 ID | `easydep-http-6f3dee83` |
| 기준선 업무 경로 | 통과 |
| Backend 경로 제거 후 기능 상실 | 통과 |
| Backend 경로 복원 후 기능 회복 | 통과 |
| Application Gateway 생성 | 579.657초 |
| 전체 실행 | 1,447.053초 |
| Resource Group 삭제 | 603.295초 |
| 독립 잔여 조회 | `ed-http-` Resource Group 0개 |

원시 결과는
[`azure-sample-app-managed-http-result-20260817.json`](azure-sample-app-managed-http-result-20260817.json)에
보존한다. 파일 SHA-256은
`f400c891a3a9e4612449a2953be7e956d54347fa7e6ffeaaedcefdad188b413d`다.

## 해석 범위

- HTTP 기능 경로와 backend membership의 필요성만 관찰했다.
- 한 번의 개발 실행이므로 성공률이나 Azure 전체의 보편적 동작으로 일반화하지 않는다.
- 전송 보안, DNS, 인증서, 성능과 가용성 SLA는 측정하지 않았다.
- Backend를 하나만 사용했으므로 장애 중 다른 backend를 통한 업무 연속성을 증명하지 않는다.
- 실험 리소스는 전용 Resource Group에 만들었으며 종료 후 해당 실행 소유 리소스의 잔여가
  없음을 독립 조회했다.
