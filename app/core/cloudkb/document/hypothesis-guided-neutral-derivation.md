# 중립 가설 기반 CSP 교차검증

## 전환 이유

Native inventory 전체를 먼저 판정하는 두 번의 독립 리뷰는 schema 잡음과 실제 제품 지식의
비율이 낮다는 사실을 드러냈다. 두 번째 round에서도 AWS 56건, Azure 14건, GCP 88건의
사람 검토 충돌이 남았다. 특히 Azure의 일반 `SubResource`/`arm-id`는 target type을 충분히
말하지 않아, 전수 판정만 반복해도 관계 지식이 늘지 않았다.

따라서 현재 방법은 native-first 전수 분류에서 **hypothesis-guided, evidence-validated**
방식으로 전환한다. Cloud-Barista, TOSCA, OCCI는 정답 ontology가 아니라 조사할 개념과
관계를 제안하는 가설 생성원이다. 최종 CSP 사실은 여전히 CSP native source와 제어면 실험이
결정한다.

## 절차

1. 세 중립 모델을 서로 독립적으로 읽고 VM-connected IaaS 후보를 추출한다.
2. 세 모델 사이에서 이름이 아니라 정의·기능 효과·lifecycle·관계 방향을 비교한다.
3. 합쳐진 후보마다 AWS, Azure, GCP native 구성요소를 찾는다.
4. CSP별 대응을 `equivalent`, `partial`, `composite`, `unmatched`로 판정한다.
5. `partial/composite/unmatched`와 필수 관계에만 우선적으로 제어면 실험을 배정한다.
6. 중립 모델에 선택되지 않은 native inventory를 층화 표본 감사해 누락과 provider
   extension을 찾는다.
7. 검증된 후보와 extension만 alignment에 넣고 동결한다.

## 편향 통제

- 외부 중립 모델에 등장한다는 사실은 CSP 존재·필수성·동등성의 증거가 아니다.
- 세 모델 후보 추출 중에는 AWS/Azure/GCP projection과 P1~P3를 보지 않는다.
- 한 CSP에서 여러 resource/configuration으로 실현되면 `composite`로 보존한다.
- 공통분모로 줄이면서 lifecycle, cardinality, ownership이 사라지면 `partial`로 기록한다.
- 중립 모델에 없는 native 요소를 버리지 않고 표본 감사와 provider extension 경로를 둔다.
- P1~P3는 alignment 동결 뒤 평가에만 사용한다.

## 기존 리뷰의 상태

Round 1·2 결과와 권고 보고서는 실패 기록이 아니라 native evidence의 감사 자료로 보존한다.
다만 158개 충돌을 전부 해결하는 작업은 중단한다. 중립 후보의 CSP 교차검증에 실제로 필요한
충돌만 다시 열어 사람 판정 또는 실증으로 해결한다. 사용하지 않은 충돌을 자동 승인하거나
동결 graph에 포함하지 않는다.

## 완료 조건

- 세 중립 source packet이 등록된 고정 출처와 source locator로 재현된다.
- 합쳐진 각 후보에 세 CSP mapping 또는 명시적 `unmatched`가 있다.
- 의미 손실과 composite 구성요소가 기록된다.
- 선택 밖 native 표본 감사에서 새로운 중요 개념이 나오지 않거나 extension으로 추가된다.
- 실행에 쓰는 관계는 native source와 필요한 제어면 실증을 통과한다.
- 그 뒤에만 LLM direct/planned/neutral 비교 실험의 C arm을 연다.

