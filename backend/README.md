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

## Run

From this `backend/` folder:

```
python -m uvicorn app.main:app --port 8000
```

Then open **http://localhost:8000** — the frontend (`../frontend`) is served
from the same process, so there's nothing else to start.

Add `--reload` during development to auto-restart on code changes.

### Try it with dummy data

`../dummy_data/` has a small 20-row fixture built to hit every validation
rule (bad names, bad IDs, bad DoBs, bad addresses, missing phones) plus the
two reference files needed at the upload gates. See
`../dummy_data/README.md` for which account triggers what. Upload
`dummy_raw.csv` at the start screen, `dummy_replace_reference.xlsx` at the
"Data Consistency Update" stage, and `dummy_cms_export.csv` at the "CMS Data
Integration" stage.

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
   K2_ALLOWED_ORIGINS=https://your-frontend.example.com "d:\K2 Automation\k2\Scripts\python.exe" -m uvicorn app.main:app --port 8000 --host 0.0.0.0
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
                    + status.json) under jobs/<job_id>/, survives a restart
    helpers.py       Cross-cutting glue: audit-event recording, quality
                    summary, xlsx export writers
    pipeline/       Stage logic ported from clean.py / replace.py / cmsdata.py /
                    idvalid.py / dobvalid.py / pah3.py / mobileupd.py, one file
                    per pipeline stage -- see below
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
    id_dob_validate.py, address_fix.py, mobile_fill.py, cms_integration.py,
    final_id_check.py, default_id.py
                    Each file: that stage's own constants, validator,
                    reason messages, and handler -- nothing else. (send_email
                    and done have no pipeline-layer code -- handled directly
                    in routes/.)
```

## Pipeline stages

Each stage is one of five types; the server auto-runs `auto` stages back to
back and stops on the others until the frontend (or an API call) resolves them.

| # | Stage id | Type | What happens |
|---|---|---|---|
| 1 | `clean` | auto | Strips line breaks from every field |
| 2 | `replace` | upload | Upload a historical override file; merges on `ACCOUNT_NUMBER`, all fields except `CARD_NUMBER`. Skippable — `POST /jobs/{id}/skip` advances without a file, leaving accounts at their current values |
| 3 | `reset_cms` | auto | Blanks `ACCOUNT_TYPE`/`CARD_TYPE`/`CARD_PROGRAM`/`CARD_STATUS` |
| 4 | `name_validate` | manual_edit | Flags 1-character first/middle/last names for inline correction |
| 5 | `id_dob_validate` | manual_edit | Flags invalid `ID_TYPE`/`ID_NUMBER`/DoB for inline correction |
| 6 | `address_fix` | auto | Auto-fills accounts with a missing address, an address with no letters at all (e.g. a phone number or placeholder), or an exact match against a known-junk denylist (e.g. "JUNE", a single letter) — replacement pulled from a Baghdad/province address pool, ported from the legacy `pah3.py` script. Rows whose province has no pool entry are left invalid, with no later step re-checking them — see `rules/06-address_fix.txt` |
| 7 | `mobile_fill` | manual_edit | Flags accounts with no phone number (blank, `XXX_NOT_COLLECTED_XXX`, or all-zero) for inline entry |
| 8 | `cms_integration` | upload | Upload a CMS export; merges `CARD_NUMBER`/`ACCOUNT_TYPE`/`CARD_TYPE`/`CARD_PROGRAM`/`CARD_STATUS`. Skippable; also shows a disabled "Invoke via API (coming soon)" placeholder |
| 9 | `send_email` | email | Stub checkpoint: download the in-progress dataset to share manually; "Continue" just advances (no real send yet). Skippable |
| 10 | `final_id_check` | manual_edit | Last pass on any still-invalid IDs |
| 11 | `default_id` | confirm | Assigns a random 8-digit ID (`00######`) + `Civil Id` type to whatever is still invalid |
| 12 | `done` | done | Final dataset ready to download |

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
| `POST /jobs` | multipart `file` — create a job from the raw CSV/XLSX, kicks off processing in the background |
| `GET /jobs/{id}` | Full status: stage list + statuses + history |
| `GET /jobs/{id}/progress` | `{status, current_step_index, total_steps, current_step_name, percent}` while processing; `status` is `"idle"`/`"done"`/`"error"` (with a `message`) once settled |
| `GET /jobs/{id}/current` | Detail for whatever stage is currently active (shape depends on stage type) |
| `POST /jobs/{id}/upload` | multipart `file` — resolve an `upload` stage in the background |
| `POST /jobs/{id}/skip` | Skip the current stage without doing its normal action (only stages marked `skippable`: `replace`, `cms_integration`, `send_email`) |
| `POST /jobs/{id}/submit` | `{ edits: [{row_key, field, value}], force_advance }` — resolve a `manual_edit` stage in the background |
| `POST /jobs/{id}/send-email` | Advance the `send_email` stage (stub — no data change, no real email sent yet) |
| `GET /jobs/{id}/email/download` | Download the in-progress dataset as `{job_id}.xlsx` while parked at the `send_email` stage; repeatable, no side effects |
| `POST /jobs/{id}/confirm` | Resolve a `confirm` stage in the background |
| `GET /jobs/{id}/download` | Download the final dataset as `{job_id}_final.xlsx` once the job reaches `done` |
| `GET /jobs/{id}/audit/download` | Download the audit trail as `{job_id}_audit.xlsx` |

The `send_email` download is one workbook split into a separate named sheet
per manual-review topic (`Name Validation`, `ID & DoB Validation`,
`Missing Mobile Numbers`, `CMS Data Integration`, `Final ID
Validation`) cut down to
each stage's flagged rows plus a `validation_notes` column explaining why —
see `_write_review_sheets_xlsx` / `_REVIEW_SHEET_COLUMNS` in
`app/helpers.py`. The final `.xlsx` download is different: by then there's
nothing left to review, so it's one flat sheet with every column, the same
shape as the original raw import — see `_write_flat_xlsx`.

Calling an action endpoint again while a job is already processing gets a
`409 Conflict` instead of starting a second run.

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
