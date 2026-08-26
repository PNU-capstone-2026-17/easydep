# Checkpoint E2E harness

제품 DB나 workspace를 거치지 않고, 동결한 사례연구 체크포인트에서 다음 에이전트
산출물 하나만 생성한다. 실행 결과는 `artifacts/checkpoint-e2e/`에 남는다.

`e1-aws`는 기존 E1 자연어 사례를 그대로 참조한다. 완결된 배포 실험에 필요한 앱 artifact,
공개 HTTP, PostgreSQL runtime, private TCP 연결, block volume 계약은 별도 typed planning
fact로 전달한다. 이 fact는 클래스·시퀀스·API·ERD 생성에는 들어가지 않으며, ERD로부터
데이터베이스나 디스크를 암묵적으로 추가하지 않는다.

```powershell
python -m evaluation.checkpoint_e2e run --case e1-aws --from erd
python -m evaluation.checkpoint_e2e run-all --case e1-aws
python -m evaluation.checkpoint_e2e run-all --case e1-aws --run-id <run-id> --resume
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws --through sequence_diagram
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws --output <candidate> --resume
python -m evaluation.checkpoint_e2e gold-seed <candidate> --output <rebased-candidate> --through erd
python -m evaluation.checkpoint_e2e gold-validate <candidate-directory>
python -m evaluation.checkpoint_e2e gold-promote <candidate-directory> --case e1-aws
```

`run-all`의 각 작업은 직전 실행 결과가 아니라 해당 골드 snapshot에서 시작한다. 다이어그램
산출물은 PUML과 SVG를 함께 기록하며, LLM 표현은 바이트가 아니라 구조 signature로 비교한다.
OpenTofu 검증은 E2E 전용 캐시를 만들지 않고 시스템 공용
`.easydep/provider-plugin-cache`를 사용한다. 이 캐시는 고정된 AWS·Azure·GCP provider를
각각 하나씩만 보존하며 `app.core.cloudkb.depkb.provider_cache`가 허용 버전을 검사한다.
