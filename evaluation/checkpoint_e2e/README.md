# Checkpoint E2E harness

제품 DB나 workspace를 거치지 않고 사례연구 입력에서 전체 후보를 연쇄 생성하거나,
동결한 gold 체크포인트에서 다음 단계만 격리 생성한다. 실행 결과는
`artifacts/checkpoint-e2e/`에 남는다.

`e1-aws`는 기존 E1 자연어 사례를 그대로 참조한다. 완결된 배포 실험에 필요한 앱 artifact,
공개 HTTP, PostgreSQL runtime, private TCP 연결, block volume 계약은 별도 typed planning
fact로 전달한다. 이 fact는 클래스·시퀀스·API·ERD 생성에는 들어가지 않으며, ERD로부터
데이터베이스나 디스크를 암묵적으로 추가하지 않는다.

```powershell
# Stable sequential candidate output.  Promote only after full validation.
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws --through sequence_diagram
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws --resume
# Keep the verified requirements snapshot after a downstream code fix.
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws --resume --restart-from requirements
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws --replace

# Stable isolated samples: each starts from the selected frozen gold checkpoint.
python -m evaluation.checkpoint_e2e run-stage --case e1-aws --from erd
python -m evaluation.checkpoint_e2e run-stage --case e1-aws --from erd --samples 5
python -m evaluation.checkpoint_e2e run-stage --case e1-aws --from erd --resume
python -m evaluation.checkpoint_e2e run-stage --case e1-aws --from erd --replace

# Legacy timestamped evidence commands remain available for compatibility.
python -m evaluation.checkpoint_e2e run --case e1-aws --from erd
python -m evaluation.checkpoint_e2e run-all --case e1-aws
python -m evaluation.checkpoint_e2e run-all --case e1-aws --run-id <run-id> --resume

# A custom candidate path can still be resumed explicitly.
python -m evaluation.checkpoint_e2e gold-candidate --case e1-aws --output <candidate> --resume
python -m evaluation.checkpoint_e2e gold-seed <candidate> --output <rebased-candidate> --through erd
python -m evaluation.checkpoint_e2e gold-validate <candidate-directory>
python -m evaluation.checkpoint_e2e gold-promote <candidate-directory> --case e1-aws
```

`gold-candidate` publishes only to
`artifacts/checkpoint-e2e/current/<case>/chain`; `run-stage` publishes to the
corresponding `stages/<checkpoint-transition>` directory, containing exactly
`sample-01` through `sample-N` (three by default) and an aggregate manifest
verdict.  A completed current directory is reused with `--resume`, or safely
rebuilt with `--replace`: work first completes in a sibling staging directory
and only then replaces that exact current directory.  Failure evidence is also
published as current output rather than leaving a hidden staging directory.
`--restart-from` verifies and retains the named checkpoint, removes later
snapshots and evidence in staging, and regenerates only the affected suffix.
The frozen `evaluation/baselines/.../goldset` is read-only for experiment
commands and remains the sole accepted chain until a fully validated current
chain is explicitly promoted.

`run-all`의 각 작업은 직전 실행 결과가 아니라 해당 골드 snapshot에서 시작한다. 다이어그램
산출물은 PUML과 SVG를 함께 기록하며, LLM 표현은 바이트가 아니라 구조 signature로 비교한다.
OpenTofu 검증은 E2E 전용 캐시를 만들지 않고 시스템 공용
`.easydep/provider-plugin-cache`를 사용한다. 이 캐시는 고정된 AWS·Azure·GCP provider를
각각 하나씩만 보존하며 `app.cloudkb.depkb.provider_cache`가 허용 버전을 검사한다.
