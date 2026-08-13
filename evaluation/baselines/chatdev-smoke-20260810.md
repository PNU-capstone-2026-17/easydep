# ChatDev 기준군 실행 확인

## 실행 범위

- 공식 ChatDev `v1.1.6`, commit `bcab15717940818938402394a04aea2052d76665`
- P1-AWS 입력 1회
- 모델: 루트 `.env`와 동일한 `openai/gpt-oss-120b`
- ChatDev 소프트웨어 회사의 기본 demand analysis, coding, review, testing, documentation chain
- 실제 cloud 리소스 생성과 Docker 평가는 수행하지 않음

## 실행 결과

ChatDev native chain은 429.412초에 정상 종료했다. LLM 호출은 14회였고 HTTP 오류 응답은 없었다.
생성 디렉터리는 공통 평가 경계인 `repo/`로 복사됐으며 원본 ChatDev 작업 디렉터리는 실행 뒤
삭제됐다. 공급자 응답 본문과 API 키는 측정 로그에 저장하지 않았다.

구형 런타임을 현재 OpenAI 호환 endpoint에 연결하면서 다음 호환 문제가 확인됐다.

1. `openai 1.3.3`이 `httpx 0.28`과 호환되지 않아 `httpx 0.27.2`를 환경에 고정했다.
2. 공급자가 메시지에 추가한 `annotations`를 ChatDev가 역직렬화하지 못해, 프록시가 표준 메시지
   필드만 전달하도록 했다.
3. ChatDev 1.1.6이 긴 프롬프트에서 음수 `max_tokens`를 계산해, 잘못된 값에 한해서만 고정된 양수
   상한으로 교정했다. 이 실행에서는 14회 중 8회에 적용됐다.

이 변환들은 ChatDev 역할, 프롬프트, chat chain 또는 생성 결과를 고치지 않는다. 모델 endpoint와
구형 클라이언트 사이의 전송 호환만 담당하며 적용 여부를 실행 manifest와 이벤트 로그에 남긴다.

## 공통 정적 평가 연결

공통 평가기는 생성 저장소 21개 파일을 읽고 종료했다. ChatDev 실행 성공과 산출물 품질은 구분된다.
이 1회 출력은 Dockerfile이 없었고 Terraform 파일 앞에 Python식 docstring을 넣어 HCL 파싱에
실패했으므로 `implementationComplete=false`, `experimentEligible=false`였다. 이는 기준군을
사후 보정하지 않고 그대로 측정한 개발 smoke 결과다. 잘못된 HCL 때문에 평가기 자체가 중단되지
않도록 파서 오류를 구조화해 기록하는 일반 보완도 추가했다.

이 결과는 ChatDev 기준군 실행 경계가 작동한다는 증거일 뿐, EasyDep과의 효과 비교 결과가 아니다.
비교 주장은 동일 사례·동일 반복·동일 외부 평가기로 네 시스템을 실행한 뒤에만 제시한다.
