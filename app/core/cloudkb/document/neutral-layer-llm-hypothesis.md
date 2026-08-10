# LLM 단계적 CSP 구체화: 기대, 위험, 검증 가설

## 결론의 상태

`사용자 의도 → 중립 계층 → CSP native graph → IaC`가 직접 IaC 생성보다 낫다는 것은
현재 EasyDep의 **설계 가설**이지 확정 사실이 아니다. 계획 분리와 구조화된 중간 표현이
코드 생성·조합 일반화를 개선한 연구는 있지만, CSP 중립 ontology를 거치는 방식 자체를
동일 조건에서 검증한 직접 증거는 부족하다.

따라서 중립 계층의 채택 이유를 “LLM을 더 똑똑하게 만든다”로 두지 않는다. 현재 기대하는
일차 가치는 의도 보존 계약, CSP별 차이의 설명, 지원 불가·부분 매핑의 명시, 결과 검증과
감사 가능성이다. 생성 정확도 개선 여부는 별도 통제 실험으로 판정한다.

## 연구가 지지하는 범위

- 계획 후 구현하는 코드 생성은 직접 생성보다 나아질 수 있다. Self-planning 연구는 여러
  코드 생성 benchmark에서 직접 생성 대비 Pass@1의 상대 개선을 보고했다.
- 작은 하위 문제부터 푸는 least-to-most 방식과 semantic tag, grammar, tree 같은 구조적
  중간 표현은 일부 compositional split에서 큰 개선을 보였다.
- 그러나 중간 언어 비교 연구에서는 모든 모델·대상 언어에 공통으로 우월한 형식 표현이
  없었고, 중간 답의 정확성과 최종 코드 정확성 사이 상관도 약했다.
- 외부 knowledge graph로 grounding한 모델도 새로운 관계 길이와 구성요소 조합에서
  어려움을 보였다. 구조화 지식을 제공하는 것만으로 안정적인 합성이 보장되지 않는다.
- IaC 연구가 직접 지지하는 것은 최신 provider 지식의 retrieval, schema 적합성 검사,
  `validate → plan → policy → runtime` 피드백이다. 이는 중립 계층과 별도로 필요하다.

## 기대하는 효과

1. 사용자의 기능·운영 의도와 CSP 선택을 분리한다.
2. 동일 의도가 CSP마다 어떤 native 자원 묶음과 lifecycle로 실현되는지 추적한다.
3. `partial`, `composite`, `unmatched`를 통해 의미 손실과 지원 불가를 숨기지 않는다.
4. 생성된 IaC가 원래 의도와 동결된 native evidence를 보존하는지 검사할 좌표를 제공한다.
5. CSP 변경 시 재사용 가능한 의도와 다시 결정해야 하는 provider-specific 의미를 나눈다.

## 예상 위험

### 정보 병목

중립 모델이 native cardinality, lifecycle, default 생성, attachment/configuration 차이를 담지
못하면 뒤 단계는 잃어버린 정보를 신뢰성 있게 복원할 수 없다. 공통분모만 남기는 것은
중립성이 아니라 정보 삭제일 수 있다.

### 오류 전파와 거짓 확신

잘못된 중립 계획이 다음 단계의 입력으로 고정되면 직접 생성보다 오류가 오래 살아남는다.
형식에 맞는 graph라는 사실은 의미가 맞다는 증거가 아니다. 각 단계는 독립 검증되고 실패 시
중단하거나 명시적으로 abstain해야 한다.

### 거짓 이식성

이름이 비슷한 자원을 `equivalent`로 취급하면 CSP별 기능·소유권·삭제 동작의 차이를 숨긴다.
provider extension을 공통 개념에 억지로 편입하지 않고 `partial/composite/unmatched`로
보존해야 한다.

### 오래된 지식과 schema 환각

중립 지식은 최신 provider resource/property를 대체하지 않는다. 구체화 단계에는 고정된
native graph와 현재 provider schema가 모두 필요하며, 존재하지 않는 property와 폐기 API는
결정론적 검증기로 차단해야 한다.

### 비용과 지연

단계가 늘면 LLM 호출, token, latency와 실패 지점도 늘어난다. 정확도 개선 없이 설명 파일만
늘어나는 경우 전체 방식은 실패로 판정한다.

### 특정 시나리오 과적합

P1~P3에서 필요한 자원만 중립 개념으로 만들면 holdout과 provider-specific 요청에서 무너질
수 있다. ontology 동결 전에는 P1~P3를 입력으로 쓰지 않고, 평가 때 native graph 전체의
node·edge coverage를 별도로 검사한다.

## 검증할 가설

- **H1 의도 보존:** 중립 계층 방식이 직접 생성보다 사용자 의도 위반률을 낮춘다.
- **H2 native 정확성:** 중립 계층 방식이 필수 의존성 누락과 존재하지 않는 property 생성을
  줄인다.
- **H3 조합 일반화:** 개발 사례에 없던 자원 조합과 provider extension에서 성능 저하가 작다.
- **H4 이식성:** 동일 의도의 CSP 변경 시 수정 범위와 의미 손실을 더 정확히 보고한다.
- **H5 비용:** 위 개선이 추가 token, 시간, 재시도 비용을 정당화한다.

다음 반례 중 하나라도 관측되면 “항상 중립 계층을 거친다”는 정책은 기각한다.

- 단일 CSP의 provider-specific 요청에서 직접 생성보다 유의하게 나쁘다.
- `validate/plan`은 통과하지만 원래 의도 충족률이 낮다.
- 중립 단계 오류가 반복 수정 뒤에도 최종 결과에 남는다.
- 품질 차이 없이 비용·시간만 증가한다.

## 사전 등록할 비교 실험

동일 모델, temperature, seed 집합, token/시간 한도, provider schema snapshot과 verifier를
세 arm에 동일하게 적용한다.

| Arm | 경로 | 중립 지식 |
|---|---|---|
| A direct | 요구사항 → CSP IaC | 없음 |
| B planned | 요구사항 → 자연어 계획 → CSP IaC | 없음 |
| C neutral | 요구사항 → typed neutral intent → native realization → CSP IaC | 있음 |

개발 사례와 holdout을 분리하고 최소한 다음 사례군을 포함한다.

- 세 CSP에 공통으로 실현되는 기본 VM 구성
- CSP마다 resource/configuration 경계가 다른 구성
- 한 CSP에만 있는 provider extension
- `partial`, `composite`, `unmatched`가 필요한 요청
- 보지 못한 native 구성요소 조합
- 의도적으로 모순되거나 지원 불가능하여 거절해야 하는 요청

일차 지표는 semantic intent 충족률, 배포 성공률, dependency precision/recall,
unsupported-property 비율이다. 이차 지표는 `validate/plan/policy/runtime` 단계별 실패,
의미 손실 보고 정확도, 수정 횟수, token, wall time과 비용이다. 결과가 없는 실패와 거절을
성공으로 재분류하지 않는다.

## 구현 원칙

중립 계층은 LLM에 넣는 긴 설명문이 아니라 작은 typed contract로 전달한다. 요청과 관련된
개념·관계·반례만 retrieval하며, CSP 구체화 결과는 native ID와 근거 hash를 가져야 한다.
LLM은 native graph나 Terraform dependency를 자유롭게 재정의하지 않고 후보를 제안한다.
최종 판정은 schema, graph coverage, Terraform validate/plan, policy와 runtime oracle이 한다.

실험에서 C가 우월하지 않으면 중립 계층은 비교·설명·감사용으로만 유지하고, 생성 경로는
direct 또는 planned 방식을 선택할 수 있어야 한다.

## 관련 연구

- [Self-planning Code Generation with Large Language Models](https://arxiv.org/abs/2303.06689)
- [Least-to-Most Prompting Enables Complex Reasoning](https://arxiv.org/abs/2205.10625)
- [Assessing Code Generation with Intermediate Languages](https://arxiv.org/abs/2407.05411)
- [Compositional Generalization via Semantic Tagging](https://aclanthology.org/2021.findings-emnlp.88/)
- [Grammar-based Decoding for Improved Compositional Generalization](https://aclanthology.org/2023.findings-acl.91/)
- [Compositional Generalization with Grounded Language Models](https://aclanthology.org/2024.findings-acl.205/)
- [IaC-Eval, NeurIPS 2024](https://papers.nips.cc/paper_files/paper/2024/hash/f26b29298ae8acd94bd7e839688e329b-Abstract-Datasets_and_Benchmarks_Track.html)
- [SWE-InfraBench](https://arxiv.org/abs/2606.05249)
- [Verifier-First Evaluation of Agentic LLMs for IaC](https://arxiv.org/abs/2607.20478)

