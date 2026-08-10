# Native v2 공식 관측 입력

> 현재 판정은 사람의 전수 투표가 아니라 공식 근거 규칙을 사용한다. 아래 검토·합의 절차는
> 초기 방법의 감사 기록이며 확인적 모델 동결의 전제조건이 아니다. 서로 충돌하는 고영향
> 공식 근거가 생긴 예외에만 사람 검토를 다시 연다.

이 디렉터리의 `*-observations.json`은 리소스 유형을 미리 붙이지 않은 control-plane
원시 관측이다. `*-boundary-sample.json`은 seed 42로 만든 층화 경계 표본이며,
`*-review-scope.json`은 결정 앵커에서 도달한 관측과 경계 표본의 합집합이다.

각 관측은 다음 11개 지표를 같은 필드로 가진다: 식별 필드, CRUD 연산, 부모 경로,
독립 조회 가능성, 부모 갱신 뒤 존속, 분리 가능성, 독립 삭제 가능성, 수명주기 소유자,
내장 대상, 공급자 자동 생성 여부, 연결 관리자다. 공식 API 모델에서 직접 확인하지 못한
값은 `false`가 아니라 `null`로 둔다. 이 값은 후속 행동실험이나 검토 근거 없이는
채우지 않는다.

현재 검토 범위는 AWS 152개, Azure 309개, GCP 152개다. 공급자 간 숫자를 같게 맞추지
않으며, 공식 스키마의 연결 구조와 층별 모집단 크기에 따라 결정한다. 모집단은 Docker-on-VM
개발 사례의 결정 앵커가 속한 공식 서비스 모델·계열의 전체 연산으로 제한한다.

아래 전수 검토 절차와 빈 양식은 초기 설계의 감사 자료로 보존한다. 현재 프로토콜에서는
필수 동결 입력이 아니며, 공식 근거 규칙이 충돌한 고영향 예외에만 사람이 개입한다.

두 검토자가 과거 방식으로 같은 `review-scope`를 사용할 경우 다음을 판정한다.

1. 관측이 독립적으로 프로비저닝·참조·삭제되거나 다른 관측의 배포 결과를 바꾸는가
2. 포함한다면 원시 사실에서 귀납적으로 어떤 유형 이름을 부여할 수 있는가
3. 판정 이유와 공식 모델 위치가 무엇인가

`native_inventory` 명령의 `--reviewers` 옵션은 빈 검토 양식을 만들 뿐 판정을 대신하지
않는다. κ와 Krippendorff α가 각각 0.70 이상이고 모든 불일치를 합의한 뒤에만
`*-model.json`을 만들 수 있다. 현재 디렉터리에 동결 모델이 없는 것은 검토가 아직
끝나지 않았다는 뜻이며 준비도 검사가 이를 차단한다.

두 독립 검토와 합의 파일을 받은 뒤에는 다음 명령으로 동결한다. 합의 파일은 불일치한
`nativeId`를 키로 하고 `included`, `derivedTypes`, `reason`을 담은 JSON 객체다.

```powershell
python -m evaluation.research_protocol.commands.native_review `
  aws-observations.json aws-review-scope.json `
  aws-review-a.json aws-review-b.json aws-adjudications.json aws-model.json
```
