# 리소스 의존성 판정 감사

이 감사는 LLM 또는 비전문가의 직관을 정답으로 사용하지 않는다. 판정 근거의 우선순위는 실제
provider 동작, Terraform provider schema와 검증, CSP 공식 문서, 실측 DepKB claim, LLM 해석
순이다. 근거가 부족한 항목은 오답으로 강제하지 않고 `insufficient-evidence`로 남긴다.

`selection.json`은 정정된 v3 실험의 무수정 기준군에서 CSP별 그래프 통과·실패 셀을 하나씩
결정론적으로 고른 결과다. 각 증거 카드는 요구사항, 생성 Terraform, 추출 그래프, 오라클,
불일치, 공식 근거와 잠정 판정을 함께 기록한다.

한 셀에는 독립적인 결함이 함께 있을 수 있으므로 다음 판정을 복수로 부여할 수 있다.

- `confirmed-model-error`: 생성 결과가 요구사항 또는 확인된 CSP 관계를 위반했다.
- `confirmed-oracle-error`: 오라클이 필수가 아닌 관계를 강제하거나 허용 대안을 빠뜨렸다.
- `confirmed-analyzer-error`: Terraform에 있는 관계를 추출하지 못했거나 잘못 추출했다.
- `invalid-iac`: 생성된 IaC가 provider schema, validate 또는 plan 단계에서 유효하지 않다.
- `acceptable-alternative`: 오라클과 다르지만 요구사항을 만족하는 CSP 구성이다.
- `insufficient-evidence`: 현재 근거로 책임 소재를 확정할 수 없다.

각 카드는 두 검토자가 독립 판정한다. 판정이 다르면 합의 기록 없이는 완료된 카드로
취급하지 않는다. 카드 형식은 `card-template.json`, 검증 규칙은 `classification.py`에 있다.
