# 벤더 원천 스냅샷 재현성 점검

## 결론

DepKB의 커밋된 그래프와 인벤토리는 캐시 없이도 사용할 수 있다. 다만 원천 문서부터
인벤토리를 다시 추출하는 검증은 선언된 해시와 일치하는 고정 원천 스냅샷이 있을 때만
실행한다. 누락된 원천을 현재 롤링 문서로 대체하지 않는다.

## AWS

- 원천: AWS CloudFormation Resource Specification 258.0.0
- 고정 URL: `https://d1uauaxba7bl26.cloudfront.net/258.0.0/gzip/CloudFormationResourceSpecification.json`
- SHA-256: `cb04ddec8e3e2e87f06a628c1e31b1640f49492e9d86d8cb48d7c2ef527dae63`
- 2026-08-09에 공식 고정 URL에서 다시 내려받아 선언된 해시와 일치함을 확인했다.

기존 구현은 파일명과 해시는 258.0.0으로 고정하면서 다운로드 URL에는 `latest`를
사용했다. 최신 판이 바뀌면 복구가 실패하므로 버전 URL로 수정했다. AWS는 리소스
명세에 판 번호가 있으며 이전 판도 버전 URL로 지정할 수 있다.

## GCP

- 원천: Google Compute API Discovery Document
- 고정 revision: `20260722`
- SHA-256: `b71cb75cb68d790065cecb01363b0d714c6388304ae027c45108255b311a3203`

공식 Discovery endpoint는 API의 `v1` 문서를 제공하지만 이 프로젝트가 고정한 과거
revision을 선택하는 공식 URL은 확인하지 못했다. 이는 문서에 과거 revision 조회가
없다는 점에서 내린 현재의 조사 결론이며, 서비스가 영구히 제공하지 않는다는 주장은
아니다. 따라서 과거 스냅샷이 없는 환경에서 현재 문서를 같은 파일명으로 저장하거나
핀을 자동 갱신하지 않는다.

## 시험 정책

- 캐시가 있으면 파일 해시를 먼저 확인하고 원천 재추출 결과를 커밋된 인벤토리와
  완전히 비교한다.
- 고정 캐시가 없으면 해당 원천 재추출 시험만 `skip`으로 기록한다. 커밋된 그래프의
  스키마·정합성 시험은 계속 실행한다.
- GCP 원천부터의 완전한 clean-room 재현은 과거 원문 스냅샷을 출처와 함께 보존하기
  전까지 미충족으로 보고한다.

참고: [AWS CloudFormation 리소스 명세](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/cfn-resource-specification.html), [Google API Discovery 문서 사용법](https://developers.google.com/discovery/v1/using)
