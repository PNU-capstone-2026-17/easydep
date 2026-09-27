# Implementation-stage unit-test owners

## Contract and shared path

Implementation planning now adds one test-only owner after each backend or frontend
implementation owner. Each test task depends on its subject, exposes only its test file
as writable/required output, and carries `required_test_paths` plus the subject source
path in `verification_profile.unitTestSubjectPaths`. Backend tasks also identify the
JUnit test class with `unitTestClass`; frontend tasks use Vitest. Integration depends on
both implementation and unit-test owners. API/scenario testing remains in the later
Testing phase.

These owners use the existing coordinator, workspace, and verification/report path. They
are not an isolated compile/test pipeline: the same run manifest, task DAG, materialized
application, and integration gate remain shared. Current planner metadata assigns the
single-file unit-test task to the existing direct editor; its subject source and API
contracts remain read-only, and the existing canonical unit-test gate is unchanged. The
earlier replay below used restricted OpenHands at configured `medium`; the follow-up
direct-editor replay now has an actual model/check result. Direct editor's existing
`medium` to `low` mapping applies to that follow-up.

Generated frontend scaffolds provision Vitest 4.0.18 with the existing Vite 6.4.3 and
Node 22 toolchain, jsdom, Testing Library, and pinned `@testing-library/jest-dom` 7.0.1.
The scaffold registers the Vitest-specific matcher entry via `setupFiles` and explicitly
cleans React Testing Library DOM after each test, following [Vitest setupFiles](https://v4.vitest.dev/config/setupfiles),
[jest-dom's Vitest setup](https://github.com/testing-library/jest-dom#with-vitest), and
[Testing Library cleanup](https://testing-library.com/docs/react-testing-library/api/#cleanup).
The pinned jest-dom 7.0.1 supports the existing Node 22 floor and makes Vitest an optional
peer ([release notes](https://github.com/testing-library/jest-dom/releases/tag/v7.0.1)).
The focused command contract is
`npm run test:unit -- --reporter=json --outputFile=reports/easydep-vitest-unit.json <test-path>`
from `application/frontend`; the verifier removes the transient report before each run.

## Verification status

- Focused pytest: 10 passed total (7 planner/scaffold tests; 3 unit-verification tests).
- Editor-transition checks after that baseline: the existing planner cases were rerun
  (3 passed) and runtime's new editor-target cases passed (2 passed). These are follow-up
  reruns/additions, not 15 distinct tests.
- Shared React scaffold setup regression: the existing focused template test passed (1).
- Source inputs were app `f01ededa-07de-4ced-8ea4-8d5348994a0e`, saved job
  `635eb8bbfd344acb8bfe930bceffa0d0`, run `run_ec4889a9fcb8`. The actual replay was
  `.easydep/implementation-runs/owner-sequence-replay-443b542efa7a46d0923e7ab3a9e1a25c`,
  generated run `run_d33cfd51cdaf`; task IDs below refer to its fresh plan.
- Earlier validation was a partial four-owner replay, not full E2E. The backend test task's
  conversation ended `stuck`, but its exact-FQCN Gradle check exited 0 (JUnit 6/0/0); the
  13,392 B Java test was promoted (SHA-256 prefix `159492`, suffix `BBAFFE`). Assertion
  quality was not independently reviewed; the JUnit XML was unavailable after the earlier
  volume cleanup and was not recreated. The 3,888 B backend task-result record is preserved
  under `.easydep/diagnostics/unit-test-followup-20260926/baseline-snapshots` (distinct from
  the missing JUnit XML).
- In this replay, the frontend implementation parent used direct editor at effective
  `low` from saved `medium`; unit owners use restricted OpenHands at configured `medium`.
  Production defaults and model configuration were not changed.
- A follow-up direct-editor validation regenerated only the frontend unit TaskSpec through
  the production planner/manifest merge path: GLM `@cf/zai-org/glm-5.3-flash`, saved
  medium/16384, temperature 0.2, editor mode, one writable test path; effective editor
  reasoning maps medium to low under the existing runtime policy. The fresh public owner
  run took 102.060s, used two `replace_source` actions, and ran canonical Vitest once: 6
  total, 0 passed, 6 failed, 0 skipped. Failure was generated test setup (`toHaveTextContent`
  matcher unavailable, then duplicate buttons from missing DOM cleanup), not a feature-source
  verdict. No test was promoted; the 6,324 B source and 87,838 B report remain under
  `.easydep/diagnostics/unit-test-followup-20260926/frontend-editor-attempt`.
- A fixture-only canonical recheck applied the shared scaffold setup to that unchanged test
  and parent snapshot: Vitest 6/6 passed, 0 failed/skipped. Parent hash stayed
  `84E604…FE0FA`; GLM-generated test hash stayed `5E091899…D2DF29A0`. No source, test, API,
  or client was edited; this validates the environment fix on the same model output, not a
  new owner/LLM run or full E2E. The 2,785 B report, 155 B setup, and 213 B Vite config are
  retained under `.easydep/diagnostics/unit-test-followup-20260926/fixture-only-pass`.
  Reported test titles cover blank validation/API no-call, trimmed criteria/results, empty
  state, API-specific and generic errors, and loading-button state; this is a title-based
  summary, not an independent semantic review.
  Fixture root/volume and owned TEMP timing root were cleaned; the original saved
  app/job/run remain untouched.
- In that replay, frontend implementation built successfully (5,823 B promoted TSX), but
  the restricted frontend unit owner timed out, then resumed conversation
  `80292b2a-a10c-50de-bdd1-ea2dcc690cee` returned two no-action replies (reasoning lengths
  35,701/34,343; no visible content/tool calls; usage/finish reason unavailable). It ended
  `INTERRUPTED/stuck`; no canonical Vitest check or test promotion occurred, and persistent-
  volume candidate existence was not verified. This protocol outcome is not a test failure.

| Owner | Task ID in actual replay | Executor / effective model / effort / cap | demo | Actual result |
|---|---|---|---|---|
| Backend unit owner | `implement-backend-com-easydep-app-application-impl-courseofferingsearchcontrolservice-unit-test` | Restricted OpenHands / GLM `@cf/zai-org/glm-5.3-flash` / medium / 16384 | false | Conversation stuck; exact-FQCN Gradle check PASS, JUnit 6/0/0; source promoted |
| Frontend unit owner | `implement-frontend-feature-searchcourseofferings-965e16762f0f-unit-test` | Restricted OpenHands / GLM `@cf/zai-org/glm-5.3-flash` / medium / 16384 | false | Resumed conversation interrupted after two no-action replies; no canonical Vitest/report or test promotion (quality unjudged) |
| Frontend unit editor follow-up | Same frontend unit task | Direct editor / GLM `@cf/zai-org/glm-5.3-flash` / effective low / 16384 | false | Initial run Vitest 0/6/0; fixture-only same-test recheck 6/6/0 after shared setup fix (no owner/LLM run) |

## Integration finalization regression check

- Read-only audit confirmed the coordinator already reuses a SUCCEEDED
  `integration-implementation` task's `resultFile` as `backendRegression`; the
  full-workspace backend gate remains an `elif` fallback when there is no successful
  integration owner. No production check was removed or newly deduplicated.
- Three focused coordinator tests passed: the existing no-integration fallback still
  runs once; a successful integration result reconciled from manifest/result files
  finalizes without calling the fallback; and a failed integration owner leaves the
  workflow FAILED without invoking completion. This protects completion/result
  consistency, not application behavior or full E2E.

## Verification scope and missing failure-based repair path

- The earlier Workspace calculator branch still ended before frontend-unit verification
  and is not counted as completed E2E. Separately, a controlled diagnostic fixture now
  verified an assertion-failure → parent-source repair → same frozen canonical test pass.
  The fixture is synthetic and is not a repair to the saved application.
- Unit tasks deliberately make the implementation subject read-only and the assigned
  test file writable (`runtime.py:_owner_evidence_boundary_message` and unit-task
  prompt branch). The newly added repair selector recognizes positive executed/failed
  unit-test totals, maps the unit task's declared subject paths to exactly one declared
  parent implementation owner, and records the unchanged unit task for canonical
  recheck. Zero, all-skipped, and no-failure totals do not select the parent source.
- Focused local coverage: seven verifier/repair cases passed (8.98 s), including real
  JUnit XML counts on nonzero `:test FAILED`, exclusion of stale XML after
  `:testClasses FAILED`, and fresh Vitest failure totals; four strict-loader cases passed
  (8.93 s), including accepting the system's repaired prompt hash and rejecting tampering.
  Earlier lifecycle tests also passed (five cases, 18.36 s); they used a verifier stub and
  were not treated as real canonical execution.

### Controlled diagnostic red→repair→green

- Disposable fixture root: `.easydep/implementation-runs/unit-assertion-repair-probe-20260926`,
  run `generated/runs/run_probe`. Parent task
  `implement-backend-com-easydep-app-application-impl-sumcalculationcontrolservice`;
  unit task is the same ID plus `-unit-test`. The parent source was deliberately seeded
  with `firstNumber.add(secondNumber).add(BigDecimal.ONE)`; this is a controlled defect,
  not an application edit.
- Initial canonical JUnit was 4 total / 2 failed / 0 skipped in 22.177 s. The positive
  decimal assertion expected 19.75 but got 20.75; the negative/zero case also failed.
  The frozen test and retained candidate were both 2,188 B with SHA-256
  `c6cc66eaf6551d88163bfaebb0b40e47cc55b75fe82578e148e9fce82f78525c`.
- The first failure exposed missing `unitTestResults` on nonzero Gradle test exits; exact
  JUnit XML counts are now included only when Gradle reports `> Task :test FAILED` and
  the selected class XML is fresh/parseable. A separate strict-loader mismatch rejected
  a system-repaired prompt digest; the loader now verifies the existing
  `SHA256(base prompt + NUL + repair prompt)` contract. Focused tests above cover both.
- The parent source owner then succeeded with actual model
  `openai/@cf/zai-org/glm-5.3-flash`, changing only the service source (715 B, SHA-256
  `71b7dd828bac8a6622194fea60682b1866597fe1b556d54416d63079518b0b26` →
  `71626d4c489b8475b39cf18e8b69d6a4ba4ccf86176044427eed27d3034d8960`). TaskSpec was
  GLM medium / 16384; direct editor's effective medium→low mapping is code-derived, not
  an explicit saved result field. Parent task duration was 29.296 s; its canonical
  `compileJava` gate exited 0. The green recheck invoked no model (`agentInvoked=false`),
  ran the same Gradle test class in 16.772 s, and passed 4/0/0. The frozen test SHA was
  unchanged before/after; only that test file was promoted after the green check.
- The 19,147 B `probe-summary.json`, RED snapshot (5,869 B), parent result (3,804 B),
  and green result (2,454 B) are retained under the fixture root. No raw JUnit XML
  survived owner-workspace cleanup. This is a controlled owner/repair/check/promotion
  diagnostic, not a Workspace API E2E, full implementation workflow, frontend test, or
  Testing-stage pass. The original calculator Workspace branch remains incomplete.

### Workspace Testing repair admission follow-up

- A focused local regression now covers the public `request_owner_repair` admission seam
  without an OpenHands SDK `base_state.json`. It still requires a completed worker record,
  job/manifest/workflow checkpoint, and unambiguous diagnostics resolving to one declared
  implementation owner; the queued repair plan retains that owner's source-only scope.
  The existing Workspace Testing requeue seam also passed, preserving the same command,
  implementation job, and test profile. These two focused tests passed; they are not a
  live Workspace Testing execution.
- A later disposable probe exercised public repair admission with a frozen canonical JUnit
  candidate: RED was 4 total / 2 failed / 0 skipped in 28.190 s, with
  `agentInvoked=false`; the active plan mapped the failed unit task to its declared backend
  owner and scheduled that same unit for frozen recheck. An initial driver continuation
  stopped on `TypeError: _RecordingExecutor.shutdown(cancel_futures=...)` before the parent
  call; the retained active plan was then continued without repeating setup, RED, or public
  admission.
- That continuation succeeded: the parent GLM owner used
  `openai/@cf/zai-org/glm-5.3-flash`, changed exactly one backend source file, and completed
  in 29.301 s. The frozen canonical recheck invoked no model and passed 4/0/0 in 22.804 s.
  Its test SHA remained `c6cc66eaf6551d88163bfaebb0b40e47cc55b75fe82578e148e9fce82f78525c`;
  the controlled `+1` source defect was removed (source SHA
  `71b7dd828bac8a6622194fea60682b1866597fe1b556d54416d63079518b0b26` →
  `71626d4c489b8475b39cf18e8b69d6a4ba4ccf86176044427eed27d3034d8960`). The fixture
  evidence is under `.easydep/implementation-runs/public-owner-repair-probe-20260927/`.
- This proves public repair admission without an SDK checkpoint plus controlled source repair
  and frozen canonical recheck, not live HTTP/Workspace Testing completion or a complete
  Workspace API E2E. The two preceding setup errors remain non-evidence: one ran a writable
  oracle before public admission (4/4); the next lacked the frozen test file and stopped
  before RED. No product or model failure is inferred from them.
- Two earlier attempts are setup errors, not model evidence: one ran a writable unit oracle
  before public repair admission (4/4, invalid for this proof); the next stopped before RED
  because the isolated driver had not recreated the frozen test file. No product defect or
  model failure is inferred from either attempt. The separate controlled diagnostic above
  remains the evidence for source repair and frozen GREEN, and is not upgraded to a full
  Workspace Testing failure→repair loop.

### Direct-editor repair prompt regression

- Direct-editor repair requests now retain the original task prompt and append only the
  latest repair evidence/diagnosis, rather than replacing the task contract or accumulating
  older repair messages. Focused tests
  `test_editor_owner_repairs_once_after_failed_check` and
  `test_direct_editor_repair_prompt_keeps_initial_contract_without_history` passed (2/2).
  They assert the original task and exact target remain visible with the newest source and
  diagnosis, while an older repair is not repeated. This is a mocked local regression only;
  the corresponding course frontend-unit owner replay has now reached its canonical check:
  GLM direct-editor made one `replace_source` action in 43.289 s, then Vitest exited 1
  after 18.548 s (`npm ci` included), with 3 tests / 1 failed / 0 skipped. A frozen candidate
  was retained (SHA `c0d950…8d290e`) for the declared DropRegistration implementation owner.
  The diagnostic did not preserve the raw assertion details, so this is a unit-check failure
  with unknown cause—not evidence of a source defect. The existing Workspace resume/parent
  repair route is pending; no GREEN recheck, implementation completion, or Testing-stage
  result is claimed.
- The next owner replay will copy an already-produced selected-task Vitest report and any
  system-recorded frozen test candidate into its preserved evidence before diagnostic cleanup.
  Focused `test_replay_preserves_frontend_vitest_report_and_frozen_candidate` and the existing
  `test_replay_writes_failure_result_and_cleans_up_when_owner_fails` both passed. This change
  cannot recover the previous attempt's deleted report. The first owner-replay request was
  blocked before execution (0 model/container calls); a subsequent replay completed. It used
  GLM `openai/@cf/zai-org/glm-5.3-flash`, one `replace_source`, and canonical Vitest passed
  4/0/0 in 21.801 s (owner duration 65.138 s). The preserved raw report is 3,054 B and the
  task result 5,249 B under
  `C:\Users\projw\AppData\Local\Temp\easydep-current-unit-replay-20260927\owner-replay-evidence-25607d65fb4240a3aed61e2eba3e0506\agent-executions`; no frozen candidate was produced on this passing attempt. The four test titles cover heading,
  article region, initial API no-call, and pending state. This is only a fresh diagnostic-run
  pass: `_clone_job` copies saved job inputs, then `replay_owner_task` generates a new run and
  selects its newly materialized unit task; it does not carry the old run's completed TSX
  source. The preserved evidence has no subject TSX/hash, so this pass does not validate the
  prior implementation's behavior or reproduce the earlier 3/1 failure. The new evidence
  path itself was exercised, but no failed report/candidate case was produced by this replay.

### Diagnostic replay input fidelity correction

- The preceding 4/0/0 DropRegistration replay was only a fresh-scaffold test, not a test of
  the saved completed implementation: its evidence had no subject TSX. The diagnostic now
  seeds **unit-task replays only** from `verification_profile.unitTestSubjectPaths`, requiring
  one explicit `depends_on` implementation task, a `SUCCEEDED` workflow entry, exact parent
  `allowed_write_paths` coverage, and a source SHA matching the saved workflow `outputHashes`.
  It does not change source-task replay or any production app/source.
- Before cleanup, the same evidence seam now preserves the declared subject source, required
  unit-test source, and existing Vitest report. Focused
  `test_unit_replay_copies_explicit_completed_subject_and_preserves_test_evidence` and
  `test_unit_replay_fails_clearly_when_completed_subject_is_missing` passed (2/2, 10.50 s).
  This establishes diagnostic input fidelity locally, not model behavior. Terra's subsequent
  source-faithful GLM replay was blocked before execution (0 model/container calls); therefore
  no source-faithful test verdict exists yet. The earlier 4/0/0 remains recorded only as a
  fresh-scaffold pass and is not evidence about the saved DropRegistration implementation.
- A later source-faithful replay now seeded the saved, completed TSX (3,351 B; SHA
  `00BC…C38A`, matching the parent output hash) before fresh unit-task materialization. GLM
  `openai/@cf/zai-org/glm-5.3-flash` made one edit (owner 53.149 s); canonical Vitest ran
  7/1/0 in 23.933 s. The failed generated oracle queried `getByRole('form')` for an unnamed
  form; the rendered form was busy and its controls disabled, so this is a test-query
  mismatch, not evidence of a production source defect. The frozen candidate metadata SHA is
  `64cea5…6674`, but that run's cleanup omitted the candidate file because it reads the
  nested `verificationEvidence.frozenTestCandidate` field. The preservation seam now handles
  that actual result shape; three focused diagnostic-replay tests pass (3/3, 8.81 s), and the
  generic frontend-unit prompt has one focused assertion for accessible-role guidance (1/1,
  9.64 s). These local tests do not re-run the model. The candidate file itself was not
  recoverable from the completed diagnostic, so no repair/green claim is made. Raw subject,
  report, result, and event evidence is retained at
  `.easydep/diagnostics/unit-test-followup-20260926/course-dropregistration-faithful-replay-20260927/`.
- A subsequent replay loaded the subject before task materialization and retained the failed
  test candidate as well: Vitest 6/1/0; subject 3,351 B SHA `00BC…C38A`; frozen test 4,377 B
  SHA `643AE6…2B834`, matching the result metadata; raw report 9,144 B, result 5,367 B, and
  event journal 304 B. These exact files are copied under
  `.easydep/diagnostics/unit-test-followup-20260926/course-dropregistration-faithful-replay-20260927-attempt2/`.
  This verifies subject/test/report preservation for a failed canonical check, not a parent
  repair or GREEN result; the remaining assertion failure is not classified in this entry.
- The 6/1 replay preceded delivery of the role-query guidance: the old copy lived only in
  `_owner_workspace_guidance`, while direct editor requests consume `_owner_evidence_boundary_message`.
  Unit-task boundary assembly now includes the authoring/read-only scope for nonempty focused
  test paths and the generic frontend role guidance. An actual `_request_direct_editor_action`
  capture test passed for frontend and backend unit tasks (2/2, 9.74 s), asserting required
  test paths, readonly subject body, unit authoring contract, and frontend role guidance.
  This proves prompt composition, not a post-change model response; the 6/1 result must not be
  described as the model ignoring guidance it did not receive.
- After moving the shared unit authoring/accessible-role guidance into the composed evidence
  boundary, an actual request-capture regression confirmed it reaches `_request_direct_editor_action`
  for both frontend and backend unit tasks. The next source-faithful GLM replay still ended at
  canonical Vitest 7/1/0; its saved subject remains 3,351 B / SHA `00BC…C38A`, and the frozen
  test is now preserved (4,332 B / SHA `7B72B9…570AD`, matching result metadata). Raw report
  (9,606 B), result (5,367 B), and events (304 B) are retained separately at
  `.easydep/diagnostics/unit-test-followup-20260926/course-dropregistration-faithful-replay-20260927-attempt3/`.
  The prompt-delivery regression is locally proven, but the model/check outcome is still not
  GREEN; no parent repair, same-test recheck, or normal Workspace retry is claimed here.
- Three bounded oracle assessments used the configured OSS model `openai/gpt-oss-120b`
  (Cloudflare endpoint, reasoning medium), with source/test/contract/report evidence and the
  typed output `classification` (`implementation|test_oracle|undetermined`), rationale,
  evidence, preserved assertions, and correction instruction. DropRegistration received two
  calls (6.556 s, then 6.062 s); SumCalculation received one (2.529 s). The first Drop review
  classified the unnamed-form role query as `test_oracle`, but incorrectly suggested adding a
  `data-testid` to the read-only SUT. The scoped repeat kept the same classification and
  instead recommended locating the existing form from the submitting button in the test file;
  no source or test was changed by these reviews. The controlled Sum fixture was assessed as
  `implementation`, consistent with the plus-one source and its valid JUnit expectations.
  Exact raw Drop report totals are 7 total / 6 passed / 1 failed / 0 pending; the model's
  “six other tests pass” statement agrees with these counts. Requests/responses are retained in
  `.easydep/diagnostics/unit-test-followup-20260926/oss-oracle-assessment-20260927.json` and
  `oss-oracle-assessment-drop-recheck-20260927.json`. These are review outputs, not an
  implementation repair or canonical test verdict.
- The same two neutral evidence payloads were also assessed with the configured coding GLM,
  `@cf/zai-org/glm-5.3-flash` (Cloudflare, low reasoning): DropRegistration 16.859 s and the
  controlled Sum fixture 10.418 s. Both returned the same classifications as OSS: test-oracle
  query issue for the unnamed form, and implementation plus-one defect for Sum. GLM's Drop
  instruction stayed within the assigned test file and preserved the remaining assertions;
  its Sum instruction kept the JUnit test unchanged and limited correction to the subject.
  These are two case-level trials, not an accuracy benchmark. The Drop OSS scoped repeat used
  the same tightened instruction; the earlier OSS Sum call preceded that scope tightening,
  so cross-model differences should not be inferred from latency or wording. GLM raw responses,
  usage, and timings are retained at
  `.easydep/diagnostics/unit-test-followup-20260926/glm-oracle-assessment-20260927.json`.
- The later `-fresh-config` diagnostic used the same completed DropRegistration subject and
  frozen failure seed but a new task ID, giving it a fresh sandbox populated from the current
  generated frontend setup. This corrected two diagnostic-fixture issues (stale sandbox lacked
  `test:unit`; the seed result had collided with the mutable runtime result path); neither was a
  product-source failure. The fresh canonical Vitest report is 7 total / 7 passed / 0 failed /
  0 pending (3,154 B); GLM editor used one `replace_source` (170,518 ms), and the canonical check
  passed in 117,041 ms including `npm ci`. The test output is 4,436 B / SHA
  `F2F5B646…DED77C40`; the subject remained SHA `00BCBF…C38A`. This establishes a fresh-config
  unit-test pass for the diagnostic clone only, not completion of the interrupted live Workspace
  course workflow or its downstream integration/Testing stages.

### Source-repair retry boundary

For an implementation-classified unit failure, `schedule_cross_phase_repair` assigns the
declared source parent as owner and keeps the failed unit in `recheckTaskIds`. Reconciliation
therefore leaves the unchanged unit result `FAILED` while the parent is `PENDING`. The
coordinator returns this repair-planned state, and `run_workflow_to_completion` treats that
`FAILED` status as terminal for the current invocation. By contrast, test-oracle repair changes
the unit task's effective prompt, so reconciliation can make that unit `PENDING` and continue
within the same invocation. The existing `retry_implementation` checkpoint action passes
`retry_failed=True`; one such retry can run the source parent and then the frozen unit recheck
once it becomes runnable. This explains the observed yield boundary; automatic continuation
after source-only repair remains a separate, non-blocking workflow-design consideration.

### Production TypeScript scope and focused unit checks

The shared frontend `tsconfig.json` keeps `src` as its production include and excludes test/spec
files and test directories. Normal `run_phase` refreshes only this generated config when the
frontend scaffold exists; it does not regenerate source, tests, package metadata, or design
artifacts. Focused Vitest remains the runtime behavior check, not a static typecheck of test
files. Earlier generated-fixture TypeScript errors are retained as a separate type-quality issue:
they are no longer part of the production `tsc -b` gate, and this change does not claim those
fixtures are statically type-checked. Vitest documents typechecking as a separate feature, disabled
by default: [Testing Types](https://vitest.dev/guide/testing-types.html),
[typecheck configuration](https://vitest.dev/config/typecheck.html).

The focused scaffold/phase/typecheck/cache/build boundary selection passed 6 tests; its dedicated
TEMP directory was removed. The existing 31 completed TaskSpecs are unaffected because the
config is not part of their inputs or prompts. These are focused code-level checks, not proof that
the Workspace retry succeeded. That retry has since terminated with a source-scope guard failure,
before the assigned unit test was rechecked; this config change does not resolve that failure.

The current verification split keeps per-source `tsc -b` and per-task focused Vitest, with the
production Vite bundle at integration rather than repeating it for each source owner. A follow-up
three-test selection passed for typecheck command routing, rejection of stale `dist` as build
evidence, and the integration full-build boundary; its dedicated TEMP directory was removed.
Same-task repair continues to reuse its existing workspace and npm/Gradle download caches, but
task-to-task compiler state (`.tsbuildinfo`/Gradle compiler state) is not shared because successful
task sandboxes are cleaned. No new cross-task cache was introduced, elapsed-time savings have not
been measured, and integration test duplication is outside this change. This split does not fix
the separately observed source-repair scope-guard failure.
