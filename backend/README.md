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
    main.py       FastAPI routes: create job, advance stages, upload/submit/confirm, download
    pipeline.py   Stage logic ported from clean.py / replace.py / cmsdata.py / idvalid.py /
                  dobvalid.py / pah3.py — pure functions over an in-memory DataFrame
    store.py      Per-job state: in-memory dict + a disk snapshot (parquet + status.json)
                  under jobs/<job_id>/, so a job survives a server restart
  jobs/           Runtime data, one folder per job (gitignored, safe to delete when idle)
  requirements.txt
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
| 6 | `mobile_fill` | manual_edit | Flags accounts with no phone number (blank, `XXX_NOT_COLLECTED_XXX`, or all-zero) for inline entry |
| 7 | `cms_integration` | upload | Upload a CMS export; merges `CARD_NUMBER`/`ACCOUNT_TYPE`/`CARD_TYPE`/`CARD_PROGRAM`/`CARD_STATUS`. Skippable; also shows a disabled "Invoke via API (coming soon)" placeholder |
| 8 | `send_email` | email | Stub checkpoint: download the in-progress dataset to share manually; "Continue" just advances (no real send yet). Skippable |
| 9 | `final_id_check` | manual_edit | Last pass on any still-invalid IDs |
| 10 | `default_id` | confirm | Assigns a random 8-digit ID (`00######`) + `Civil Id` type to whatever is still invalid |
| 11 | `done` | done | Final dataset ready to download |

`manual_edit` stages page 200 flagged rows at a time; submitting a batch
re-validates and either loads the next batch or advances. Every manual stage
also accepts `force_advance: true` on `/submit` to skip remaining rows without
fixing them.

**Manual-edit stages are currently bypassed entirely**
(`BYPASS_MANUAL_EDIT_STAGES = True` in `app/background.py`): the background
runner auto-advances `name_validate`/`id_dob_validate`/`mobile_fill`/
`final_id_check` the same way it chains `auto` stages, logging a history
entry with how many rows were left flagged. The frontend never sees these
stages while the flag is on. Flip it to `False` to restore the normal
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
| `GET /jobs/{id}/download` | Download the final dataset as `{job_id}.xlsx` once the job reaches `done` |

Both the `send_email` and final `.xlsx` downloads are one workbook split
into a separate named sheet per topic (`Name Validation`,
`ID & DoB Validation`, `Address & Contact`, `Missing Mobile
Numbers`, `CMS Data Integration`, `Final ID Validation`), each keyed by
`ACCOUNT_NUMBER`, rather than one flat sheet with every column — see
`_write_stage_sheets_xlsx` / `_EXPORT_SHEET_COLUMNS` in `app/helpers.py`.

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
