# 자연어 질문–응답 부분 재개 파일럿

## 목적과 범위

사용자가 클라우드 제약을 빠뜨렸을 때 EasyDep이 추측하지 않고 질문하며, 답변 뒤에는 관련
계약 작업만 다시 수행하는지 확인했다. 이 파일럿은 Docker-on-Linux-VM 한 건의 개발 검증이며
일반화나 앱 기능 성공을 주장하지 않는다.

## 발견한 런타임 불일치

기존 오케스트레이션 어댑터는 항상 피드백 게이트가 없는 그래프를 사용했다. 같은 자연어 입력은
내부적으로 provider·region·월 예산 누락을 계산했지만 사용자에게 질문하지 않고 완료됐다. 대화형
실행은 질문 게이트를 사용하고 배치 실행은 기존 무중단 그래프를 쓰도록 모드를 분리했다.

또한 구조화 답변을 일반 문자열 피드백과 구분하지 않았고, 답변 블록의 `필드: 값` 표현이 grounding
대상에서 빠져 정직한 인용을 환각으로 거절했다. 답변을 `ResourceAnswer` 계약으로 감싸고 실제로
모델에 보여 준 답변 표현을 근거 후보에 포함했다. provider·DB·앱 이름에 따른 분기는 추가하지 않았다.

## 실제 관찰

입력은 “Deploy a Dockerized Spring Boot REST service on Linux virtual machines.”였다. 첫 checkpoint는
provider, region, monthlyBudgetUSD를 필수 질문으로, minVCpu, trafficPattern, multiZone을 권고 질문으로
제시했다. 답변으로 Azure, Korea Central, 월 100 USD를 주자 다음 결과가 나왔다.

- 계약: provider `azure`, region `koreacentral`, monthlyBudgetUSD `100.0`
- 남은 필수 질문과 거절된 답변: 각각 0건
- 재개 실행: LLM 1회, LLM 4.638초, 전체 7.139초
- 상류 `deployment_needs`: 보존

초기 실행은 결과 출력의 CP949 인코딩 실패 때문에 내부 telemetry를 보존하지 못했다. 셸에서 관찰한
51.5초에는 모듈 import와 로컬 BERT 로드가 포함되므로 LLM 시간으로 해석하지 않는다. 수정된 독립
probe는 TTFT 2.000초, 전체 2.161초, HTTP 429 없음이었다.

## 해석 한계와 다음 단계

이 결과는 자연어 입력에서 질문이 나오고, 구조화 답변이 계약을 바꾸며, 상류 분석을 반복하지 않는
경로가 실제로 동작한다는 개발 증거다. 전체 요구사항 산출물 완료, 애플리케이션 기능, cloud apply,
다른 모호성 유형의 일반화는 측정하지 않았다. 다음에는 최소 용량 누락과 HA–로컬 상태 충돌 두 건만
같은 방식으로 확인한다. 세 건이 끝나기 전 새 capability나 사례별 진단 규칙을 늘리지 않는다.

기계 판독 결과는 `natural-language-feedback-pilot-20260809.json`에 있다.
