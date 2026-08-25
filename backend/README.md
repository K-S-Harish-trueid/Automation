# K2 Automation — Backend

FastAPI service that runs the K2 KYC data-preparation pipeline (see
`K2 Data Preparation process Flow.docx` in the project root) against an
uploaded CSV/XLSX, pausing at the steps that need a human to review or fill
in data, and serving the wizard frontend at `/`.

## Setup

Use the Python interpreter that will run the app:

```
python -m pip install -r requirements.txt
```

Needs a reachable **PostgreSQL** server for the historical-override store
(see below) — set `DATABASE_URL`, e.g.:

```
postgresql+psycopg://postgres:1234@localhost:5433/k2_historical
```

| part | value | |
|---|---|---|
| scheme / driver | `postgresql+psycopg` | SQLAlchemy dialect + driver (psycopg v3) |
| username | `postgres` | |
| password | `1234` | throwaway local-dev value -- change for anything beyond local dev |
| host | `localhost` | |
| port | `5433` | **not the Postgres standard (5432)** -- specific to the dev machine this default was written on (two Postgres versions installed side by side); a fresh install elsewhere is almost certainly 5432 instead |
| database name | `k2_historical` | |

Shape: `scheme://username:password@host:port/database_name`.

Falls back to that exact local-dev connection string if `DATABASE_URL` isn't
set -- **do not rely on that fallback beyond local dev**: it silently fails
closed, not loud. A bad/unreachable `DATABASE_URL` doesn't crash the app --
`historical_db.has_data()` catches every connection error and just reports
"not seeded," so historical override quietly stops working with no error
anywhere. Set `DATABASE_URL` explicitly for every real deployment. The
target database is created automatically on first connect if it doesn't
exist yet; you still need Postgres itself installed and running.

## Run

From this `backend/` folder:

```
python -m uvicorn app.main:app --port 8000
```

Then open **http://localhost:8000** — the frontend (`../frontend`) is served
from the same process, so there's nothing else to start.

Add `--reload` during development to auto-restart on code changes.

From the project root instead of `backend/`, `run.py` wraps the same uvicorn
app with a couple of extra conveniences (`--debug` for verbose logging +
auto-reload + in-browser tracebacks):

```
python run.py [--port 8000] [--host 0.0.0.0] [--reload] [--debug]
```

Defaults to `--host 0.0.0.0`, not `127.0.0.1` — reachable from other devices
on the same network out of the box, not just `localhost`. Prints the
machine's LAN IP on startup (e.g. `http://192.168.1.42:8000`) when bound
that way, so there's something to actually copy for a phone/another laptop
on the same network. See the **Security note** below before relying on
that for anything beyond local-network testing.

### Try it with dummy data

`../dummy_data/` has a small 20-row fixture built to hit every validation
rule (bad names, bad IDs, bad DoBs, bad addresses, missing phones). See
`../dummy_data/README.md` for which account triggers what. Upload
`dummy_raw.csv` at the start screen. The historical-override reference data
(what used to be a separate `dummy_replace_reference.xlsx` upload) now comes
from `historical.db` instead — see "Historical override data" below for how
to seed it. `dummy_cms_export.csv` predates the Stage 1/2/3 handoff below and
is no longer consumed by an automatic stage — CMS mobile/card data now comes
in through two direct CMS export files at Stage 3 instead (see below).

### Historical override data (Postgres `historical` table)

The `replace` stage (see the stage table below) matches each account against
a historical reference table kept in Postgres (see `DATABASE_URL` above),
not an uploaded file — parsing the ~130MB source xlsx on every job was too
slow (see `app/historical_db.py`'s docstring), so it's imported once and
queried from there instead. A fresh Postgres database starts with no
`historical` table at all until seeded.

Seed or reseed it any of these ways, all producing an identical table:

- **From the web UI** — the Dashboard's "Historical reference data" panel
  (and the historical-override warning gate itself, if a job hits it with
  the store empty) lets an operator upload an xlsx/csv straight through the
  browser. Calls `POST /api/historical/seed`; `GET /api/historical/status`
  reports whether it's seeded and how many rows.
- **`python backend/data/seed_historical.py [--source path] [--database-url url]`**
  — same import, run from a terminal. Defaults to this repo's
  `dummy_data/Stage 1/Historical_Dataset.xlsx` fixture and
  `DATABASE_URL` (or its local-dev default). If the source file isn't found
  at its default path and `--source` wasn't given, it asks for the path
  interactively instead of failing outright.
- **`seed_historical.bat`** (project root, Windows) — double-click wrapper
  around the script above, using this repo's own `venv`.
- **`python backend/create_xlsx_2_db.py <source.xlsx>`** — a generic,
  standalone xlsx→**SQLite** converter with no dependency on this app's
  schema (arbitrary columns, table name `data` by default, writes a local
  `.db` file). Unrelated to the live Postgres store — useful for quickly
  poking at any xlsx as a queryable database, not for seeding `replace`.

If a job reaches the `replace` stage while the historical store is empty or
unseeded, the pipeline doesn't error out or silently skip it — it pauses
into a gate (`GET /jobs/{id}/current` reports `type: "historical_warning"`)
showing the operator a warning, an inline "seed now" option, and a Continue
button. `POST /jobs/{id}/continue-historical` resumes it (taking a rollback
checkpoint first, same as any other gate) — if the store got seeded while
paused, the real override applies on resume instead of being skipped.

### Hosting the frontend and backend separately

By default the backend serves the frontend itself (same origin, `config.js`
defaults to `"/api"`, nothing to configure). To host them apart instead —
e.g. frontend on static hosting, backend on your own machine/server where the
data stays — two things need to change:

1. **Frontend**: edit `../frontend/config.js` and point `K2_API_BASE` at the
   backend's real address, e.g.:
   ```js
   window.K2_API_BASE = "http://192.168.1.20:8000/api";
   ```
   Then serve the `frontend/` folder from wherever (any static host, or even
   `python -m http.server` in that folder).

2. **Backend CORS**: once the frontend is on a different origin, restrict
   which origin(s) may call the API instead of the wide-open default:
   ```
   K2_ALLOWED_ORIGINS=https://your-frontend.example.com
   ```
   (Comma-separate multiple origins. Omit the env var to leave it wide open,
   which is fine for local-network testing but not once anything public can
   reach the port.)

**Security note:** this data is KYC PII (names, national IDs, DoBs, phone
numbers, addresses). The API has no authentication — anyone who can reach the
backend port can create jobs and pull data back out. That's acceptable for
`localhost` or a trusted local network, but if the backend becomes reachable
from the internet (port-forwarded, tunneled, deployed to a cloud VM, etc.) it
needs an auth layer (API key header at minimum) before that happens — worth
flagging before you expose it beyond your own machine/network.

## Environment variables

Copy `.env.example` (project root) to `.env` and edit values there — `.env`
is gitignored, real values never get committed. Every var is optional;
anything left out (or the whole file missing) falls back to the exact
default already hardcoded in the app, so an absent `.env` changes nothing.
17 vars total:

- 12 are business-rule thresholds (DOB cutoff, age of majority, ID format
  regexes, etc.) -- see `app/rules_config.py` for the full explanation of
  each one.
- `DATABASE_URL` -- see "Setup" above for the full breakdown.
- `K2_DEBUG`, `K2_ALLOWED_ORIGINS` -- see `app/main.py`.
- `K2_OPERATOR_NAME` -- see `app/routes/stage.py` (attributed on manual-edit
  audit events).
- `K2_MAX_STORED_JOBS` -- see "Job retention" below. Read from
  `app/rules_config.py` (not `store.py` directly) specifically so it's
  guaranteed to see `.env` -- `.env` is loaded as an import-time side effect
  of `rules_config.py` itself, and `store.py` used to get imported earlier
  in `main.py`'s startup than anything that triggered that load, so a value
  set only in `.env` was silently never picked up. Fixed 2026-08-24.

`.env.example` is intentionally just plain `KEY=value` lines, no comments --
this section plus each file's own docstring is the explanation.

## Project layout

```
backend/
  app/
    main.py         FastAPI app setup, router mounting
    routes/         API endpoints (jobs, stage actions, rollback), thin --
                    delegate the actual work to pipeline/ and store.py
    background.py   Runs the auto-stage chain in a background thread after
                    each action endpoint returns
    store.py        Per-job state: in-memory dict + a disk snapshot (parquet
                    + status.json) under jobs/<job_id>/, survives a restart.
                    Split into four smaller modules below (2026-08-25) --
                    store.py itself now just owns the in-memory dict and the
                    core read/write ops, and re-exports the rest so every
                    existing `store.xxx(...)` call site is unaffected
    job_paths.py    JOBS_DIR, job_id validation regex, job directory
                    resolution -- split out of store.py so job_audit.py/
                    checkpoints.py/job_retention.py don't need to import
                    store.py itself (store.py imports from all three, so
                    that would be circular)
    job_audit.py    The append-only per-job audit.jsonl -- append/read/count
                    events (split out of store.py)
    checkpoints.py  Checkpoint create/list/rollback (split out of store.py) --
                    works entirely through store.py's own public functions
                    (get_df/get_status/set_df/set_status/persist) rather
                    than reaching into its private in-memory job dict
    job_retention.py  Capacity/retention eviction of old job folders (split
                    out of store.py) -- see "Job retention" below
    historical_db.py  Postgres cache backing the `replace` stage (see
                    "Historical override data" above) -- has_data(),
                    load_reference_df(), seed_from_file/seed_from_bytes,
                    stats(), upsert_rows(), get_engine() (shared connection,
                    also used by audit_log_db.py/generated_records_db.py/
                    address_pools_db.py below)
    helpers.py       Cross-cutting glue: stage navigation/progress,
                    audit-event recording, quality summary, edit validation
    xlsx_export.py   Every xlsx read/write in the app: the Stage 1/2
                    dispatch-file writers, the final flat output, the
                    sheet/row-count summary shown under every download
                    button (split out of helpers.py, which used to hold
                    this alongside unrelated concerns)
    audit_log_db.py  Best-effort Postgres mirror of every job's local
                    audit.jsonl, queryable across all jobs at once (table
                    `audit_log`) -- local audit.jsonl stays the source of
                    truth the app itself depends on; this is a convenience
                    mirror only, never blocks a job if Postgres is down
    generated_records_db.py  Append-only Postgres log (table
                    `generated_records`) of every fabricated Civil ID
                    (default_id stage) and auto-filled address (address_fix
                    stage), tagged with the job that generated it
    address_pools_db.py  The `address_fix` stage's denylist + per-province
                    replacement-address pool (tables `address_denylist`/
                    `address_pool`) -- used to be hardcoded in
                    pipeline/stages/address_fix.py, now editable directly in
                    Postgres (pgAdmin or any SQL client), no code change or
                    redeploy needed for a new entry. Cached in memory with a
                    60s TTL; degrades to empty results (never fails a job)
                    if Postgres is unreachable
    pipeline/       Stage logic ported from clean.py / replace.py / cmsdata.py /
                    idvalid.py / dobvalid.py / pah3.py / mobileupd.py, one file
                    per pipeline stage -- see below
    routes/historical.py  Web-based (re)seeding of historical.db -- GET
                    /api/historical/status, POST /api/historical/seed
    routes/handoff.py  The Stage 1/2/3 reviewer handoff: dispatch/download
                    the Stage 1 and Stage 2 xlsx files, and ingest Naresh's/
                    Haider's responses. Kept out of stage.py since its
                    upload/merge shape (three files at once for Stage 3,
                    blank-means-no-change merges) differs from the generic
                    upload-stage gate.
  data/             historical.db + the scripts that build/seed it
                    (seed_historical.py, create_xlsx_2_db.py,
                    migrate_address_pools.py) -- gitignored except those
                    scripts, see "Historical override data" above.
                    migrate_address_pools.py is one-time: it ports the
                    original hardcoded address denylist/pool into Postgres
                    for a brand-new database -- safe to re-run (replaces
                    both tables wholesale) but only needed once per fresh
                    Postgres instance; after that, edit the tables directly
  jobs/             Runtime data, one folder per job (gitignored, safe to delete when idle)
  requirements.txt
```

### `pipeline/` layout

One file per stage, named after the stage id (matches `rules/NN-<id>.txt`),
so a change scoped to one stage only touches that stage's file:

```
pipeline/
  __init__.py       Re-exports the public surface (pipeline.STAGES,
                    pipeline.mask_name_invalid(...), etc.) regardless of
                    which file below something actually lives in
  registry.py        The wiring list: STAGES order, AUTO_HANDLERS,
                    UPLOAD_HANDLERS, CONFIRM_HANDLERS, MANUAL_STAGES --
                    only changes when a stage is added/removed/reordered
  toolbox.py         Helpers 2+ stages genuinely share: generic Series
                    helpers (series_available, series_has_letter, ...) and
                    the ID_TYPE/ID_NUMBER check reused by id_dob_validate,
                    final_id_check, and default_id
  stages/
    clean.py, replace.py, reset_cms.py, name_validate.py,
    id_dob_validate.py, address_fix.py, mobile_fill.py, final_id_check.py,
    default_id.py
                    Each file: that stage's own constants, validator,
                    reason messages, and handler -- nothing else.
                    (cms_integration.py now only holds the CMS_UPDATE_COLS
                    column list -- CMS mobile/card data comes in through two
                    direct CMS export files at Stage 3, not an automated
                    merge stage. stage1_dispatch, stage2_dispatch, and done
                    have no pipeline-layer stage code -- handled directly in
                    routes/.)
  stage_merge.py    Apply/validate logic for the Stage 1/2/3 handoff --
                    matches by ACCOUNT_NUMBER, overwrites a cell only where
                    the reviewer's response (or CMS export) provides a
                    non-blank value.
```

## Pipeline stages

Each stage is one of `auto`, `upload`, `manual_edit`, `stage1`, `stage2`,
`confirm`, or `done`; the server auto-runs `auto` stages back to back and
stops on the others until the frontend (or an API call) resolves them. (No
stage is actually type `upload` right now — the mechanism is still there,
`UPLOAD_HANDLERS` in `registry.py`, for if a future stage needs a real
file-upload gate again.)

| # | Stage id | Type | What happens |
|---|---|---|---|
| 1 | `clean` | auto | Strips line breaks from every field |
| 2 | `replace` | auto | Matches every account against `data/historical.db` (see "Historical override data" above) on `ACCOUNT_NUMBER`, overwriting all fields except `CARD_NUMBER`. If the store is empty/unseeded, pauses into a `historical_warning` gate instead of silently skipping it — see above |
| 3 | `reset_cms` | auto | Blanks `ACCOUNT_TYPE`/`CARD_TYPE`/`CARD_PROGRAM`/`CARD_STATUS`/`DATE_OPENED` |
| 4 | `id_dob_validate` | manual_edit | Flags invalid `ID_TYPE`/`ID_NUMBER`/DoB for inline correction |
| 5 | `address_fix` | auto | Auto-fills accounts with a missing address, an address with no letters at all (e.g. a phone number or placeholder), or an exact match against a known-junk denylist (e.g. "JUNE", a single letter) — replacement pulled from a Baghdad/province address pool, ported from the legacy `pah3.py` script. Rows whose province has no pool entry are left invalid, with no later step re-checking them — see `rules/06-address_fix.txt` |
| 6 | `mobile_fill` | manual_edit | Flags accounts with no phone number (blank, `XXX_NOT_COLLECTED_XXX`, or all-zero) for inline entry |
| 7 | `stage1_dispatch` | stage1 | Parks the job and hands out two files: `stage1_haider.xlsx` (Mobile flagged rows + a blank CMS sheet for every account, filled in for real later at Stage 3) and `stage1_naresh.xlsx` (ID Corrections + DOB Corrections sheets, every currently invalid row of each). Advanced externally by the Stage 2 page once Naresh's response is uploaded — see "Stage 1/2/3 reviewer handoff" below |
| 8 | `name_validate` | manual_edit | Flags 1-character first/middle/last names for inline correction. Deliberately sits *after* Stage 1 Dispatch, not before — name issues are never in Haider's Stage 1 file, only bundled into the Stage 2 dispatch file |
| 9 | `stage2_dispatch` | stage2 | Parks the job and hands out `stage2_haider.xlsx` (Name Validation + ID Corrections + DOB Corrections sheets: name issues from `name_validate` above, plus whatever's still invalid after Naresh's ID/DOB fixes). Advanced externally by the Stage 3 page once Haider's response(s) are uploaded |
| 10 | `final_id_check` | manual_edit | Last pass on any still-invalid IDs (part of Stage 3's own tail, not a separate trip) |
| 11 | `default_id` | confirm | Assigns a random 8-digit ID (`00######`) + `Civil Id` type to whatever is still invalid (also Stage 3's tail — the frontend renders this straight off the Stage 3 upload, same shared confirm screen used everywhere else) |
| 12 | `done` | done | Final dataset ready to download (Stage 3's last stage) |

`manual_edit` stages page 200 flagged rows at a time; submitting a batch
re-validates and either loads the next batch or advances. Every manual stage
also accepts `force_advance: true` on `/submit` to skip remaining rows without
fixing them.

**Manual-edit stages are currently bypassed entirely**
(`BYPASS_MANUAL_EDIT_STAGES = True` in `app/background.py`): the background
runner auto-advances `name_validate`/`id_dob_validate`/
`mobile_fill`/`final_id_check` the same way it chains `auto` stages, logging a history
entry with how many rows were left flagged. (`address_fix` is a real `auto`
stage now, not a bypassed manual one — it always runs, flag or no flag.)
The frontend never sees the bypassed stages while the flag is on. Flip it to `False` to restore the normal
interactive gate — nothing behind it was removed, and the previously-added
`MANUAL_EDIT_ENABLED` flag (view-only vs. editable table, `app/routes/stage.py`)
still governs behavior whenever a manual_edit stage *is* shown again. A job
already paused at a manual_edit gate when this flag flips on won't
auto-advance retroactively; resolve it with `POST /submit`
`{"edits": [], "force_advance": true}` or restart the job.

## API

All endpoints are under `/api`. The four action endpoints
(`POST /jobs`, `/upload`, `/submit`, `/confirm`) return immediately with
`{"job_id": ..., "status": "processing"}` — the actual pipeline work runs in
a background thread. Poll `/progress` until it stops reporting
`"processing"`, then call `/jobs/{id}` and `/jobs/{id}/current` as usual.

| Method & path | Purpose |
|---|---|
| `POST /uploads/raw-preview` | multipart `file` — preview a raw CSV/XLSX before committing to it: `{filename, file_size, row_count, column_count, columns, missing_required_columns, ...}`. Creates nothing |
| `POST /jobs` | multipart `file` — create a job from the raw CSV/XLSX, kicks off processing in the background |
| `GET /jobs` | List job summaries (id, filename, current stage, row count, status); `?stage_id=<id>` filters to jobs currently parked at that stage — see below |
| `GET /jobs/{id}` | Full status: stage list + statuses + history + `rollback_available`/`rollback_targets` (see "Rollback" below) |
| `GET /jobs/{id}/progress` | `{status, current_step_index, total_steps, current_step_name, percent}` while processing; `status` is `"idle"`/`"done"`/`"error"` (with a `message`) once settled |
| `GET /jobs/{id}/current` | Detail for whatever stage is currently active (shape depends on stage type) |
| `GET /jobs/{id}/raw-upload` | Download the exact bytes originally uploaded for this job, untouched by any stage |
| `POST /jobs/{id}/upload` | multipart `file` — resolve an `upload` stage in the background (no stage currently uses this) |
| `POST /jobs/{id}/continue-historical` | Resume a job paused at the `replace` stage's `historical_warning` gate (empty/unseeded `historical.db`) — takes a rollback checkpoint, then resumes the auto-stage chain |
| `POST /jobs/{id}/update-historical` | Insert-or-replace this **completed** job's own accounts into `historical.db` (keyed on `ACCOUNT_NUMBER`, never duplicates) — only available once the job reaches `done` |
| `POST /jobs/{id}/draft` | `{ edits: [{row_key, field, value}] }` — save in-progress edits for the current `manual_edit` stage without submitting them |
| `DELETE /jobs/{id}/draft` | Clear the saved draft for the current `manual_edit` stage |
| `GET /jobs/{id}/workbook` | Download an Excel workbook covering every `manual_edit` stage reached so far, for offline review/editing |
| `POST /jobs/{id}/submit` | `{ edits: [{row_key, field, value}], force_advance }` — resolve a `manual_edit` stage in the background |
| `POST /jobs/{id}/submit-workbook` | multipart `file` (+ `force_advance`) — same as `/submit`, but the edits come from a completed `/workbook` download instead of inline JSON |
| `GET /jobs/{id}/stage1/haider.xlsx` | Download Haider's Stage 1 file (Mobile flagged rows + blank CMS sheet) while parked at `stage1_dispatch`; repeatable, no side effects |
| `GET /jobs/{id}/stage1/naresh.xlsx` | Download Naresh's Stage 1 file (ID Corrections + DOB Corrections sheets, every currently invalid row of each) while parked at `stage1_dispatch`; repeatable, no side effects |
| `POST /jobs/{id}/stage2/naresh-response` | multipart `file` (2 sheets: ID Corrections, DOB Corrections) — Stage 2's merge step: apply Naresh's returned ID and DOB corrections, advance from `stage1_dispatch` through the `name_validate` gate to `stage2_dispatch` |
| `GET /jobs/{id}/stage2/haider.xlsx` | Download Stage 2's dispatch file for Haider (Name Validation + ID Corrections + DOB Corrections sheets) while parked at `stage2_dispatch`; repeatable, no side effects |
| `POST /jobs/{id}/stage3/haider-response` | multipart `cms_mobile_file` (flat: `ACCOUNT_NUMBER`+`PHONE_NUMBER`) + `cms_card_file` (flat: `ACCOUNT_NUMBER`+`CMS_UPDATE_COLS`, `DATE_OPENED` as M/D/YYYY) + `haider_corrections_file` (3 sheets: Name/ID/DOB) — Stage 3's merge step: apply all three, advance past `stage2_dispatch` into `final_id_check`/`default_id` |
| `POST /jobs/{id}/confirm` | Resolve a `confirm` stage in the background |
| `GET /jobs/{id}/download` | Download the final dataset as `{job_id}_final.xlsx` once the job reaches `done` |
| `GET /jobs/{id}/audit` | JSON `{count, events}` — the last 250 audit events (field-level change log) |
| `GET /jobs/{id}/audit/download` | Download the full audit trail as `{job_id}_audit.xlsx` |
| `POST /jobs/{id}/rollback` | Roll back to the most recent checkpoint — see "Rollback" below |
| `POST /jobs/{id}/rollback/{checkpoint_id}` | Roll back to a specific checkpoint (ids come from `GET /jobs/{id}`'s `rollback_targets`) |
| `GET /api/historical/status` | `{seeded, row_count, seeded_at}` for `data/historical.db` |
| `POST /api/historical/seed` | multipart `file` — (re)seed `historical.db` from an uploaded xlsx/csv, replacing the whole table |

`GET /jobs?stage_id=<id>` filters the job list to jobs currently parked at
that stage — used by the Stage 2/3 pages' job pickers (`stage_id` omitted
keeps the normal unfiltered dashboard listing).

See "Stage 1/2/3 reviewer handoff" below for the shape of the stage xlsx
files. The final `.xlsx` download is different: by then there's nothing
left to review, so it's one flat sheet with every column except
`SMART_IDENTIFIER` (an internal reference used only to help Naresh match ID
documents, dropped before delivery), the same shape as the original raw
import — see `_write_flat_xlsx` in `app/helpers.py`.

Calling an action endpoint again while a job is already processing gets a
`409 Conflict` instead of starting a second run.

### Rollback

Every gate-resolving action (`/upload`, `/submit`, `/submit-workbook`,
`/confirm`, `/continue-historical`) saves a full checkpoint (dataframe +
status snapshot, under `jobs/<job_id>/checkpoints/`) before applying its
change. `GET /jobs/{id}` reports the available ones as `rollback_targets`;
`POST /jobs/{id}/rollback` restores the most recent, `POST
/jobs/{id}/rollback/{checkpoint_id}` restores a specific one by id. Rolling
all the way back to the very first `clean` stage isn't supported — that
would mean re-running the whole pipeline anyway, so starting a new job is the
better path for that case.

## Stage 1/2/3 reviewer handoff

`cms_integration` and the old `send_email` stub are gone. In their place,
after `mobile_fill` the pipeline hands off to two named external reviewers
plus two direct CMS system exports, over three separate, self-contained
stages (not one continuous chain — each one is its own input → merge →
verify → dispatch mini-pipeline), matched by `ACCOUNT_NUMBER` and only
overwriting a cell where the response actually provides a non-blank value
(a reviewer not fixing every row is expected, not an error):

DOB travels with IDs, not with Name: Naresh gets first crack at both (one
workbook, ID Corrections + DOB Corrections sheets), and whatever he can't
resolve of either gets dispatched to Haider as a second-pass file in the
same shape, bundled alongside Name. CMS mobile numbers and card details no
longer come from a reviewer typing them in by hand — they arrive as two
direct exports from the CMS system itself at Stage 3.

1. **Stage 1** (`stage1_dispatch`, in the normal job wizard): download
   `stage1_haider.xlsx` (Missing Mobile Numbers sheet, cut down to
   currently-flagged rows, plus a CMS Data Integration sheet listing every
   account with blank `CMS_UPDATE_COLS` as a starting point — the real CMS
   data arrives later as its own two export files and takes precedence) and
   `stage1_naresh.xlsx` (ID Corrections + DOB Corrections sheets, every
   currently invalid row of each). Share both manually; Stage 1 stops here
   for good — nothing advances it further from inside Stage 1 itself. Name
   issues haven't been checked yet at this point (see `name_validate` below)
   so they're never in this file.
2. **Stage 2** (a separate page, its job picker only listing jobs parked at
   `stage1_dispatch`): input Naresh's completed file, merge his ID and DOB
   fixes, recheck both — `POST /stage2/naresh-response` does that in one
   call and advances the job past `stage1_dispatch`. The job then passes
   through `name_validate` (a real manual-review gate, currently
   auto-bypassed like every other manual_edit stage while
   `BYPASS_MANUAL_EDIT_STAGES = True` in `app/background.py`) before landing
   on `stage2_dispatch`, generating `stage2_haider.xlsx` (Name Validation +
   ID Corrections + DOB Corrections sheets) from whatever's still flagged.
   Once applied, the page hands the job back to the normal wizard, which
   renders Stage 2's own dispatch screen (same one you'd see resuming the
   job normally) with a "Next: Stage 3" shortcut.
3. **Stage 3** (a separate page, its job picker only listing jobs parked at
   `stage2_dispatch`): input three files — CMS Mobile Numbers export (flat:
   `ACCOUNT_NUMBER`+`PHONE_NUMBER`), CMS Card Details export (flat:
   `ACCOUNT_NUMBER`+`CMS_UPDATE_COLS`, `DATE_OPENED` arrives as M/D/YYYY and
   gets normalised to YYYY-MM-DD), and Haider's corrected file (3 sheets:
   Name/ID/DOB) — `POST /stage3/haider-response` applies all three, in that
   order, in one call. The job then proceeds into the unchanged
   `final_id_check` (auto-bypassed, as always) and lands on the existing
   `default_id` confirm gate — Stage 3 hands the job to the normal wizard at
   this point too, so the confirm-click and final-output screens the
   operator sees are the *same shared screens* every job uses, not a
   separate copy.

See `app/pipeline/stage_merge.py` for the merge logic and
`app/helpers.py`'s `_write_stage1_haider_xlsx` / `_write_stage1_naresh_xlsx` /
`_write_stage2_haider_xlsx` for the xlsx writers.

## Stage Tester (hidden page — not linked in the app)

`GET /test` (note: **`/test`, not `/test.html`**) serves a small standalone
page for trying a single stage's rule against typed-in values, without
creating a real job. Two panels: left is the stage picker + input fields +
Run Check/Clear, right is a condensed "Rules followed" summary for whichever
stage is selected (from `_RULES_PANELS` in `app/routes/stage_test.py` —
kept in sync with `rules/NN-*.txt` by hand, same discipline as
`registry.py`'s `instructions` text). A full-width Result panel at the
bottom shows FLAGGED/PASSED plus the exact reason message a real operator
would see; for `address_fix` it also shows a before/after table, since that
stage rewrites the value instead of just flagging it. Each stage also gets
two one-click "fill example" buttons (a flagged case and a passing case) so
you don't have to hand-type test values every time.

Colors are borrowed from the main app's `style.css` tokens (`--canvas`,
`--accent`, etc., hardcoded here rather than importing the stylesheet) so it
doesn't look thrown-together, but it deliberately does NOT reuse the main
sidebar/layout or `app.js` — see "Deliberately kept out of sight" below for
why looking distinct from a real job screen matters here.

It's a thin wrapper around the real per-stage `mask_*`/`reasons` functions
in `app/pipeline/stages/` — it runs your one test row through the actual
pipeline code and throws the row away, so there's no separate "test" logic
to keep in sync with the real rules (the Rules-followed panel text is the
one exception — that's hand-written prose, not derived from code, so it can
drift if a rule changes and this doesn't get updated alongside it).

Testable stages: `name_validate`, `id_dob_validate`, `address_fix`,
`mobile_fill`, `final_id_check` (the ones with a per-row rule to try — whole-
dataset auto stages like `clean`/`reset_cms`/`replace` aren't testable this
way, there's nothing single-row about them).

Deliberately kept out of sight on purpose, not by accident:
- Not linked anywhere in the main app UI.
- Not served as a static file (`app/templates/stage_test.html`, outside
  `frontend/`, read directly by the `/test` route — so `/test.html` 404s).
- `include_in_schema=False` on the page route and its two `/api/stage-test/*`
  endpoints, so it doesn't show up in `/docs` either.
- No login, though — same as every other endpoint here, it's reachable by
  anyone who can reach the port. It's hidden from casual discovery, not
  access-controlled. See the security note at the top of this file.

## Job retention

This pipeline handles KYC PII, so finished jobs don't stick around: at most
the **configured capacity limit** of the most recently completed jobs is kept (both on disk under `jobs/`
and in memory) — whenever a job reaches `done`, and again on server startup,
older completed jobs beyond that cap are deleted automatically, including
their downloadable `.xlsx`. **Jobs still waiting on a manual gate are never
touched**, no matter how many are open at once — only finished ones count
against the cap. Download the final file promptly once a job completes if
you want to keep it; there's no grace period once enough newer jobs finish behind it.

In addition, the workspace uses a rolling total-job limit when a new raw file
or backup is created. The default is 100 jobs and can be changed with
`K2_MAX_STORED_JOBS`; the oldest non-processing job is removed when that limit
would be exceeded.

## Known limitations (prototype)

- `id_dob_validate` can flag tens of thousands of rows on messy source data —
  fine for plumbing, but not realistically an edit-one-by-one UI at that
  volume; a bulk/batch-upload fix path would be needed for production use on
  large invalid-ID counts.
- No auth — intended for local/internal use only.
- Background jobs run on plain Python threads (no task queue), and job state
  isn't locked against concurrent readers during a background write — fine
  for one operator at a time, not for heavy concurrent use.
