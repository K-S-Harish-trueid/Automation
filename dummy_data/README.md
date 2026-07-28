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
| 9000011 | Address is `0` (placeholder), city is real (`Some City`) → `address_fix` replaces ACCOUNT_ADDRESS from the Baghdad pool (its province, `Baghdad`, is mapped), city/province untouched |
| 9000012 | Address is `JUNE` (denylist match — has letters, so the "no letters" rule alone wouldn't catch it), city is the `00000` placeholder → `address_fix` sets city/province to Baghdad and replaces ACCOUNT_ADDRESS from the pool |
| 9000013 | Address is `-` (no letters), province is `Outside the country` → `address_fix` sets city/province to Baghdad and replaces ACCOUNT_ADDRESS from the general pool (outside-country rows can't be province-mapped) |
| 9000014 | Address is a real-looking street value, city is the `00000` placeholder → `address_fix` sets city/province to Baghdad but leaves the (already-valid) address alone |
| 9000015 | Blank phone → `mobile_fill` |
| 9000016 | All-zero phone → `mobile_fill` |
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
