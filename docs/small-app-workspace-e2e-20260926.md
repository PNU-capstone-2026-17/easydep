# Small app implementation-to-testing timing notes

Scope: one already-designed small app, from implementation through Testing,
observed through existing command/task timestamps and result records only. No new app
generation, instrumentation, or reruns.

## Excluded scope-mismatch attempt

The initially observed app `62e5111b-5f10-4916-af98-e1150dec6b47` and requirements
command `2b386a8b-9425-4ea0-8a4c-c7040b741363` belonged to a scope-mismatch attempt that
was stopped when the user clarified that a fresh requirements/design run was not wanted.
It is not part of the implementation-to-testing timing sample. Its optional vCPU/memory
suggestion (`requiredValues=null`, `auto_selectable=true`) and subsequent advance
commands are retained here only to distinguish that abandoned attempt from the intended
measurement. No user answer was supplied on the user's behalf.

No timing from this stopped attempt is used to infer implementation or testing latency.

## Intended run

Selected seed app: `ab86d3cc-e08d-4f35-996b-82558357fc79`, Integer Sum Calculator,
one use case with OpenAPI route `/sum/{firstNumber}/{secondNumber}`. Its prior Testing
stage is COMPLETED and ten design artifacts exist; this is provenance only and does not
count as a pass for the current run. Existing implementation job:
`fe80a41ab25d4ac0b3aecb68f7535fb8`.

The source app was branched at `stage=design` through `branch_checkpoint`:
branch `00fbe67b-055d-4388-803d-6722539c8657`, command `d59e0363…`, COMPLETED
20:45:24–20:45:34 KST. Normal `start_implementation` command
`4be5d8b7-0d40-4c76-af09-4886f1a03d26` for this branch was QUEUED at 20:47:15 KST.
The DB row shows it started at 20:47:16.086654 KST. It created implementation job
`7f00288e2a6b4c4e8c9241a5507c7ae2`, run `run_1f0f89511bce`. A workflow snapshot at
20:52:11 KST was `READY`, current phase `frontend`; backend implementation and its unit
task were `SUCCEEDED`, while frontend and integration were pending. The initial
20:47:17.898432 KST progress card showed Backend `running`, Frontend/Integration
`waiting`, and a passed last Gradle check; this alone was not treated as task completion.

| Task | Effective executor/model | Task-result duration | Canonical check and result |
|---|---|---:|---|
| `implement-backend-com-easydep-app-application-impl-sumcalculationcontrolservice` | Direct editor / GLM `@cf/zai-org/glm-5.3-flash` / low | 55.939 s | `gradle ... compileJava --build-cache`, exit 0, 43.152 s, reused from task check |
| `implement-backend-com-easydep-app-application-impl-sumcalculationcontrolservice-unit-test` | Direct editor / GLM `@cf/zai-org/glm-5.3-flash` / low | 52.375 s | Exact-FQCN `gradle ... test --tests com.easydep.app.application.impl.SumCalculationControlServiceTest --build-cache`, exit 0, 25.999 s; JUnit total 4, failed 0, skipped 0; reused from task check |
| `implement-frontend-feature-calculatesum-708d4fc8362b` | Direct editor / GLM `@cf/zai-org/glm-5.3-flash` / low | 56.146 s | `npm run build`, exit 0, 26.800 s, reused from task check |

The frontend build pass did not mean frontend unit-test promotion succeeded. The frontend
unit task `implement-frontend-feature-calculatesum-708d4fc8362b-unit-test` ended
INTERRUPTED after 3 attempts. Attempt 1's canonical check stopped at
`implementation-promotion-boundary`: `application/frontend/reports/easydep-vitest-unit.json`
was outside the owner's promotion boundary. Attempt 2 was rejected as
`WRITE_OUTSIDE_OWNER_SCOPE` while trying to edit outside the single assigned test path;
attempt 3 ended `Editor conversation made no source change`. The known candidate report
path was absent when checked, so frontend unit-test totals/verdict are unconfirmed—not a
test assertion failure and not a pass. The report was not recreated.

The Workspace command was INTERRUPTED with the final no-source-change diagnostic. The
run-owner terminal span was 20:47:16.086–20:58:08.050 KST (651.963 s); the DB command
row's `completed_at` was 20:59:00.989 KST. These are different recorded boundaries and
the ~52.9 s gap is unclassified. Per-call LLM wall time was not present in the retained
task results. Task and check durations are separate and may overlap, so they are not
summed or subtracted to infer model time. Backend unit tests genuinely passed (4/4); the
frontend unit verdict is unconfirmed. Integration remained pending with 0 attempts, and
the actual Testing stage/runtime was not started. This is a partial implementation run,
not an end-to-end pass.

Follow-up boundary fix: the Vitest JSON destination is now selected by the system under
`run_root/reports/agent-executions/{task_id}.vitest.json`, outside the application
candidate; it is not a model-supplied argument. The verifier retains the raw report and
the exact one-test-file write boundary. Three focused local tests passed for the report
destination, candidate diff, and existing result gate. The subsequent same-run retry
below did not reach Vitest, so this production path remains unverified by an actual
frontend-unit run.

## Same-run retry after report-boundary fix

`retry_implementation` command `94352878-d048-49c1-b272-624b31c77714` reused the same
branch, job `7f00288e2a6b4c4e8c9241a5507c7ae2`, and run `run_1f0f89511bce`; it was queued
at 21:21:31 KST and terminated at 21:23:41 KST after 129.803 s. The backend parent/unit
and frontend parent results were reused. The frontend-unit task again ended INTERRUPTED
with `Editor conversation made no source change`. Therefore it did not run the canonical
Vitest check, create the controlled raw report
`reports/agent-executions/implement-frontend-feature-calculatesum-708d4fc8362b-unit-test.vitest.json`,
or promote a frontend test. No new report timestamp or unit totals exist; this is not a
Vitest assertion failure or pass. Integration remained unstarted/waiting, and Testing
was not run. The focused local tests (3 passed) validate the report-boundary code path,
not the blocked model-generated unit-test workflow. A later controlled diagnostic did
exercise parent-source repair and a same-test green recheck; see
[the unit-test follow-up](implementation-unit-test-followup.md). It used a disposable
synthetic fixture and does not complete or replace this Workspace API branch. This
branch's integration and Testing stages remain unstarted.

## Retry after the unit-author prompt correction

The generic bounded-evidence prompt had told unit-test owners to begin by editing a
source with an assigned completion marker, conflicting with creating a new test file.
The prompt was narrowed for backend/frontend unit-test tasks; implementation owners
retain the marker-first instruction. One focused local prompt regression passed, but
that is not a model or Workspace API result.

Workspace command `ae343cc4-66b8-496f-8fea-2ce913007412` retried only the existing
frontend-unit task in the same branch, job `7f00288e2a6b4c4e8c9241a5507c7ae2`, and run
`run_1f0f89511bce`. Its saved task result is
`reports/agent-executions/implement-frontend-feature-calculatesum-708d4fc8362b-unit-test.result.json`.
The result is INTERRUPTED (`OwnerConversationIncomplete`): effective model
`openai/@cf/zai-org/glm-5.3-flash`, task duration 24.642 s, one `replace_source` tool
action, but no changed source and no canonical check. The unit task did not produce a
Vitest report or promote a test. Integration remained unstarted and Testing was not run.
This is another editor-protocol/no-source-change outcome, not a unit assertion failure
or pass; the Workspace implementation-to-Testing flow remains incomplete.

## Retry with the pending-candidate guard

Workspace command `aa86e055-2ada-4515-b903-0d94693cb86a` retried only the same
frontend-unit task, job `7f00288e2a6b4c4e8c9241a5507c7ae2`, and run
`run_1f0f89511bce`. The task result is `FAILED` after 35.356 s, with effective model
`openai/@cf/zai-org/glm-5.3-flash` and one `replace_source` action. The saved Vitest
report `reports/agent-executions/implement-frontend-feature-calculatesum-708d4fc8362b-unit-test.vitest.json`
(2,770 B) records 6 passed, 0 failed, 0 pending. However, promotion was blocked with
`Candidate changes outside the owner's promotion boundary: application/frontend/reports/easydep-vitest-unit.json`.
Thus the canonical unit assertions passed, but the task did not complete or promote its
test candidate; this is a promotion-boundary failure, not a test failure. Integration
and Testing remain unstarted. Token usage/effective reasoning are not present in the
result; the TaskSpec remains GLM/medium/16,384, while direct-editor low is derived from
the existing runtime mapping rather than the stored result.

## Clean design-branch replay

After the persistent-workspace promotion failure above, the same source app
`ab86d3cc-e08d-4f35-996b-82558357fc79` was branched again from its completed design
checkpoint, without new Requirements/Design generation or manual artifact edits. The
clean branch app is `4b8db1c6-0733-4049-b984-2d48ae82df98`; branch command
`9f6ee269-bb1a-4bc0-88f3-d48f9a669bc9` completed, and its normal `start_implementation`
command `3658c23d-0f17-4892-b7ae-8a4cfa61d33c` created job
`a00f4ec96513494ea6a225384c01728f`, run `run_6d7a66b24770`. Its planned tasks were
backend implementation/unit test, frontend implementation/unit test, and integration.
This was a fresh implementation workspace for the same small-app design, not a fix or
continuation of the old run.

### Terminal result

Implementation command `3658c23d-0f17-4892-b7ae-8a4cfa61d33c` completed and the
workflow reached COMPLETE. The separate Testing command
`332d7864-8b0c-439e-9f58-4d0f9a867151` completed PASS with
`validationSkipped=false`; stored timestamps are created 00:07:43.199842 KST, started
00:07:43.573358, and completed 00:12:23.180415 (279.607 s).

| Task | Result / stored model | Canonical check |
|---|---|---|
| `implement-backend-com-easydep-app-application-impl-sumcalculationcontrolservice` | SUCCEEDED, GLM `openai/@cf/zai-org/glm-5.3-flash`, 42.628 s | `compileJava`, exit 0, 32.168 s |
| `implement-backend-com-easydep-app-application-impl-sumcalculationcontrolservice-unit-test` | SUCCEEDED, 46.210 s | Selected-FQCN Gradle test, exit 0, 29.749 s; JUnit 4/0 failed/0 skipped |
| `implement-frontend-feature-calculatesum-708d4fc8362b` | SUCCEEDED, GLM, 46.087 s | `npm run build`, exit 0, 22.983 s |
| `implement-frontend-feature-calculatesum-708d4fc8362b-unit-test` | SUCCEEDED, GLM, 45.026 s | Vitest, 15.259 s; 5 passed/0 failed/0 pending; raw report 2,314 B |
| `implement-vertical-integration` | SUCCEEDED, 56.909 s | `thin-integration`, `backend-test`, and `frontend-build`, exit 0 |

Testing passed its dynamic-functional, package, static, and IaC gates; UC1 workflow
reported 1 passed in 12.124 s. These are the recorded stage checks, not a claim of a
separate deployed-browser or production acceptance test. The run used the H2-MySQL
test profile; containers/network were stopped afterward. It did not test production
DB/cloud deployment. Testing model identity was not present in the final result
projection (OSS was configured for non-coding stages, but the actual call model is
unobserved). Artifact versions were IAC_CODE 2843, TEST_CODE 2845, SOURCE_CODE 2844,
DEPLOYMENT_FILE 2841, and FRONTEND_SOURCE_CODE 2842. Coding task results stored GLM;
neither token usage nor effective reasoning was stored there. The TaskSpec used
medium/16,384; direct-editor low is derived from the existing runtime mapping, not a
saved result. The preceding persistent-workspace attempts, including the frontend 6/6
assertion pass blocked at promotion, remain separate provenance and are not counted as
part of this clean successful run.
Task and canonical-check durations above are recorded independently; no model-call wall
time was retained for this run, and the task/check intervals may overlap. They are not
subtracted or summed to assign a bottleneck to model, runner, or verification work.

## Measurement policy

Record only existing timestamps, model/check summaries, and task/command IDs. Separate
explicit user-input waiting from queue/processing time where timestamps permit. Do not
infer model time from phase wall time or assign an unexplained gap to a subsystem. For
each later stage, capture model/effort/token cap and call/repair counts only when present;
record compile/test/build/install/container durations and cache reuse only when present.

## Read-only persisted-state revalidation (2026-09-27)

For app `4b8db1c6-0733-4049-b984-2d48ae82df98`, implementation command
`3658c23d-0f17-4892-b7ae-8a4cfa61d33c` and Testing command
`332d7864-8b0c-439e-9f58-4d0f9a867151` were rechecked without rerunning either stage.
The current Workspace API reports Testing `COMPLETED/PASS`; `validationSkipped=false`,
with IAC/static/dynamic-functional gates PASS and one executed workflow. UC1's stored
`GET /sum/42/42` result is HTTP 200 with body `84`. Verification used the `test` profile
with H2 in MySQL mode; this is not production DB/cloud deployment or browser UI evidence.
The implementation file-artifact API currently exposes: SOURCE_CODE 10 files,
FRONTEND_SOURCE_CODE 32, TEST_CODE 2, IAC_CODE 8, and DEPLOYMENT_FILE 7. `TESTING_PROFILE`
is not a file-artifact type; profile is verification metadata in the Testing result.

The exact persisted run `a00f4ec96513494ea6a225384c01728f/run_6d7a66b24770` confirms
backend JUnit 4/0 failed/0 skipped, frontend Vitest 5/0/0, backend `compileJava`, frontend
build, and thin integration checks all exited 0. These are generated unit/build and one
recorded functional workflow results, not an independent oracle over all requirements.
The implementation/integration task results retain GLM for the coding tasks; the Testing
result does not retain an actual per-call model field, so its configured OSS role is not
reported as an observed model. This audit does not alter the separate 16-input course
workflow, whose completion remains pending.

### Final read-only confirmation

The current Workspace GET still reports Testing command
`332d7864-8b0c-439e-9f58-4d0f9a867151` as `COMPLETED/PASS`, with validation not skipped;
the same saved UC1 step records `GET /sum/42/42` → HTTP 200, body `84`. Exact run
results again show JUnit 4/0/0, Vitest 5/0/0, and integration checks exiting 0. Artifact
counts are SOURCE_CODE 10, FRONTEND_SOURCE_CODE 32, TEST_CODE 2, IAC_CODE 8, and
DEPLOYMENT_FILE 7. Testing's configured OSS role is not evidence of the actual model,
which remains unobserved in the stored result.
