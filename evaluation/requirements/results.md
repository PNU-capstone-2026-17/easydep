# Requirements benchmark results

Model: `openai/gpt-oss-120b`, temperature 0. Development results compare the same four
inputs before and after repair-context and static-first validation changes.

## Development macro totals

| Metric | Before | After | Change |
|---|---:|---:|---:|
| LLM calls | 100 | 92 | -8.0% |
| Total tokens | 287,137 | 260,942 | -9.1% |
| Wall time | 430.6 s | 338.0 s | -21.5% |
| Remaining specification issues | 10 | 7 | -30.0% |
| FR coverage | 1.0 | 1.0 | unchanged |
| Actor recall | 1.0 | 1.0 | unchanged |
| Explicit role-fact accuracy | 1.0 | 1.0 | unchanged |

The selected generic changes were:

- provide the previous structured specification to the repair call;
- defer semantic LLM validation until deterministic structural checks pass;
- persist wall-clock time in run metrics.

`dev_notification_delivery` increased from two to three remaining specification issues. No
application-specific prompt adjustment was made because macro quality and cost improved.

## Frozen holdout

| Application | FR coverage | Actor recall | Role accuracy | Spec issues | Calls | Tokens | Wall time |
|---|---:|---:|---:|---:|---:|---:|---:|
| telehealth | 1.0 | 1.0 | 0.67 | 4 | 32 | 100,582 | 99.6 s |
| logistics | 1.0 | 1.0 | 1.0 | 3 | 21 | 59,275 | 79.7 s |
| partner reporting | 1.0 | 1.0 | 1.0 | 0 | 30 | 92,222 | 111.4 s |

Telehealth exposed one unresolved modeling limitation: the Pharmacy receives system output but
does not provide a service to the system, while the Clinician owns the enclosing prescription
goal. The current `primary_actor` / `supporting_actors` pair cannot represent that participation
cleanly. Do not tune against this holdout item directly. Add a different outbound-recipient case
to the development split before evaluating a generic role-model extension in a later cycle.

Artifacts are ignored by Git; the recorded run directories are:

- Development before: `run_20260804T175221Z_c8a9c320f0`,
  `run_20260804T175501Z_c7c343bffd`, `run_20260804T175802Z_dd0eab1e53`,
  `run_20260804T180040Z_94db103625`
- Development after: `run_20260804T180533Z_c8a9c320f0`,
  `run_20260804T180714Z_c7c343bffd`, `run_20260804T180919Z_dd0eab1e53`,
  `run_20260804T181145Z_94db103625`
- Holdout: `run_20260804T181429Z_34dd7c034b`, `run_20260804T181636Z_128d3d409f`,
  `run_20260804T181902Z_f6ab50e564`
