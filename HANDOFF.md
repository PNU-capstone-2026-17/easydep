# Handoff Notes

Date: 2026-07-20
Workspace: `C:\Users\ask02\Documents\agent test`

## MySQL Artifact Store (added 2026-07-20)

Artifacts are no longer carried in the request body as `state`. Every artifact
is stored in MySQL and looked up by the `app_id` (UUID) issued at session start,
matching the 산출물 정보 클래스 design in the report.

Files:

- `app/db/schema.sql` — reference DDL with design rationale.
- `app/db/models.py` — SQLAlchemy models: `apps`, `artifacts`,
  `artifact_versions`.
- `app/db/session.py` — engine/session, `init_db()` creates the database and
  tables on FastAPI startup.
- `app/repositories/artifact_repository.py` — `create_app`, `load_state`,
  `save_stage`, `list_versions`. `STAGE_ARTIFACTS` maps ArchitectureState keys
  onto artifact rows.
- `verify_db.py` — standalone round-trip check.

Schema shape:

- `artifacts` holds one row per (app_id, artifact_type) = the current artifact.
- `artifact_versions` holds every revision, so feedback never overwrites the
  previous output. `origin` is GENERATED / AUTO_FIXED / FEEDBACK_REVISED.
- Columns are limited to what the code actually reads. Speculative columns
  (phase, content_format, owner_id, title, per-row audit timestamps) and the
  `feedbacks` table were removed on 2026-07-20: they were written but never
  read, and none of them appear in the design document. Anything needed later
  can be added with ALTER TABLE, unlike revision history, which cannot be
  reconstructed after the fact.
- `artifact_type` is VARCHAR, not ENUM, so implementation/testing artifacts can
  be added without a migration.
- Only final artifacts are stored. The BCE element JSON is deliberately NOT
  persisted (see below).

### Why intermediate data is not stored

BCE storage was tried and removed on 2026-07-20. Two reasons:

1. Nothing is lost. `generate_plantuml_from_bce_json` writes every BCE field
   into the PlantUML (className, stereotype, fields, methods, description as a
   note, relationship type as the arrow symbol), so the elements are recoverable
   from the final artifact.
2. It goes stale. Feedback revision calls `revise_puml_with_llm`, which edits
   the PlantUML directly and never updates the BCE JSON. After one revision the
   two disagree, and a traceability graph built from BCE would link elements
   that no longer exist in the diagram.

The planned traceability graph (sequence participants → class diagram classes)
should therefore parse the FINAL artifact of each version into its own tables,
e.g. `artifact_elements` (version, element kind, name) and `element_links`
(from element, to element, relation). That stays correct across revisions and
gives a per-version view of the graph.

`.env` keys: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.

API (all state now comes from the database):

```text
POST /api/apps                                          issue app_id
GET  /api/apps                                          list sessions
GET  /api/apps/{app_id}                                 load all artifacts
POST /api/apps/{app_id}/stages/{stage}/generate
POST /api/apps/{app_id}/stages/{stage}/feedback
GET  /api/apps/{app_id}/stages/{stage}/versions         revision history
GET  /api/apps/{app_id}/stages/{stage}/versions/{n}
GET  /api/apps/{app_id}/stages/{stage}/image.{png|svg}  rendered on demand
```

Diagram images are NOT stored in the database. They are re-rendered from the
stored PlantUML text into `outputs/{app_id}/`, which is a disposable cache.
That directory is per-app on purpose: previously every user shared filenames
like `outputs/bce_class_diagram.puml` and concurrent sessions overwrote each
other. The public `/outputs` static mount was removed for the same reason.

The frontend keeps the app_id in `localStorage` and restores artifacts from the
database on load.

### Concurrent generation lock

`artifacts.status = GENERATING` is claimed before any generation starts, through
a single conditional UPDATE, so only one request per (app_id, artifact_type) can
run. A second request gets 409 immediately instead of failing later on the
`uq_versions_artifact_no` unique constraint. Different stages of the same app do
not block each other.

The claim is a lease, not a permanent flag: `generation_started_at` is stamped
with it, and a request may take over a GENERATING row older than
`STAGE_LOCK_LEASE_SECONDS` (default 900). Without that, a worker crashing
mid-generation would lock the artifact forever.

On release, an artifact that already has a version returns to READY even if the
run failed, so a failed regeneration never discards the previous good output.

Note: `init_db()` uses `create_all`, which does not ALTER existing tables. When a
model column changes, apply it to an already-provisioned database by hand, or
drop the database and let startup recreate it (the local database was recreated
this way when the schema was minimized).

Verified on 2026-07-20: schema creation, save/load round trip, version history,
per-app on-demand rendering, prerequisite guard (409), unknown app id (404),
malformed app id (400), and real LLM class-diagram generations persisted and
re-read from MySQL. Re-verified end to end after the schema was minimized.

Lock verified the same day: duplicate claim rejected, other stages unaffected,
re-claim after release, stale-lease takeover, and two concurrent HTTP generate
requests where one returned 200 (8.8s) and the other 409 (15ms).

## Current Goal

Build a LangGraph-based architecture artifact workflow:

1. Class diagram generation
2. Sequence diagram generation
3. API specification generation
4. ERD generation
5. Deployment diagram generation

Each artifact should be generated in order. The user should not freely skip ahead.
After each artifact is generated, the user can enter natural-language feedback.
The LLM should revise that artifact using the feedback.
For PlantUML artifacts, syntax errors should be extracted using the logic from
`puml_error.py`, passed to the LLM, and retried until the artifact compiles.
Set `MAX_REVISION_ATTEMPTS` to cap the retries; 0 (the default) means no cap.

## Important Clarification

`BCE JSON` is not a final user-facing artifact.
It is internal intermediate data for class diagram generation:

- Boundary
- Control
- Entity

It should not appear as a separate tab/output in the frontend.

## Files Added / Changed

- `server.py`
  - FastAPI app.
  - Serves `frontend/index.html`.
  - Exposes stage APIs:
    - `POST /api/stages/{stage}/generate`
    - `POST /api/stages/{stage}/feedback`
  - Enforces stage prerequisites.

- `frontend/index.html`
  - Static workflow UI.
  - Shows ordered steps:
    - Class Diagram
    - Sequence Diagram
    - API Specification
    - ERD
    - Deployment Diagram
  - Removed BCE JSON tab.
  - Current stage generation and feedback controls exist.

- `app/services/bce_class_extractor.py`
  - Based on user-provided `2.1_class_extractor.py`.
  - Extracts BCE JSON from scenario through OpenAI-compatible API.
  - Uses `.env` keys:
    - `BASE_URL`
    - `API_KEY`
    - `CLASS_EXTRACTOR_MODEL` or `DESIGN_AGENT_MODEL`

- `app/services/plantuml_class_diagram.py`
  - Based on user-provided `3.1_class_diagram_generator.py`.
  - Converts BCE JSON to PlantUML class diagram.

- `app/services/plantuml_error.py`
  - Based on user-provided `puml_error.py`.
  - Extracts detailed PlantUML error hints from SVG output.

- `app/services/llm_artifacts.py`
  - LLM generation/revision helpers for later artifacts and feedback.
  - Revises PlantUML/JSON artifacts using natural-language feedback and errors.

- `app/services/artifact_validation.py`
  - PlantUML validation wrapper.
  - API spec validation helper.

- `app/nodes/artifact_generation.py`
  - Sequence/API/ERD/Deployment nodes now call LLM helpers.

- `app/graphs/class_diagram_graph.py`
  - Class diagram graph was adjusted toward:
    - generate
    - validate
    - feedback/revise loop

## Current Known Issue

Clicking generate was failing because the first class diagram stage calls the
external LLM API. This was investigated further on 2026-07-10.

Resolved/confirmed items:

- A tiny LLM API request succeeds against `nvidia/nemotron-3-super-120b-a12b`.
- The original class extraction failure was not only network latency; the model
  sometimes returned JSON that was not strict enough for `json.loads`.
- `app/services/bce_class_extractor.py` now requests JSON mode where supported
  and repairs common JSON formatting issues before parsing.
- A harmless synthetic class-generation request through FastAPI succeeded.
- Sequence generation after class diagram generation succeeded.
- Class diagram feedback revision succeeded.

Observed timing:

- Synthetic class generation through API: roughly 28 seconds in one successful run.
- Sequence generation through API: roughly 40 seconds in one successful run.
- Class feedback revision through API: roughly 114 seconds in one successful run.

Historical observations:

- Earlier, sandboxed server runs failed with:
  - `openai.APIConnectionError`
  - Windows socket access denied
- Running the server with escalated permissions removed that socket-denied error.
- However, the LLM request was still slow/hanging during class extraction.
- `from openai import OpenAI` was also slow on first import, so `server.py`
  currently warms it up during FastAPI startup.

Current timeout defaults were changed to be more realistic:

- `LLM_TIMEOUT_SECONDS`: default `120`
- `LLM_WALL_TIMEOUT_SECONDS`: default `150`
- `LLM_MAX_RETRIES`: default `0`

For quick debugging, run the server with shorter env vars:

```powershell
$env:LLM_WALL_TIMEOUT_SECONDS='10'
$env:LLM_TIMEOUT_SECONDS='10'
$env:LLM_MAX_RETRIES='0'
& "C:\Users\ask02\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8000
```

For normal testing:

```powershell
& "C:\Users\ask02\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## What To Check Next

1. Open `http://127.0.0.1:8000`.
2. Test the browser button path, not only direct API calls.
3. Confirm the UI remains understandable during long feedback calls.
4. Continue testing:
   - API specification generation
   - ERD generation
   - Deployment diagram generation
   - Feedback revision for sequence/API/ERD/deployment
5. Consider adding a visible spinner/progress text if the current status text is
   not enough for long LLM calls.

## Current Server State

After the 2026-07-10 fix session, the FastAPI server may still be running on
port `8000` if the assistant left it active for browser testing. Check with:

```powershell
netstat -ano | findstr :8000
```

## Do Not Forget

Do not show `BCE JSON` as a final artifact in the UI.
It is internal class extraction state only.
