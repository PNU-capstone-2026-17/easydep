# origin/main 통합 기록 (2026-08-28)

`integration/origin-main-20260828`은 다음 두 부모를 `--no-ff`로 통합했다.

- typed 설계 기준: `a4576ff65b548edfd6a712b4d628fe1d6e79b52e`
- 고정한 `origin/main`: `d3caa86f49cc4c501cdfbdfd906f3b5f13b387b6`
- merge commit: `5906d5dec7e4814965f762511ec647a080a56260`

구형 class/sequence extractor는 복원하지 않았다. main의 수정 의도는 typed BCE·sequence
계약의 정규화와 결정론 검증으로 옮겼다. 여기에는 collection 입력, Control 반환 응답,
API-to-Control 경로, 중복 call ID, call보다 앞선 return, Actor→Boundary→Control handoff,
main-flow 순서와 확장 분기 위치가 포함된다. 요구사항 Step 3은 정적 검증을 통과해 의미
검증 단계로 전진한 후보를 finding 수가 같다는 이유로 버리지 않는다.

main의 구현·workspace 안정화와 ResourcePlan 기반 Terraform 경로는 유지했다. 저장된 현재
시퀀스 계약은 `normalize_sequence_model`로 재검증하며 삭제된 heuristic extractor를 다시
참조하지 않는다.

통합 직후 requirements Step 1~3, typed class/sequence, API, checkpoint, implementation,
workspace 대상 테스트가 통과했다. 전체 테스트의 16개 실패는 이 merge가 수정하지 않은
기존 cloudkb cache·동결 hash·환경 의존 평가 경로에 한정되며, 후속 bounded-context 이동과
최종 rollup에서 다시 감사한다.
