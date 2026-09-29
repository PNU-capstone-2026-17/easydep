# Run-local unified implementation workspace plan

## Purpose

Try a run-local shared frontend-and-backend workspace for normal serial implementation tasks, without Git. The pilot should reduce repeated source preparation while preserving task isolation, current verification, promotion, and recovery behavior. The pilot changes implementation-runner code only; it does not edit generated applications, change the database schema, or convert existing runs.

## Scope and constraints

- Use one run-local source workspace for ordinary serial tasks. Keep existing isolated paths for precheck, frozen-unit, repair, and any other special flow that requires them.
- Reuse the existing canonical-vs-candidate manifest and scope checks, verified promotion, and task result/evidence paths. These remain the authority for what may reach canonical source and when a task succeeds.
- Preserve project-local dependency/build caches across normal serial tasks in the run workspace. Keep cache invalidation tied to the existing install inputs and failure behavior; caches are disposable with the run volume.
- Retain an unsuccessful task's candidate for that same task's retry/recovery. Never make it visible to an unrelated task. Reconcile canonical changes from isolated paths before the next shared-workspace task.
- Do not add a toolchain image rebuild, new database schema, checkpoint migration, or speculative transaction/recovery architecture for this pilot.

Parallel owner execution, historical-run conversion, changing LLM/source-generation semantics, and bypassing demo validation are out of scope.

## Current baseline and measurements

The coordinator currently executes owners serially in [`_execute_task_batch`](../app/implementation/workflows/coordinator.py). Each owner gets a task-specific workspace through [`prepare_agent_workspace`](../app/implementation/agents/workspace.py); candidate changes are checked against canonical run source in [`_candidate_application_changes`](../app/implementation/agents/runtime.py), and approved changes are promoted by [`_promote_changed_files`](../app/implementation/agents/runtime.py). The pilot changes workspace routing only for the normal serial path; existing special paths and checks remain in place.

Observed workload measurements motivating the pilot:

| Repeated work in one run | Observed count | Observed elapsed time |
| --- | ---: | ---: |
| Frontend `npm ci` | 14 | about 135 s |
| Backend compile | 8 | 239 s |
| Backend test | 8 | 230 s |

These are measurements, not promises of recoverable savings. Gradle already uses the named `GRADLE_USER_HOME` cache and npm already has a named download cache, so dependency download is not the whole cost. Measure workspace/source preparation, dependency preparation, compile, test, and verification separately; retain per-task timing and run-volume growth and state the limitations of a single comparison run.

## Preserved behavior

The shared workspace is an implementation detail. Existing task scope and immutable-path checks, canonical-vs-candidate manifest, promotion validation, result publication, verification evidence, frozen-unit SHA oracle, retry/stop behavior, finalization/audit/regression branches, and frontend artifact paths remain authoritative. Do not replace or weaken them with workspace-local bookkeeping.

Normal tasks must see the latest accepted canonical baseline plus only their own same-task retained candidate when resuming. On successful verification, use the existing verified promotion path and task result path. A no-change success follows the existing result-publication path. On failure, interruption, NEEDS_INPUT, or stop, preserve the candidate using the existing task-associated recovery path and prevent unrelated tasks from observing it. Special isolated paths continue to run as they do now; reconcile their accepted canonical changes before returning to the shared path.

Preserve useful project-local caches (including `node_modules` and Gradle/build outputs) for the lifetime of the run-local workspace so serial tasks can reuse them. Existing lockfile/install checks and failed-install behavior govern reuse. Do not promote cache contents as source. Exact run-volume cleanup remains the cleanup boundary.

## Minimal pilot sequence

1. Characterize current manifest/scope, verified promotion, result publication, same-task retry, stop, and isolated-path behavior with focused deterministic checks.
2. Route ordinary serial frontend and backend tasks through one run-local shared workspace, retaining existing task identity, manifest, promotion, result, retry, and stop paths. Keep special isolated paths unchanged.
3. Run focused deterministic checks for task isolation, same-task candidate retention, add/modify/delete and no-change promotion, cache reuse/invalidation, and isolated-path reconciliation.
4. After those checks pass, run one normal fresh implementation job with no demo bypass and compare per-task timings against the recorded baseline. Do not add a mandatory integration run beyond existing branches.

## Measurement, limits, and rollback

Record per task workspace preparation, dependency preparation, compile, focused test, and verification durations, plus cache hits/misses, source digest, and run-volume disk growth where available. Compare frontend and backend task measurements separately, holding cache policy constant. A single run is directional evidence, not statistical proof; retain the raw measurements and caveats. Adopt the routing change only if preparation savings exceed added synchronization/cache costs without degrading source hashes, verification, or result evidence.

Roll back by routing future tasks to the existing task-specific sandboxes if an owner can observe another task's unaccepted edits, scope decisions differ from the existing runtime checks, frozen evidence changes, exact run cleanup leaves workspace data behind, or verification fails for workspace-lifecycle reasons. Historical runs need no conversion.

## Deferred follow-up

Git may be considered later only if the no-Git pilot demonstrates a real need for source history or rollback that existing manifests, promotion, and task recovery paths cannot meet. Reassess that need from pilot evidence before proposing Git, image/toolchain changes, or additional persistence architecture.
