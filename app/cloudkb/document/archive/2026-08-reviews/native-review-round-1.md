# Native review round 1

두 AI 리뷰어는 서로의 결과, 기존 중립 vocabulary·claim, P1~P3를 보지 않고 고정된 native
inventory와 source locator만 판정했다. 두 리뷰 모두 `validate_review(require_complete=True)`를
통과한 뒤 합의기를 실행했다.

| CSP | A node 포함/제외 | B node 포함/제외 | 합의 node 포함/제외/충돌 | 합의 relation 포함/제외/충돌 |
|---|---:|---:|---:|---:|
| AWS | 39 / 84 | 55 / 68 | 31 / 27 / 65 | 41 / 164 / 37 |
| Azure | 30 / 34 | 44 / 20 | 18 / 1 / 45 | 0 / 64 / 0 |
| GCP | 69 / 36 | 71 / 34 | 49 / 10 / 46 | 0 / 101 / 0 |

모든 충돌은 `humanReviewRequired=true`로 보존했으며 native graph 동결은 차단된다. 포함 수의
차이는 한 AI의 반복 판정을 독립 리뷰로 가장하지 않았음을 보여주지만, 합의 자체가 사실의
충분조건은 아니다.

## 두 리뷰어가 독립적으로 발견한 extraction 결함

Azure와 GCP의 relation 후보는 대부분 API operation의 request body schema가 해당 자원
schema를 가리키는 참조였다. 이는 resource-to-resource dependency가 아니다. 두 리뷰어 모두
관계를 만들어내지 않고 전부 제외했다.

GCP inventory에는 176개 candidate row가 있지만 candidate identity 기준 고유 항목은
101개였다. 리뷰 packet 생성기의 candidate ID는 동일한 의미의 중복을 합치므로 리뷰어들은
101개를 한 번씩 판정했다.

따라서 이 round의 Azure/GCP node 판정은 검토 자료로 유효하지만, relation graph가
완전하다는 근거로 사용할 수 없다. 다음 discovery round는 operation request wrapper가 아니라
resource property 내부의 ID/reference field를 재귀 탐색하고, target resource collection과
해석 가능한 경우에만 후보를 만들어야 한다. 새 inventory hash가 생기면 이 round 결과를
그대로 이식하지 않고 두 독립 리뷰를 다시 수행한다.
