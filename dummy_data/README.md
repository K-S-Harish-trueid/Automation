# Dummy data

Small hand-built fixtures for exercising every stage of the pipeline without
the real ~35k-row export. 20 accounts (`9000001`–`9000020`), each engineered
to hit a specific rule:

| Account | Demonstrates |
|---|---|
| 9000001 | Fully valid (Passport); also gets matched by `dummy_cms_export.csv` (see note below) |
| 9000002 | Fully valid (National Id); gets overwritten by `dummy_replace_reference.xlsx` |
| 9000003 | 1-character first name → `name_validate` |
| 9000004 | 1-character middle name → `name_validate` |
| 9000005 | 1-character last name → `name_validate` |
| 9000006 | `ID_TYPE = doc` → both ID fields unavailable → `id_dob_validate` |
| 9000007 | Passport with malformed `ID_NUMBER` → `id_dob_validate` |
| 9000008 | Blank DoB → `id_dob_validate` |
| 9000009 | DoB under 18 → `id_dob_validate` |
| 9000010 | Default bad date (1900) → `id_dob_validate` |
| 9000011 | Address is `0` (placeholder), city is real (`Some City`) → `address_fix` replaces ACCOUNT_ADDRESS from the Baghdad pool (its province, `Baghdad`, is mapped), city/province untouched |
| 9000012 | Address is `JUNE` (denylist match — has letters, so the "no letters" rule alone wouldn't catch it), city is the `00000` placeholder → `address_fix` sets city/province to Baghdad and replaces ACCOUNT_ADDRESS from the pool |
| 9000013 | Address is `-` (no letters), province is `Outside the country` → `address_fix` sets city/province to Baghdad and replaces ACCOUNT_ADDRESS from the general pool (outside-country rows can't be province-mapped) |
| 9000014 | Address is a real-looking street value, city is the `00000` placeholder → `address_fix` sets city/province to Baghdad but leaves the (already-valid) address alone |
| 9000015 | Blank phone → `mobile_fill` |
| 9000016 | All-zero phone → `mobile_fill` |
| 9000017 | Overwritten by `dummy_replace_reference.xlsx` (name/address/ID all replaced) |
| 9000018 | Matched by `dummy_cms_export.csv` (see note below) |
| 9000019 | Fully valid (Civil Id); also matched by `dummy_cms_export.csv` (see note below) |
| 9000020 | Malformed ID **and** missing phone at once — shows a row flagged by two different stages |

## Files

- **dummy_raw.csv** — upload this first, at the initial "Start a new batch" screen.
- **dummy_replace_reference.xlsx** — upload at the `replace` (Historical Override) stage. Overwrites 9000002 and 9000017.
- **dummy_cms_export.csv** — predates the Stage 1/2/3 reviewer handoff (see
  `backend/README.md`) and is no longer consumed by an automatic stage; CMS
  data now comes in through two direct export files at Stage 3 instead (see
  `stage3/cms_mobile_info.csv` / `stage3/cms_card_info.csv` below). Kept here
  as reference for what CARD_NUMBER/ACCOUNT_TYPE/CARD_TYPE/CARD_PROGRAM/
  CARD_STATUS values 9000001, 9000018, and 9000019 were designed to match.

Regenerate with `gen_dummy_data.py` if the pipeline rules change (script not
checked in — ask to have it regenerated).

## Testing Stage 1/2/3 (the reviewer handoff)

Folders holding exactly what to upload at each step. These were generated
against this exact `dummy_raw.csv` and verified end-to-end (each file was fed
through the real pipeline once to confirm the final result), so following
the sequence below reproduces a known-good run. Each stage is its own
self-contained mini-pipeline (input → merge → verify → dispatch), not one
continuous line — Stage 1 stops for good once it dispatches; Stage 2 and
Stage 3 are separate pages, each with its own job picker, and once their
upload is applied the job is handed straight to the normal wizard (Stage 2
lands back on its own dispatch screen; Stage 3 lands on the existing
confirm → final output screens).

DOB travels with IDs, not with Name: Naresh gets first crack at both ID and
DOB fixes (one workbook, 2 sheets), and whatever he can't resolve of either
gets dispatched to Haider as a second-pass file, bundled alongside Name (the
same 2-sheet ID/DOB shape, plus a Name Validation sheet). CMS mobile numbers
and card details no longer come from a reviewer typing them in — they arrive
as two direct exports from the CMS system itself at Stage 3.

- **`init/`** — `dummy_raw.csv` + `dummy_replace_reference.xlsx`, for
  starting a brand-new job (Stage 1). **Skip the `replace` stage** (don't
  upload `dummy_replace_reference.xlsx`) if you want Stage 1 Dispatch to
  match the files below exactly — the `stage2`/`stage3` responses were
  generated against a run where 9000002/9000017 were never overwritten by
  it. (Uploading it instead is a fine way to test `replace` on its own, just
  don't expect the stage files to line up with the fixtures below
  afterward.)
- **`stage2/naresh_response.xlsx`** — upload on the Stage 2 page once the job
  reaches Stage 1 Dispatch. 2 sheets: **ID Corrections** fixes 9000006 and
  9000007, leaves 9000017/9000020 blank; **DOB Corrections** fixes 9000008
  and 9000010, leaves 9000009 blank (Naresh can't resolve those, matching
  "Naresh does what he can, Haider gets the rest").
- **`stage3/cms_mobile_info.csv`** (flat: ACCOUNT_NUMBER + PHONE_NUMBER) +
  **`stage3/cms_card_info.csv`** (flat: ACCOUNT_NUMBER + CMS_UPDATE_COLS,
  DATE_OPENED as M/D/YYYY) + **`stage3/haider_corrections_response.xlsx`**
  (Name Validation + ID Corrections + DOB Corrections, no CMS/Mobile) —
  upload all three together on the Stage 3 page once the job reaches
  Stage 2 Dispatch. The two CMS exports fix Mobile (9000015, 9000016) and
  CMS card details (9000001, 9000018, 9000019); Haider's file fixes Name
  (9000003/4/5), the ID on 9000017, and the DOB on 9000009 (Naresh's
  leftover); deliberately leaves 9000017's name and 9000020's DOB/ID blank
  to demonstrate that a blank cell means "no change", never "clear this
  field".

Expected end state: every account resolves except **9000020**, which reaches
the `default_id` confirm gate (surfaced directly inside Stage 3, not a
separate trip to the dashboard) and gets an auto-generated Civil ID — the
one row nobody could fix across all three stages.
