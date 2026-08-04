# Requirements-to-implementation orchestration

This package calls the member-owned requirements, design, and implementation
agents without changing their internal workflows. Testing is intentionally not
connected yet.

## Stage boundary

1. Requirements analysis produces traceable use cases and `resource_spec`.
2. Design produces class, sequence, OpenAPI, ERD, and logical deployment artifacts.
3. `depkb` enriches the deployment view for Docker on a VM.
4. The graph pauses before implementation.
5. After approval, an LLM creates a **provisional** sizing/cost placeholder.
   It is explicitly unmeasured and will be replaced by the planned recommender.
6. The implementation workflow generates, verifies, repairs, and checkpoints code.

Public resume helpers are in `graph.py`:

- `complete_design(run_id)` stops at the implementation boundary.
- `start_implementation_from_completed_design(design_run_id)` reuses cached design.
- `complete_implementation(run_id)` approves and resumes implementation tasks.

The implementation adapter maps the repository-wide `.env` model settings to
the worker, preserves its transmission approval gate, and normalizes the design
contract for the bundled BCE generator. Untyped design attributes and parameters
are temporarily represented as `String`; this assumption must not be confused
with a design decision.

## Multi-application design evaluation

Run all samples, preserving successes across retries:

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m app.core.orchestration.sample_evaluation --resume
```

Results are written to `artifacts/orchestration/sample-evaluation/<sample>/`.
Each result contains the raw response, requirements/design artifacts, both
deployment diagrams, constraint provenance, and structural inspection.

Observed live results on 2026-08-05:

| Sample | Result | Requirements / actors / use cases |
|---|---|---:|
| shopping_mall | structural checks passed | 9 / 3 / 4 |
| toystore | structural checks passed | 30 / 4 / 10 |
| cloud_native_voucher_medium | structural checks passed | 18 / 5 / 13 |
| note_taking | API timeout; retry required | - |
| bank_of_anthos | host `MemoryError`; retry required | - |

PlantUML validation is currently environment-unavailable because the configured
`plantuml.jar` is absent. This is reported separately and is not counted as a
model syntax pass. Voucher also exposed mojibake in a depkb open question.

## Live implementation evidence

The shopping-mall design was resumed into implementation. Sixteen of eighteen
tasks completed across process/time-limit restarts. The remaining wiring task is
blocked on this host by repeated JVM native-memory crashes; successful task
checkpoints remain reusable. This is not recorded as a completed implementation.
