# 요구사항 분석 에이전트 개선 보고서

## 1. 목적

단일 voucher 앱의 결과만 보고 프롬프트를 수정하면 특정 문장과 도메인에 과적합될 수 있다.
따라서 Docker-on-VM 범위 안에서 액터·트리거·외부 연동·상태·부하 특성이 다른 앱들을 개발
세트와 홀드아웃으로 나누고, 개발 세트의 macro 결과로만 개선안을 선택했다.

액터 역할 모델은 이번 작업으로 동결한다. 현재 모델은 액터에 전역 `kind`를 두지 않고 각
유스케이스에 `primary_actor`와 `supporting_actors`를 둔다. 추가 역할 확장은 이번 범위에서
진행하지 않는다.

## 2. 평가 구성

### 개발 세트

- Stateless conversion: 외부 서비스와 영속 상태가 없는 최소 사례
- Checkout gateway: 결제·환불 시 외부 서비스가 supporting인 사례
- Notification delivery: 시간 트리거, 결과 수신자, 메시지 제공자를 구분하는 사례
- IoT monitoring: 비인간 primary actor와 이벤트 기반 알림 사례

### 홀드아웃

- Telehealth: 영상·결제·처방 전달
- Logistics: 같은 Carrier가 UC에 따라 primary와 supporting을 오가는 사례
- Partner reporting: 시간 기반 작업, 외부 시세 서비스, webhook 수신자

입력에는 요구사항과 클라우드 제약만 둔다. 기대 액터·역할은 `oracle.json`에 분리하여 에이전트
프롬프트로 전달되지 않게 했다. 홀드아웃 입력은 SHA-256으로 동결했다.

## 3. 기준선에서 발견한 문제

개발 세트 4개에서 FR coverage와 명시적 액터 역할은 정확했지만 UC 수가 증가할수록 호출 수와
토큰이 빠르게 증가했다. 주요 원인은 UC별 fully-dressed 명세 생성, 의미 검증, 전체 재생성,
재검증이었다.

코드 검토 결과 repair 프롬프트는 “이전 출력의 올바른 부분을 유지하라”고 지시하면서 실제
이전 명세를 전달하지 않았다. 따라서 모델은 결함을 국소적으로 고치지 못하고 매번 명세 전체를
다시 추측했다. 또한 결정론적으로 발견 가능한 구조 오류가 있어도 의미 검증 LLM을 먼저 호출했다.

## 4. 적용한 개선

1. repair 호출에 직전 명세의 구조화 JSON을 함께 전달했다.
2. 정적 검증을 먼저 실행하고, 구조 오류가 없을 때만 의미 검증 LLM을 호출하도록 변경했다.
3. 실행시간 비교를 위해 `wall_seconds`를 manifest metrics에 저장했다.
4. 앱별 결과 대신 개발 세트의 호출·토큰·시간·잔여 이슈 macro 합계로 변경 채택 여부를 정했다.

MetaGPT의 역할/SOP 구조에서 참고할 수 있는 핵심도 동일하다. 다음 역할 또는 검토 단계에는
자유로운 대화 이력이 아니라 필요한 중간 산출물을 명시적으로 전달해야 한다. 이번 개선은 새
에이전트를 늘리지 않고 기존 repair 단계에 실제 산출물을 전달하는 방식으로 적용했다.

## 5. 결과

| 지표 | 개선 전 | 개선 후 | 변화 |
|---|---:|---:|---:|
| LLM 호출 | 100 | 92 | -8.0% |
| 전체 토큰 | 287,137 | 260,942 | -9.1% |
| wall time | 430.6초 | 338.0초 | -21.5% |
| 잔여 명세 이슈 | 10 | 7 | -30.0% |
| FR coverage | 1.0 | 1.0 | 유지 |
| actor recall | 1.0 | 1.0 | 유지 |
| 명시적 역할 정확도 | 1.0 | 1.0 | 유지 |

Notification 앱은 잔여 이슈가 2개에서 3개로 증가했다. 그러나 나머지 세 앱과 macro 비용·품질이
개선되어 변경을 채택했으며, 해당 앱 문장을 겨냥한 예외 규칙은 추가하지 않았다.

선택한 변경을 동결한 후 홀드아웃을 한 번 실행했다. Logistics와 Partner reporting의 역할
정확도는 1.0, Telehealth는 0.67이었다. Telehealth의 Pharmacy 사례는 현재 두 역할만으로 표현하기
어려운 한계를 보였지만, 홀드아웃 결과에 맞춘 후속 수정은 하지 않았다.

세부 앱별 수치와 artifact ID는 `results.md`에 기록한다.

## 6. 재실행 방법

저장소 루트에서 실행한다.

```powershell
# 개발 세트: 개선 중 반복 실행 가능
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split development

# 홀드아웃: 개선안 선택 후에만 실행; 실행 전 해시를 자동 검증
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split holdout

# 직접 추가한 도메인 확장 세트
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split domainExpansion
```

각 실행은 `artifacts/run_*`에 산출물을 저장하고 앱별 점수와 macro 합계를 콘솔에 출력한다.

## 7. 도메인 확장 방법

1. `templates/application.json`을 복사하여 `inputs/`에 새 영어 입력을 만든다.
2. `suite.json`의 `domainExpansion`에 상대 경로를 추가한다.
3. 채점이 필요하면 `templates/oracle-entry.json` 형식으로 `oracle.json`에 항목을 추가한다.
4. 프롬프트에는 새 앱의 이름·액터·문장을 복사하지 않는다.
5. 최소 세 개 이상의 서로 다른 도메인을 모은 뒤 macro 결과로 판단한다.

권장 확장 축은 파일 저장/대용량 처리, 인증 제공자, 외부 검색 API, 예약·배치, 사람이 서비스를
제공하는 외부 조직, 장애 복구 작업이다. VM/Docker 범위를 벗어난 Kubernetes 전용 요구는 넣지 않는다.
