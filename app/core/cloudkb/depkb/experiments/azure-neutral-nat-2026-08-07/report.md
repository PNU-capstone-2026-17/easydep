# Azure NAT Gateway 제어면 실험 결과

Standard 정적 Public IP를 NAT Gateway에 연결한 뒤, 이 NAT Gateway를 subnet에 연결했다.
Azure는 참조 중인 Public IP의 삭제를 `PublicIPAddressCannotBeDeleted` 오류로 거부했고,
연결된 NAT Gateway의 삭제를 `CannotDeleteNatGatewayAssociatedToSubnet` 오류로 거부했다.

NAT Gateway와 subnet의 연결을 해제한 뒤 두 리소스를 모두 삭제했으며, resource group 잔존
확인 결과는 false였다. 이번 실행의 조합 형태 조회는 Windows 명령 파싱 과정에서 잘못
구성되었으므로 증거로 사용할 수 없다. 반면 명시적인 삭제 실패 두 건과 연결 해제 후 삭제에
성공한 순서는 증거로 사용할 수 있다.

결론: Public IP -> NAT Gateway와 NAT Gateway -> subnet을 보편적인 중립 모델 요구 사항이
아니라 명시적인 Azure 조합/수명 주기 관계로 유지한다.
