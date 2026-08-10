# AWS 퍼블릭 NAT 제어면 실험 결과

퍼블릭 NAT 게이트웨이는 VPC, 서브넷, Internet Gateway, 기본 경로, Elastic IP 및 NAT Gateway를 조합한 뒤에야 사용 가능한 상태가 되었다. NAT가 존재하는 동안 이를 포함하는 서브넷을 삭제하자 `DependencyViolation`으로 실패했다.

연결된 Elastic IP 해제는 `AuthFailure`로 실패했으므로, 이를 의존성의 증거로 해석하기에는 모호하다. NAT를 삭제한 뒤 역순 정리는 성공했다. 태그 기반 잔존 리소스 조회에서는 활성 리소스를 찾지 못했으며, NAT 조회에는 최종 `deleted` 레코드만 반환되었다.

결론: AWS 퍼블릭 NAT 경로는 공급자별 조합으로 모델링하고 서브넷과 NAT 사이의 수명 주기 제약을 유지한다. 이번 실행만으로 EIP 수명 주기 엣지를 추론하지 않는다.
