# 백엔드 코드 길잡이

`app`은 EasyDep 백엔드의 실제 업무 코드를 모아 둔 디렉터리다. 처음 코드를 읽는다면
파일을 알파벳순으로 훑기보다 아래 실행 흐름을 먼저 이해하는 편이 쉽다.

```text
브라우저
  → workspace: 사용자의 명령을 접수하고 진행 상황을 보여 줌
  → requirements: 자연어를 구조화된 요구사항으로 바꿈
  → design: 요구사항을 클래스, 호출 순서, 통신 규칙, 데이터베이스, 배포 설계로 바꿈
  → implementation: 설계를 소스 코드와 배포 파일로 바꿈
  → testing: 생성된 결과를 정적·동적으로 검사함

모든 단계의 바깥쪽
  ├─ workspace: 프론트엔드 명령으로 여러 단계를 연결하고 중단된 지점부터 다시 시작함
  ├─ repositories/db: 산출물·명령·이벤트·체크포인트를 저장함
  ├─ cloudkb: 클라우드 선택에 필요한 사실과 규칙을 제공함
  └─ metrics: LLM(대규모 언어 모델)의 호출 시간과 실행 기록을 수집함
```

## 디렉터리별 책임

| 디렉터리 | 초보자를 위한 한 줄 설명 | 자세한 문서 |
|---|---|---|
| `requirements` | 사용자의 말을 누락 없는 요구사항 모델로 정리한다. | [요구사항](requirements/README.md) |
| `design` | 요구사항을 구현 전에 검토할 수 있는 설계 계약으로 바꾼다. | [설계](design/README.md) |
| `implementation` | 설계 계약을 실행 가능한 애플리케이션과 배포 산출물로 바꾼다. | [구현](implementation/README.md) |
| `testing` | 생성된 애플리케이션이 계약과 실행 환경을 만족하는지 검사한다. | [테스팅](testing/README.md) |
| `workspace` | 대화형 UI의 명령·진행 이벤트·자동 진행을 조율한다. | [워크스페이스](workspace/README.md) |
| `repositories` | 업무 코드가 저장 기술을 직접 알지 않도록 저장소 API를 제공한다. | [저장소](repositories/README.md) |
| `db` | MySQL 연결, 테이블 구조와 작업 중간 상태 저장을 담당한다. | [데이터베이스](db/README.md) |
| `cloudkb` | AWS·Azure·GCP의 자원, 지역, 가격과 연결 규칙을 제공한다. | [클라우드 지식](cloudkb/README.md) |
| `metrics` | LLM 호출이 멈춘 것처럼 보일 때 원인을 찾을 기록을 제공한다. | [계측](metrics/README.md) |

## 의존성 방향

업무 단계는 자신보다 바깥의 실행 조정 계층을 역으로 호출하지 않는다. 예를 들어 설계
서비스는 워크스페이스 명령이나 MySQL 행을 직접 읽지 않는다. 필요한 값은 함수 인자로 받고,
결과는 정해진 데이터 형식으로 돌려준다.

```text
HTTP/UI → workspace → 단계 공개 API → 단계 내부 서비스
              ↓
        repositories/db

cloudkb → requirements/design/implementation에서 읽을 수 있음
cloudkb ─X→ requirements/design/implementation 내부를 import하면 안 됨
```

이 규칙을 지키면 저장 방식이나 UI가 바뀌어도 요구사항·설계 알고리즘을 다시 작성하지 않아도
된다. 경계를 어기는 import는 `tests/test_package_boundaries.py`에서 검사한다.

## 오류를 읽는 순서

1. 워크스페이스 command의 `stage`, `status`, `error`를 확인한다.
2. 같은 `command_id`를 가진 workspace event에서 마지막 정상 단계를 찾는다.
3. 구현·테스트 단계라면 job 디렉터리의 상태 JSON과 `reports/`를 확인한다.
4. LLM 응답 오류인지, validator가 찾은 데이터 오류인지 구분한다.
5. 이미 저장된 산출물이 정상이라면 전체를 다시 시작하지 말고 실패한 단계부터 재개한다.

## 코드를 수정할 때

- 새 업무 규칙은 그 규칙을 소유한 단계에 둔다. 편하다는 이유로 `workspace`에 넣지 않는다.
- LLM 응답은 Pydantic schema로 확인한 뒤에만 저장하거나 다음 단계로 넘긴다.
- 내부 데이터를 PlantUML이나 OpenAPI 문서로 바꾸는 코드는 결정론적이어야 한다. 즉 같은
  입력을 받으면 항상 같은 문서를 만들어야 한다.
- 사람에게 보이는 설명·문서·주석은 한국어로 작성한다. API 필드, 규칙 ID, 로그 검색용
  식별자는 기존 영어 계약을 유지한다.
- 주석에는 코드가 그대로 말하는 동작보다 그 동작이 필요한 이유와 실패 시 의미를 적는다.
