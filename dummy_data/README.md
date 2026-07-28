# Dummy data

Small hand-built fixtures for exercising every stage of the pipeline without
the real ~35k-row export. 20 accounts (`9000001`–`9000020`), each engineered
to hit a specific rule:

| Account | Demonstrates |
|---|---|
| 9000001 | Fully valid (Passport); also gets matched by `dummy_cms_export.csv` |
| 9000002 | Fully valid (National Id); gets overwritten by `dummy_replace_reference.xlsx` |
| 9000003 | 1-character first name → `name_validate` |
| 9000004 | 1-character middle name → `name_validate` |
| 9000005 | 1-character last name → `name_validate` |
| 9000006 | `ID_TYPE = doc` → both ID fields unavailable → `id_dob_validate` |
| 9000007 | Passport with malformed `ID_NUMBER` → `id_dob_validate` |
| 9000008 | Blank DoB → `id_dob_validate` |
| 9000009 | DoB under 18 → `id_dob_validate` |
| 9000010 | Default bad date (1900) → `id_dob_validate` |
| 9000011 | Invalid address, Baghdad province → auto-repaired by `address_fix` (province map) |
| 9000012 | City `00000` + invalid address → auto-repaired (case A) |
| 9000013 | Province "Outside the country" + invalid address → auto-repaired to Baghdad |
| 9000014 | City `00000` + valid address → auto-repaired (case C, address kept) |
| 9000015 | Blank phone → `NOT_COLLECTED` → `mobile_fill` |
| 9000016 | All-zero phone → `NOT_COLLECTED` → `mobile_fill` |
| 9000017 | Overwritten by `dummy_replace_reference.xlsx` (name/address/ID all replaced) |
| 9000018 | Matched by `dummy_cms_export.csv` |
| 9000019 | Fully valid (Civil Id); also matched by `dummy_cms_export.csv` |
| 9000020 | Malformed ID **and** missing phone at once — shows a row flagged by two different stages |

## Files

- **dummy_raw.csv** — upload this first, at the initial "Start a new batch" screen.
- **dummy_replace_reference.xlsx** — upload at the `replace` (Data Consistency Update) stage. Overwrites 9000002 and 9000017.
- **dummy_cms_export.csv** — upload at the `cms_integration` stage. Fills in CARD_NUMBER/ACCOUNT_TYPE/CARD_TYPE/CARD_PROGRAM/CARD_STATUS for 9000001, 9000018, 9000019.

Regenerate with `gen_dummy_data.py` if the pipeline rules change (script not
checked in — ask to have it regenerated).
