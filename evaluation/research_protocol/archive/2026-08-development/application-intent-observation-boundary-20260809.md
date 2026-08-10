# 애플리케이션 의도–관측 경계

## 결정

요구사항에서 수락된 애플리케이션 상태 제약과 생성 코드에서 관측한 상태 구현을 서로
다른 fact로 유지한다.

- `intent.<열린 need ID>.state`: 요구사항 에이전트가 원문 근거와 함께 수락한 의도
- `observed.runtime.storage.*`: 소스·설정 관측기가 확인한 구현 사실

두 종류는 같은 `ApplicationRuntimeContract/v1`의 열린 fact 목록을 사용한다. 새
capability 이름이나 DB·CSP별 스키마 필드를 만들지 않는다.

## 외부 근거와 채택 범위

OASIS TOSCA 2.0은 requirement definition과 capability definition을 분리하고,
requirement를 충족하는 관계를 만든다. 후보 노드는 요구를 충족하는지 검증 대상이다.
Kubernetes도 사용자의 PVC 요청과 실제 PV 제공물을 별도 객체로 두고 control loop가
조건에 맞는 대상을 결합한다.

- [OASIS TOSCA 2.0 요구사항·capability](https://docs.oasis-open.org/tosca/TOSCA/v2.0/TOSCA-v2.0.html#_Toc164778704)
- [Kubernetes PersistentVolume과 Claim](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)

EasyDep은 이 “요구와 제공 사실의 분리”만 `adapted` 근거로 사용한다. TOSCA 문법이나
Kubernetes API를 구현하지 않으며 호환성을 주장하지 않는다.

## 최소 표현

deployment need의 이름은 LLM이 요구사항에 맞게 자유롭게 정한다. 명시적인 앱 상태
제약이 있을 때만 metadata에 다음 열린 객체를 둘 수 있다.

```json
{
  "applicationState": {
    "durability": "persistent",
    "accessScope": "node-filesystem",
    "accessPath": "/명시된/경로"
  }
}
```

세 값은 모두 선택적이며 원문에 명시된 값만 기록한다. 특히 `accessScope`는 로컬·노드·
VM 파일시스템이 명시됐을 때만 `node-filesystem`, 공유·외부 상태가 명시됐을 때만
`shared-service`로 둔다. 알 수 없는 값은 추측하지 않는다. 원문 요구사항 ID와 정확한
evidence span도 함께 전달한다.

LLM이 이 규칙을 어길 수 있으므로 소비 직전에 값별 근거 gate를 다시 적용한다. durability는
영속·휘발 의미가 evidence span에 있어야 하고, access scope는 노드/공유 위치 의미가 있어야
하며, access path는 정확한 경로가 span에 있어야 한다. 근거 없는 값은
`rejectedMetadata`에 이유와 함께 남기고 intent fact로 승격하지 않는다.

이 작은 축은 capability 사전을 고정하기 위한 것이 아니라 다음 판정의 입력이다.

1. 사용자가 노드 상태를 명시했고 다중 영역도 요구하면 구현 에이전트가 상태를 임의로
   외부화하지 않는다. 상태 또는 가용성 요구를 수정하도록 requirements 단계로 돌려보낸다.
2. 사용자는 상태 위치를 정하지 않았는데 생성 코드가 노드 상태를 선택했다면,
   외부화·복제 구현 또는 가용성 요구 수정 중 선택할 수 있다.
3. 생성 에이전트의 선언은 `intent.*` fact를 덮어쓸 수 없다.

## 검증 및 주장 한계

회귀시험은 다음을 확인한다.

- 임의 이름의 열린 need에서도 명시적 `applicationState`만 intent fact로 투영한다.
- 관련 없는 metadata는 앱 상태로 오인하지 않는다.
- 요구사항 소유 node scope와 multi-zone의 충돌은 두 요구사항 수정 대안만 낸다.
- 허용되지 않은 구현단 externalize 응답은 LLM을 다시 호출하거나 파일을 바꾸지 않는다.
- 상태 요구 수정은 같은 run에서 requirements 단계부터 다시 수행한다.

현재 구현은 상태 축 하나의 최소 경계다. 포트·보안·성능까지 같은 metadata 관례를
확장하지 않는다. 새 축은 실제 교차 산출물 판정 소비자와 근거가 생길 때만 추가한다.
자연어 추출 정확도와 최종 앱 기능 성공은 별도 실측이 필요하다.

## 2026-08-09 실제 LLM 대조

기존 요구사항 에이전트의 다표본 추출 경로로 두 입력을 측정했다.

| 입력 | 벽시계 시간 | LLM 제안 | 값별 gate 이후 |
|---|---:|---|---|
| “VM filesystem에 상태 저장” 명시 | 26.451초 | persistent + node-filesystem | 두 값 보존, HA 충돌 질문 생성 |
| “재시작 간 상태 영속”만 명시 | 30.002초 | persistent + node-filesystem | persistent만 보존, 근거 없는 node-filesystem 거부 |

최초 실행에서는 대조 입력의 거짓 scope가 그대로 intent가 되는 결함을 확인했다. 값별 gate를
추가한 뒤 같은 대조를 다시 실행하여 `explicitNodeScopeExtracted`,
`unspecifiedScopeNotInvented`, `explicitConflictQuestioned`가 모두 참임을 확인했다.

- 최초 결함 증거: [application-intent-pilot-20260809-attempt-1.json](../../measurements/2026-08-development/application-intent-pilot-20260809-attempt-1.json)
- 보완 후 결과: [application-intent-pilot-20260809.json](../../measurements/2026-08-development/application-intent-pilot-20260809.json)
- 재현 실행기: [run_application_intent_pilot.py](../../run_application_intent_pilot.py)

이는 두 문장의 개발 대조 결과이며 자연어 일반화 성공률은 아니다. 다만 “LLM이 제안했으니
근거 있음”으로 취급하지 않고, 실제 소비되는 값마다 원문 근거를 요구하는 경계가 작동함을
보여준다.
