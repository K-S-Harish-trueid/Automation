"""Standalone /roadmap page for developers: the whole pipeline, stage by
stage, with what each step does and the rules it applies. Same no-auth,
not-linked-from-the-UI stance as /test, /seed and /delete.

The step list, order, types and hidden flags come live from the registry
(pipeline.STAGES, HIDDEN_STAGE_IDS) and the settings from rules_config, so
those can't drift. The descriptions below are hand-written: when a rule
changes, update the matching entry here alongside rules/NN-*.txt."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .. import pipeline, rules_config
from ..background import BYPASS_MANUAL_EDIT_STAGES
from ..pipeline.registry import HIDDEN_STAGE_IDS

router = APIRouter()

_PAGE_PATH = Path(__file__).resolve().parent.parent / "templates" / "roadmap.html"

# Per step: who acts, what it does, the rules, the code, the rules doc.
_STEPS = {
    "clean": {
        "who": "System",
        "does": "Replaces line breaks inside any field with a space.",
        "rules": [],
        "code": ["stages/clean.py :: stage_clean_linebreaks"],
        "doc": "rules/01-clean.txt",
    },
    "replace": {
        "who": "System",
        "does": "Looks each ACCOUNT_NUMBER up in the Postgres historical table and copies the historical values "
                "over the uploaded ones (names, DOB, DATE_OPENED, address, city, province, phone, ID, card fields and more).",
        "rules": [
            "Match by ACCOUNT_NUMBER.",
            "If the historical table is empty, the job pauses with a warning and the operator chooses to continue.",
        ],
        "code": ["stages/replace.py :: stage_replace_from_sql, REPLACE_MAPPING_COLS"],
        "doc": "rules/02-replace.txt",
    },
    "reset_cms": {
        "who": "System",
        "does": "Clears ACCOUNT_TYPE, CARD_TYPE, CARD_PROGRAM and CARD_STATUS so they are refilled from CMS at Stage 3.",
        "rules": [],
        "code": ["stages/reset_cms.py :: stage_reset_cms_fields"],
        "doc": "rules/03-reset_cms.txt",
    },
    "id_dob_validate": {
        "who": "System",
        "does": "Flags rows whose ID or DOB is invalid. Flagged rows go to Naresh in the Stage 1 file.",
        "rules": [
            "ID_TYPE must be Passport, National Id or Civil Id ('doc' or anything else fails).",
            "Passport: 8-9 letters/digits starting with a letter. National Id: exactly 12 digits. Civil Id: just present.",
            "ID_NUMBER made only of zeros fails for every type.",
            "DOB: present, a real date, after the DOB cutoff, not in the future, customer 18+ today.",
            "An account opening date in the future also marks the DOB invalid.",
        ],
        "code": ["stages/id_dob_validate.py :: mask_id_dob_invalid, compute_dob_validity",
                 "toolbox.py :: compute_id_validity, parse_dob_series"],
        "doc": "rules/05-id_dob_validate.txt",
    },
    "address_fix": {
        "who": "System",
        "does": "Fixes junk addresses, cities and provinces automatically.",
        "rules": [
            "Junk = blank/placeholder, no letters, on the address denylist, a number starting with 3+ zeros, or 7+ digits in a row.",
            "City or province junk -> both set to Baghdad (address kept if it is fine).",
            "Address junk -> replaced from the province's address pool (Baghdad pool for Baghdad/outside the country).",
        ],
        "code": ["stages/address_fix.py :: stage_address_fix, mask_address_invalid, mask_location_junk",
                 "address_pools_db.py (denylist + pools in Postgres)"],
        "doc": "rules/06-address_fix.txt",
    },
    "mobile_fill": {
        "who": "System",
        "does": "Flags rows with no usable phone number. They go to Haider in the Stage 1 file.",
        "rules": ["PHONE_NUMBER blank, all zeros, or XXX_NOT_COLLECTED_XXX."],
        "code": ["stages/mobile_fill.py :: mask_mobile_missing"],
        "doc": "rules/07-mobile_fill.txt",
    },
    "stage1_dispatch": {
        "who": "System -> Naresh, Haider",
        "does": "Creates the Stage 1 files and waits. The job moves on when Naresh's reply is uploaded on the Stage 2 page.",
        "rules": [],
        "files": ["stage1_naresh.xlsx: ID Corrections + DOB Corrections (invalid rows only)",
                  "stage1_haider.xlsx: Missing Mobile Numbers + a blank CMS sheet"],
        "code": ["xlsx_export.py :: _write_stage1_naresh_xlsx, _write_stage1_haider_xlsx"],
    },
    "name_validate": {
        "who": "System",
        "does": "Flags names that are a single character. They go to Haider in the Stage 2 file.",
        "rules": ["First, middle or last name exactly 1 character. Blank names are not flagged."],
        "code": ["stages/name_validate.py :: mask_name_invalid"],
        "doc": "rules/04-name_validate.txt",
    },
    "stage2_dispatch": {
        "who": "System -> Haider",
        "does": "Creates Haider's Stage 2 file and waits. The job moves on when the 3 Stage 3 files are uploaded.",
        "rules": ["Only rows still invalid after Naresh's reply are included."],
        "files": ["stage2_haider.xlsx: Name Validation + ID Corrections + DOB Corrections (with the reason for each DOB)"],
        "code": ["xlsx_export.py :: _write_stage2_haider_xlsx"],
    },
    "final_id_check": {
        "who": "System",
        "does": "Last ID check after Haider's reply. Whatever Haider sent is accepted, even in the wrong format.",
        "rules": ["Flags only: ID_TYPE or ID_NUMBER blank, ID_TYPE 'doc', or ID_NUMBER all zeros."],
        "code": ["stages/final_id_check.py :: final_id_check_invalid_mask", "toolbox.py :: id_missing_mask"],
        "doc": "rules/09-final_id_check.txt",
    },
    "default_id": {
        "who": "System",
        "does": "Sets ID_TYPE and ID_NUMBER to XXX_NOT_COLLECTED_XXX for every row the final ID check flagged. No ID is generated.",
        "rules": ["Count shown on Final Output as 'IDs not collected'; every change is in the audit."],
        "code": ["stages/default_id.py :: stage_default_id_assign"],
        "doc": "rules/10-default_id.txt",
    },
    "done": {
        "who": "Operator",
        "does": "Final file and reports are ready to download.",
        "rules": [],
        "files": ["final.xlsx: every column, one sheet", "audit download: every change, who made it and why",
                  "K2_Generated_Records.xlsx: addresses generated from the pools"],
        "code": ["routes/jobs.py :: download_final, audit_history", "helpers.py :: _quality_summary"],
        "doc": "rules/11-done.txt",
    },
}

# Upload steps that resolve a waiting stage -- not separate registry stages,
# but the point where a reviewer's file enters the job.
_GATES = {
    "stage1_dispatch": {
        "id": "gate_naresh", "title": "Naresh's reply (Stage 2 page)", "who": "Naresh",
        "does": "Naresh's ID Corrections + DOB Corrections workbook is uploaded and merged.",
        "rules": [
            "Match by ACCOUNT_NUMBER; only non-blank values overwrite (blank = keep the current value).",
            "Name, ID and DOB are re-checked; what is still invalid goes into Haider's Stage 2 file.",
        ],
        "code": ["routes/handoff.py :: apply_naresh_response", "stage_merge.py :: apply_naresh_response"],
    },
    "stage2_dispatch": {
        "id": "gate_stage3", "title": "Stage 3 uploads: CMS + Haider", "who": "CMS exports, Haider",
        "does": "Three files are uploaded together and merged in this order.",
        "rules": [
            "1. CMS Mobile Numbers: phone merged; blank or all-zero phone -> XXX_NOT_COLLECTED_XXX.",
            "2. CMS Card Details: card fields and DATE_OPENED merged. A DATE_OPENED in the future is applied "
            "but warned in the summary and labelled 'CMS warning' in the audit.",
            "3. Haider's corrections, only for accounts we sent him; rows never sent are kept as they are:",
            "  - Names: his value is used. A name he left blank, or a row he didn't return -> XXX_NOT_COLLECTED_XXX.",
            "  - IDs: his values are used, no format check. ID_TYPE or ID_NUMBER blank, or the row not returned "
            "-> both XXX_NOT_COLLECTED_XXX.",
            "DOB, only rows in Haider's DOB sheet are checked; rows never sent are kept as they are:",
            "  - Haider gave a value: checked with every DOB rule except the DOB cutoff (a pre-cutoff date is accepted). "
            "Valid -> used; blank or invalid -> XXX_NOT_COLLECTED_XXX.",
            "  - Sent to Haider but not returned -> XXX_NOT_COLLECTED_XXX, whatever the reason it was sent.",
            "4. A missing 00:00:00 time is added to real DOB and DATE_OPENED values.",
        ],
        "code": ["routes/handoff.py :: apply_haider_response, _sent_accounts",
                 "stage_merge.py :: apply_cms_mobile_response, apply_cms_card_response, "
                 "apply_haider_corrections_response, _fill_sent_blanks, _validate_haider_dob_sheet, "
                 "normalise_dob_timestamp"],
        "doc": "rules/05-id_dob_validate.txt (DOB AT STAGE 3)",
    },
}

# Flow diagram per stage: what comes in (and from where), the steps in
# order, what goes out (and to whom). kind: file | db | person.
_FLOW = {
    1: {
        "in": [
            {"name": "Raw K2 file", "detail": ".csv / .xlsx / .parquet", "from": "Operator upload", "kind": "file"},
            {"name": "historical", "detail": "past records by ACCOUNT_NUMBER", "from": "Postgres", "kind": "db"},
            {"name": "address_pool + address_denylist", "detail": "replacement addresses, junk values", "from": "Postgres", "kind": "db"},
        ],
        "steps": ["clean", "replace", "reset_cms", "id_dob_validate", "address_fix", "mobile_fill", "stage1_dispatch"],
        "out": [
            {"name": "stage1_naresh.xlsx", "detail": "ID Corrections + DOB Corrections", "to": "Naresh", "kind": "file"},
            {"name": "stage1_haider.xlsx", "detail": "Missing Mobile Numbers + blank CMS sheet", "to": "Haider", "kind": "file"},
        ],
    },
    2: {
        "in": [
            {"name": "Naresh's reply", "detail": "ID Corrections + DOB Corrections, filled", "from": "Naresh (Stage 2 page)", "kind": "person"},
        ],
        "steps": ["gate_naresh", "name_validate", "stage2_dispatch"],
        "out": [
            {"name": "stage2_haider.xlsx", "detail": "Name Validation + ID Corrections + DOB Corrections (with reasons)", "to": "Haider", "kind": "file"},
        ],
    },
    3: {
        "in": [
            {"name": "CMS Mobile Numbers", "detail": "ACCOUNT_NUMBER + PHONE_NUMBER", "from": "CMS export", "kind": "file"},
            {"name": "CMS Card Details", "detail": "card fields + DATE_OPENED", "from": "CMS export", "kind": "file"},
            {"name": "Haider's corrected file", "detail": "Name Validation + ID Corrections + DOB Corrections", "from": "Haider (Stage 3 page)", "kind": "person"},
        ],
        "steps": ["gate_stage3", "final_id_check", "default_id", "done"],
        "out": [
            {"name": "final.xlsx", "detail": "every column, one sheet", "to": "Operator download", "kind": "file"},
            {"name": "Audit download", "detail": "every change, who and why", "to": "Operator download", "kind": "file"},
            {"name": "K2_Generated_Records.xlsx", "detail": "addresses generated from the pools", "to": "Operator download", "kind": "file"},
            {"name": "audit_log + generated_records", "detail": "copy of the audit and generated values", "to": "Postgres", "kind": "db"},
        ],
    },
}

_STAGE_NAMES = {1: "Stage 1: System checks and first dispatch",
                2: "Stage 2: Naresh's reply and Haider's file",
                3: "Stage 3: CMS + Haider merge and final output"}


def _mode(stage: dict) -> str:
    if stage["type"] == "manual_edit":
        return "review_off" if BYPASS_MANUAL_EDIT_STAGES else "review"
    return {"auto": "auto", "stage1": "wait", "stage2": "wait", "confirm": "confirm", "done": "final"}.get(stage["type"], stage["type"])


@router.get("/roadmap", include_in_schema=False)
def roadmap_page():
    return FileResponse(_PAGE_PATH)


@router.get("/api/dev/roadmap", include_in_schema=False)
def roadmap_data():
    stages = {}
    for s in pipeline.STAGES:
        info = _STEPS.get(s["id"], {})
        stages.setdefault(s["stage"], []).append({
            "id": s["id"], "title": s["title"], "mode": _mode(s), "hidden": s["id"] in HIDDEN_STAGE_IDS,
            "who": info.get("who", ""), "does": info.get("does", "No description yet."),
            "rules": info.get("rules", []), "files": info.get("files", []),
            "code": info.get("code", []), "doc": info.get("doc", ""),
        })
        if s["id"] in _GATES:
            gate = _GATES[s["id"]]
            stages.setdefault(s["stage"] + 1, []).append({**gate, "mode": "upload", "hidden": False,
                                                          "files": [], "doc": gate.get("doc", "")})
    titles = {step["id"]: step["title"] for steps in stages.values() for step in steps}
    flow = {n: {**f, "steps": [{"id": i, "title": titles.get(i, i)} for i in f["steps"]]} for n, f in _FLOW.items()}
    return {
        "stages": [{"stage": n, "name": _STAGE_NAMES.get(n, f"Stage {n}"), "steps": stages[n],
                    "flow": flow.get(n)} for n in sorted(stages)],
        "settings": {
            "DOB cutoff": f"after {rules_config.DOB_CUTOFF.date()}",
            "Age of majority": rules_config.AGE_OF_MAJORITY,
            "Not-collected placeholder": rules_config.NOT_COLLECTED_PLACEHOLDER,
            "Manual review screens": "off (skipped automatically)" if BYPASS_MANUAL_EDIT_STAGES else "on",
        },
    }
