"""Sandbox for trying a single stage's rule against typed-in test values,
without creating a real job. No auth (matches the rest of this app -- see
the security note in backend/README.md); nothing here reads or writes job
data, it only runs the real pipeline functions against a throwaway 1-row
DataFrame.

Deliberately not linked from the main app UI and not served out of
frontend/ (so it isn't reachable as a plain static file either) -- not
everyone using the tool needs this, it's reachable only by knowing the
/test URL directly. include_in_schema=False keeps it off the auto-generated
API docs too."""
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import pipeline
from ..schemas import StageTestRequest

router = APIRouter()

_PAGE_PATH = Path(__file__).resolve().parent.parent / "templates" / "stage_test.html"


@router.get("/test", include_in_schema=False)
def stage_test_page():
    return FileResponse(_PAGE_PATH)


# ACCOUNT_ADDRESS/CITY/PROVINCE are the fields address_fix actually reads --
# it isn't in MANUAL_STAGES (it's an auto stage now), so its field list
# isn't derived automatically the way the manual stages' are below.
_ADDRESS_FIX_FIELDS = ["ACCOUNT_ADDRESS", "ADDRESS_CITY", "ADDRESS_PROVINCE"]

# Condensed from each stage's rules/NN-*.txt CONSTRAINTS section -- precise,
# but short enough for a side panel. If a stage's actual check changes,
# update this alongside rules/NN-*.txt in the same edit (same discipline as
# keeping registry.py's `instructions` in sync).
_RULES_PANELS = {
    "name_validate": {
        "intro": "Flagged if ANY of these is true (after trimming whitespace):",
        "points": [
            "ACCOUNT_FIRST_NAME is exactly 1 character",
            "ACCOUNT_MIDDLE_NAME is exactly 1 character",
            "ACCOUNT_LAST_NAME is exactly 1 character",
        ],
        "note": "Blank names are NOT flagged -- only exactly 1 character trips it.",
    },
    "id_dob_validate": {
        "intro": "Passes only if ALL three hold:",
        "points": [
            "ID_TYPE is Passport / National Id / Civil Id -- \"doc\" and anything else fails",
            "ID_NUMBER matches that type's format -- Passport: 8-9 chars starting with a letter, "
            "National Id: exactly 12 digits, Civil Id: just needs to be present",
            "ACCOUNT_HOLDER_DOB is a real date, after year 1905, and the customer was 18+ "
            "when DATE_OPENED happened (falls back to today's age if DATE_OPENED can't be read)",
        ],
        "note": None,
    },
    "address_fix": {
        "intro": "Invalid if ANY of these is true:",
        "points": [
            "Missing or a placeholder value",
            "No letters at all in any script -- e.g. a phone number, coordinates, \"-\"",
            "Exact match on a known-junk list (e.g. \"JUNE\", \"A\")",
        ],
        "note": "If invalid: \"outside the country\" or city 00000 → Baghdad pool + city/province "
                "reset to Baghdad. Real city with a mapped province → that province's own pool, "
                "city/province untouched. Unmapped province → left invalid, nothing changes.",
    },
    "mobile_fill": {
        "intro": "Flagged if ANY of these is true:",
        "points": [
            "PHONE_NUMBER equals XXX_NOT_COLLECTED_XXX",
            "PHONE_NUMBER is blank",
            "PHONE_NUMBER is all zeros",
        ],
        "note": None,
    },
    "final_id_check": {
        "intro": "Same ID_TYPE/ID_NUMBER rule as Missing ID & DoB (DOB is NOT re-checked here):",
        "points": [
            "ID_TYPE must be Passport / National Id / Civil Id",
            "ID_NUMBER must match that type's format (same rules as Missing ID & DoB)",
        ],
        "note": None,
    },
}


def _testable_stages() -> dict:
    titles = {s["id"]: s["title"] for s in pipeline.STAGES}
    stages = {
        stage_id: {
            "title": titles[stage_id],
            "fields": cfg["editable_cols"],
            "instructions": cfg["instructions"],
            "rules": _RULES_PANELS[stage_id],
        }
        for stage_id, cfg in pipeline.MANUAL_STAGES.items()
    }
    # DATE_OPENED isn't reviewer-editable (it's account metadata, not
    # something a reviewer corrects, so it's not in editable_cols), but the
    # DOB check now needs it to test the age-at-opening rule -- added here
    # for the sandbox only.
    stages["id_dob_validate"]["fields"] = [*stages["id_dob_validate"]["fields"], "DATE_OPENED"]
    stages["address_fix"] = {
        "title": titles["address_fix"],
        "fields": _ADDRESS_FIX_FIELDS,
        "instructions": "Accounts with a missing address, an address with no letters at all "
                        "(e.g. a phone number or placeholder), or an exact match against a known-junk "
                        "list (e.g. \"JUNE\", a single letter) get a synthetic address auto-filled from "
                        "a Baghdad/province pool -- no manual review, this runs automatically.",
        "rules": _RULES_PANELS["address_fix"],
    }
    return stages


@router.get("/api/stage-test/stages", include_in_schema=False)
def list_testable_stages():
    """Which stages can be tested here and what fields each one needs --
    drives the dropdown + input boxes on the frontend, so it can't drift
    out of sync with the real stage list."""
    return _testable_stages()


@router.post("/api/stage-test/{stage_id}", include_in_schema=False)
def run_stage_test(stage_id: str, body: StageTestRequest):
    testable = _testable_stages()
    if stage_id not in testable:
        raise HTTPException(404, f"'{stage_id}' isn't a testable stage")

    df = pd.DataFrame([body.fields])

    if stage_id == "address_fix":
        before = df.copy()
        flagged = bool(pipeline.mask_address_invalid(df).iloc[0])
        after, _summary = pipeline.stage_address_fix(df.copy())
        changes = {}
        for col in _ADDRESS_FIX_FIELDS:
            before_val = before.at[0, col] if col in before.columns else ""
            after_val = after.at[0, col] if col in after.columns else ""
            if str(before_val) != str(after_val):
                changes[col] = {"before": before_val, "after": after_val}
        return {"flagged": flagged, "reasons": [], "auto_fix_preview": changes}

    cfg = pipeline.MANUAL_STAGES[stage_id]
    flagged = bool(cfg["validator"](df).iloc[0])
    reasons = cfg["reasons"](df).get(0, []) if flagged else []
    return {"flagged": flagged, "reasons": reasons, "auto_fix_preview": None}
