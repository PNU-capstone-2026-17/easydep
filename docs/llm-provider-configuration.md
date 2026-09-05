# LLM provider 설정

EasyDep의 요구사항·설계·구현·Testing은 하나의 LLM 연결 설정을 함께 사용한다. provider를
URL이나 예전에 남은 환경변수로 추측하지 않고 `LLM_PROVIDER`로 선택한다. 따라서 provider를
바꿀 때에는 다음 네 값을 함께 설정한다.

```env
LLM_PROVIDER=openrouter
API_KEY=발급받은_키
BASE_URL=https://openrouter.ai/api/v1
MODEL=openai/gpt-4o-mini
```

## 지원 provider

| `LLM_PROVIDER` | `MODEL`에 넣는 값 | OpenHands가 사용하는 LiteLLM 이름 | 설명 |
|---|---|---|---|
| `openrouter` | OpenRouter의 모델 ID | `openrouter/<MODEL>` | OpenRouter API |
| `nvidia_nim` | NVIDIA endpoint의 모델 ID | `nvidia_nim/<MODEL>` | NVIDIA NIM |
| `cloudflare` | Workers AI 모델 ID | `openai/<MODEL>` | Cloudflare AI Gateway |
| `openai_compatible` | 연결한 endpoint의 모델 ID | `openai/<MODEL>` | 그 밖의 OpenAI 호환 API |

`MODEL`에는 OpenHands가 붙이는 `openrouter/` 또는 `nvidia_nim/` 접두사를 직접 넣지 않는다.
직접 SDK는 원래 모델 ID를 받고, OpenHands만 중앙 연결 객체가 계산한 adapter 이름을 받는다.

## 실행 경로

`app.llm_connection`이 provider에 맞는 최종 URL, 모델, header를 한 번 계산한다.

```text
루트 .env
  → app.llm_connection
  ├─ 직접 SDK (요구사항·설계·Testing)
  ├─ OpenHands (LiteLLM 모델 이름으로 변환)
  └─ subprocess/Docker runner (LLM_PROVIDER/API_KEY/BASE_URL/MODEL 전달)
```

하위 프로세스도 `LLM_PROVIDER`, `BASE_URL`, `MODEL`을 함께 받으므로 host와 다른 provider를
사용하지 않는다. Cloudflare를 선택했을 때만 계정 URL과 Gateway header를 조립한다. OpenRouter를
선택했는데 오래된 Cloudflare 환경변수가 남아 있어도 연결은 바뀌지 않는다.

## 비밀값과 실행 기록

`API_KEY`는 실제 요청을 만들 때만 사용한다. 실행 계획, manifest, Testing 계획·결과와 로그에는
provider·URL·모델만 기록하고 API key는 기록하지 않는다. 테스트는 실제 키 대신 가짜 값을 쓰며,
runner 명령에도 키 값 자체를 넣지 않고 환경변수 이름만 전달한다.

## 문제 해결 순서

1. `LLM_PROVIDER`가 네 지원 값 중 하나인지 확인한다.
2. `BASE_URL`이 선택 provider의 OpenAI 호환 API 주소인지 확인한다.
3. `MODEL`에 adapter 접두사를 중복으로 넣지 않았는지 확인한다.
4. OpenHands 구현 단계와 Testing이 같은 `.env`를 읽는지 확인한다.
5. API key가 누락되었으면 네트워크 호출 전에 설정 오류가 표시되는지 확인한다.

provider를 추가할 때에는 연결 객체의 명시적 표와 이 문서만 갱신한다. 단계별로 provider
환경변수를 새로 만들거나, 실패 시 다른 provider로 자동 전환하지 않는다.

## OpenRouter 실제 연결 확인

2026-09-05에 `openai/gpt-oss-120b`로 다음 경로를 각각 한 번 확인했다. API key는
출력하거나 실행 기록에 저장하지 않았다.

| 확인 경로 | 결과 | 관찰값 |
|---|---|---|
| 구조화 출력 | 통과 | 입력 76토큰, 출력 37토큰, 전체 4.41초, 첫 응답 조각 2.52초, 스키마 수리 0회 |
| OpenHands 파일 편집 | 통과 | `openrouter/openai/gpt-oss-120b`로 파일 조회 후 문자열 한 줄 수정, 전체 22.67초 |
| 수강신청 구현 묶음 | 중단 | `UC4·UC5` 복제본에서 두 대화가 각각 32회 한도에 도달했고 세 번째 대화가 시작되어 약 13분 뒤 중단 |

두 번째 확인은 시스템 임시 폴더에서 수행했다. OpenHands는 중앙 연결 객체가 만든 LiteLLM 모델
이름을 사용했고, 허용된 파일만 수정했다. 임시 작업 폴더는 대화가 끝난 뒤 삭제했다.

세 번째 확인도 원본 체크포인트를 바꾸지 않도록 시스템 임시 폴더에서 수행했다. provider 전송
오류나 429 응답은 없었지만, 두 번째 대화에서는 내용과 도구 호출이 모두 없는 응답도 반복됐다.
따라서 연결 통합은 정상으로 판단하지만 이 모델로 수강신청 전체 구현을 곧바로 실행하지는 않는다.
긴 구현 작업의 prompt와 도구 사용 횟수는 provider 전환과 분리하여 이후 모델 선별·구현 에이전트
개선에서 다룬다. 중단 뒤에는 복제한 run, OpenHands 작업 공간, profile 임시 폴더를 모두 삭제했다.
