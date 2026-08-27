# Native review round 2

Round 1에서 발견된 Azure/GCP request-wrapper 결함을 수정하고 새 inventory를 만든 뒤, 이전
리뷰를 보지 않은 두 AI 리뷰어가 다시 전수 판정했다.

| CSP | A node 포함/제외 | B node 포함/제외 | 합의 node 포함/제외/충돌 | 합의 relation 포함/제외/충돌 |
|---|---:|---:|---:|---:|
| AWS | 42 / 81 | 32 / 91 | 28 / 81 / 14 | 7 / 193 / 42 |
| Azure | 28 / 36 | 22 / 42 | 15 / 35 / 14 | 0 / 145 / 0 |
| GCP | 51 / 54 | 38 / 67 | 15 / 48 / 42 | 0 / 172 / 46 |

두 입력은 모두 `validate_review(require_complete=True)`를 통과했다. 합의 파일은 충돌을
`unreviewed`, `humanReviewRequired=true`로 보존하므로 아직 동결할 수 없다.

## 해석

- GCP property traversal은 Round 1의 관계 공백을 실제 후보로 바꾸었다. 다만 두 리뷰어가
  relation kind나 target을 다르게 판정한 항목은 자동 합의하지 않았다.
- Azure에서는 두 리뷰어 모두 145개 후보를 제외했다. `SubResource`나 일반 `arm-id`가 다른
  자원의 ID를 담는다는 형태는 보이지만, pinned schema만으로 고유 target type과 실제
  provisioning 관계를 증명할 수 없는 경우가 대부분이었다.
- AWS는 inventory revision이 바뀌지 않았어도 이전 판정을 복사하지 않고 다시 검토했다.

Azure의 0개 관계와 GCP의 0개 자동 합의 관계는 “관계가 없다”는 결론이 아니다. 전자는
target-resolution/evidence 공백이고, 후자는 독립 판정 불일치다. 다음 단계는 충돌에 대한
사람 검토와, 선택된 미확인 관계에 대한 CSP 제어면 실증이다.
