# UC1 query serialization partial validation — 2026-09-27

## Finding and change

The saved UC1 failure used an OpenAPI query parameter named `searchCriteria` whose schema is an object with `courseId`, `termId`, and `instructorId`. The parameter omitted `style` and `explode`, so the OpenAPI defaults apply: `style=form`, `explode=true`. The prior URL builder treated the object as a repeated scalar parameter and produced keys such as `searchCriteria=termId`, losing the values. OpenAPI's [Parameter Object serialization rules](https://spec.openapis.org/oas/v3.1.1.html#parameter-object) define the defaults.

`app/testing/utils/functional_executor.py::operation_url` now uses the declared query parameter metadata to serialize object values: default/form+explode emits property-value pairs, form with `explode=false` emits a comma-delimited key/value sequence, and `deepObject` with `explode=true` emits `name[property]` pairs. Existing scalar and array encoding is preserved. No generated application, database, or Testing report was edited.

Focused no-network validation passed: 5 cases across `test_query_object_uses_openapi_style_and_explode_defaults` (3 variants), `test_query_array_keeps_repeated_value_encoding`, and the existing `test_explicit_query_header_and_body_replacement_are_sent`. `git diff --check` passed; only line-ending normalization warnings were emitted.

## Saved UC1 replay evidence

The replay used the persisted Testing report for command `8101461e-2be4-4d3e-a5bc-016ef0b72b29`, app `f01ededa-07de-4ced-8ea4-8d5348994a0e`, job `15a6e59f87bb41fba970a0151001dba0`, run `run_8493acb344b5`. It used the saved `workflow-UC1` candidate plan, workflow inputs, concrete input values, and that run's OpenAPI document. It did not regenerate a plan or run the other Testing gates.

- First partial replay: gate returned `FAIL` in 28.941 s. The bounded output projection omitted nested `gateEvidence`, so the HTTP result and failure detail are unproven; this attempt is not counted as transport validation.
- Second partial replay: gate `PASS` in 146.494 s including application startup. Actual request: `GET /offerings?courseId=CS101&termId=Fall2024&instructorId=john.doe`; response HTTP 200 with body `[]`. Contract validation and saved `$statusCode == 200` criterion passed; `workflow-UC1` semantic status was `PASS` on one attempt.
- Canonical application source SHA-256 before and after, and temporary-copy source SHA-256: `029ab01ed0ce1eb4cb6f3d1665cd525c1ce9a3e006021af9f3dfd5199938fcb4`. OpenAPI SHA-256 before and after: `d617db979f65a9a7ba42102b590cf301703996da64d60588799a9a4b2238592d`.
- The exact task temporary directory was removed. Docker checks for the execution's exact container and network returned exit 0 with no remaining resources.

This proves only the saved UC1 workflow against a temporary copy. The full Testing command remains separate and is not claimed complete by this partial validation.
