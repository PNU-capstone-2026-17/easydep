# CSP 실증 기반 중립 모델 도출 절차

이 문서는 DepKB v3의 연구·빌드 절차를 고정한다. 중립 자원 목록을 먼저 만들고 CSP 용어를
끼워 맞추지 않는다. AWS, Azure, GCP의 native graph를 서로 독립적으로 완성한 뒤에만 교차
CSP 개념을 도출한다.

## 발견 경계

발견 입력은 고정된 AWS CloudFormation specification, Azure REST API specification,
Google Compute discovery document다. 기존 DepKB claim과 vocabulary, P1~P3 시나리오는 발견
입력으로 사용하지 않는다. 범위와 중단 기준은 `native/discovery-protocol.json`에 고정한다.

```powershell
python -m app.core.cloudkb.depkb.native.study discover
python -m app.core.cloudkb.depkb.native.study prepare-reviews
```

`*-inventory.json`은 고재현율 자동 추출 결과이지 연구 범위에 포함됐다는 뜻이 아니다.
두 리뷰어는 `*-review-a.json`과 `*-review-b.json`을 서로 보지 않고 판정한다. 모든 node와
관계 후보에는 공식 source locator, 포함·제외 사유, 관측 가능한 VM 연결 기준이 필요하다.

리뷰가 모두 끝나면 서로 다른 리뷰어 식별자로 합의 결과를 만든다.

```powershell
python -m app.core.cloudkb.depkb.native.study reconcile reviewer-a reviewer-b
python -m app.core.cloudkb.depkb.native.study status
```

판정 상태와 관계 종류·대상이 일치할 때만 자동 합의한다. 설명 문구와 source locator는
합쳐 보존한다. 불일치는 `humanReviewRequired=true`인 conflict로 남고 해당 항목은
`unreviewed`가 되므로 동결할 수 없다. 사람이 근거를 검토해 두 원본 리뷰를 수정한 뒤 다시
합의해야 한다. 동일한 리뷰어가 양쪽 입력을 제공할 수도 없다.

```powershell
python -m app.core.cloudkb.depkb.native.study freeze
```

동결 graph에는 두 입력의 digest와 리뷰어, `independentAgreement=true`,
`p1P2P3UsedDuringDiscovery=false`가 기록된다.

## 실증

Schema reference는 관계 후보이지 필수 관계의 증명이 아니다. 포함 후보마다 정상 구성,
생략·오조합, 연결 중 삭제, 분리 뒤 삭제 같은 runtime 제거·복구 신호를 사전에 정의한다.
기대 결과를 실행 전에 동결하고 각 CSP에서 순차 실행한다. 종료 시 실험 자원을 정리하고
잔존 자원 0건을 확인한다.

제품 계획에는 `confirmed + replicated` 관계만 들어간다. pending, failed, inconclusive,
conflicting 관계는 `unavailableFindings` 또는 `unmeasured`로 노출한다.

## 중립 계층 도출

세 native graph가 동결된 다음에만 기능 효과, 관계 방향, resource/configuration 형태,
lifecycle, cardinality, provider-created/defaulted 동작과 runtime failure signal을 비교한다.
매핑은 `equivalent`, `partial`, `composite`, `unmatched` 중 하나이며 일부 CSP에만 존재하는
개념도 지우지 않고 provider extension으로 보존한다.

모든 도출 개념은 고정된 Cloud-Barista, OASIS TOSCA, OGF OCCI 1차 자료와 각각 교차
검증한다. 출처 좌표는 `neutral-model-sources.json`에 있으며 등록되지 않은 URL이나 이동하는
브랜치 이름은 alignment 근거로 쓸 수 없다. 외부 모델은 CSP 사실의 근거가 아니라 추상화
경계의 반례·교차검증 자료다.

## 평가 격리

중립 alignment까지 동결한 다음에만 P1~P3와 종단 Terraform을 평가한다. P1~P3 밖에서도
동결 graph의 모든 node·edge를 대상으로 구조 질의를 생성해 특정 시나리오 과적합을 검사한다.
평가 실패 때문에 모델을 수정하면 새 version과 hash를 만들며 기존 결과를 덮어쓰지 않는다.

현재 v3는 발견·리뷰·동결·alignment 도구 체계이며 native review와 실증이 끝나지 않았다.
따라서 기존 v2 런타임을 자동 대체하지 않는다.

LLM 생성에서 이 중립 계층을 거치는 것이 실제로 유리한지는 별도 가설이다. 기대효과,
정보 병목·오류 전파 위험과 A/B/C 실험 기준은
[`neutral-layer-llm-hypothesis.md`](neutral-layer-llm-hypothesis.md)에 고정한다.

두 차례 native 전수 리뷰 이후에는 중립 모델을 가설 생성원으로 먼저 사용하는 방식으로
조사 순서를 조정했다. 전환 이유와 편향 통제는
[`hypothesis-guided-neutral-derivation.md`](hypothesis-guided-neutral-derivation.md)에 기록한다.
