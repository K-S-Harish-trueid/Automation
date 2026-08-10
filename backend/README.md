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

### Expose it over the internet with ngrok

From the project root (not this `backend/` folder), `run.py` wraps the same
uvicorn app and adds an `NGROK` env var:

```
NGROK=true python run.py
```

Requires the `ngrok` CLI installed and already authenticated
(`ngrok config check`). The public HTTPS URL prints to the console at
startup; while the server is running it's also always available at
**http://127.0.0.1:4040** (ngrok's local web interface), so you don't need to
scroll back to find it. See the security note under "Hosting the frontend
and backend separately" below — this makes the no-auth backend reachable
from the public internet, not just localhost.

### Try it with dummy data

`../dummy_data/` has a small 20-row fixture built to hit every validation
rule (bad names, bad IDs, bad DoBs, bad addresses, missing phones) plus the
reference file needed at the `replace` upload gate. See
`../dummy_data/README.md` for which account triggers what. Upload
`dummy_raw.csv` at the start screen and `dummy_replace_reference.xlsx` at the
"Historical Override" stage. `dummy_cms_export.csv` predates the Stage
1/2/3 handoff below and is no longer consumed by an automatic stage — CMS
mobile/card data now comes in through two direct CMS export files at Stage 3
instead (see below).

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
    routes/handoff.py  The Stage 1/2/3 reviewer handoff: dispatch/download
                    the Stage 1 and Stage 2 xlsx files, and ingest Naresh's/
                    Haider's responses. Kept out of stage.py since its
                    upload/merge shape (three files at once for Stage 3,
                    blank-means-no-change merges) differs from the generic
                    upload-stage gate.
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
stops on the others until the frontend (or an API call) resolves them.

| # | Stage id | Type | What happens |
|---|---|---|---|
| 1 | `clean` | auto | Strips line breaks from every field |
| 2 | `replace` | upload | Upload a historical override file; merges on `ACCOUNT_NUMBER`, all fields except `CARD_NUMBER`. Skippable — `POST /jobs/{id}/skip` advances without a file, leaving accounts at their current values |
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
| `POST /jobs` | multipart `file` — create a job from the raw CSV/XLSX, kicks off processing in the background |
| `GET /jobs/{id}` | Full status: stage list + statuses + history |
| `GET /jobs/{id}/progress` | `{status, current_step_index, total_steps, current_step_name, percent}` while processing; `status` is `"idle"`/`"done"`/`"error"` (with a `message`) once settled |
| `GET /jobs/{id}/current` | Detail for whatever stage is currently active (shape depends on stage type) |
| `POST /jobs/{id}/upload` | multipart `file` — resolve an `upload` stage in the background |
| `POST /jobs/{id}/skip` | Skip the current stage without doing its normal action (only stages marked `skippable`: currently just `replace`) |
| `POST /jobs/{id}/submit` | `{ edits: [{row_key, field, value}], force_advance }` — resolve a `manual_edit` stage in the background |
| `GET /jobs/{id}/stage1/haider.xlsx` | Download Haider's Stage 1 file (Mobile flagged rows + blank CMS sheet) while parked at `stage1_dispatch`; repeatable, no side effects |
| `GET /jobs/{id}/stage1/naresh.xlsx` | Download Naresh's Stage 1 file (ID Corrections + DOB Corrections sheets, every currently invalid row of each) while parked at `stage1_dispatch`; repeatable, no side effects |
| `POST /jobs/{id}/stage2/naresh-response` | multipart `file` (2 sheets: ID Corrections, DOB Corrections) — Stage 2's merge step: apply Naresh's returned ID and DOB corrections, advance from `stage1_dispatch` through the `name_validate` gate to `stage2_dispatch` |
| `GET /jobs/{id}/stage2/haider.xlsx` | Download Stage 2's dispatch file for Haider (Name Validation + ID Corrections + DOB Corrections sheets) while parked at `stage2_dispatch`; repeatable, no side effects |
| `POST /jobs/{id}/stage3/haider-response` | multipart `cms_mobile_file` (flat: `ACCOUNT_NUMBER`+`PHONE_NUMBER`) + `cms_card_file` (flat: `ACCOUNT_NUMBER`+`CMS_UPDATE_COLS`, `DATE_OPENED` as M/D/YYYY) + `haider_corrections_file` (3 sheets: Name/ID/DOB) — Stage 3's merge step: apply all three, advance past `stage2_dispatch` into `final_id_check`/`default_id` |
| `POST /jobs/{id}/confirm` | Resolve a `confirm` stage in the background |
| `GET /jobs/{id}/download` | Download the final dataset as `{job_id}_final.xlsx` once the job reaches `done` |
| `GET /jobs/{id}/audit/download` | Download the audit trail as `{job_id}_audit.xlsx` |

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
`mobile_fill`, `final_id_check` (the ones with a per-row rule to try — auto
whole-dataset stages like `clean`/`reset_cms` and the `replace` upload-merge
stage aren't testable this way, there's nothing single-row about them).

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
