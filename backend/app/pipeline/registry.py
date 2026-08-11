"""The wiring list: which file handles each of the 12 pipeline stages, in
order, plus the small cross-stage aggregates (RAW_REQUIRED_COLS, upload
dispatch) that need to know about more than one stage at once. Edit a single
stage's own behavior in its file under stages/ -- this file should only
change when a stage is added, removed, or reordered."""
from .stages.address_fix import stage_address_fix
from .stages.clean import stage_clean_linebreaks
from .stages.cms_integration import CMS_UPDATE_COLS
from .stages.default_id import default_id_invalid_mask, stage_default_id_assign
from .stages.final_id_check import mask_id_only_invalid, validation_reasons_id_only
from .stages.id_dob_validate import mask_id_dob_invalid, validation_reasons_id_dob
from .stages.mobile_fill import mask_mobile_missing, validation_reasons_mobile
from .stages.name_validate import mask_name_invalid, validation_reasons_name
from .stages.replace import REPLACE_MAPPING_COLS, stage_replace_reference, validate_replace_reference_inputs
from .stages.reset_cms import stage_reset_cms_fields

RAW_REQUIRED_COLS = list(dict.fromkeys(["ACCOUNT_NUMBER", *REPLACE_MAPPING_COLS, *CMS_UPDATE_COLS]))

# Stage ids to hide from the sidebar/progress meter (helpers.py's
# _public_job_status flags each stage entry with "hidden": True/False for
# the frontend to filter on) while still running them exactly as normal --
# this is purely a display toggle, not a pipeline change. Add/remove ids
# here to show or hide a stage; empty set shows everything.
# address_fix is hidden pending a decision on relocating/reworking it --
# see TODO.
HIDDEN_STAGE_IDS: set[str] = {"address_fix"}


def validate_upload_inputs(stage_id: str, df, ref_df):
    if stage_id == "replace":
        validate_replace_reference_inputs(df, ref_df)


STAGES = [
    {"id": "clean", "title": "Initial Data Cleaning", "type": "auto", "stage": 1},
    {"id": "replace", "title": "Historical Override", "type": "upload", "skippable": True, "stage": 1,
     "upload_label": "Historical override file (matches on ACCOUNT_NUMBER, e.g. K2_DATA_PAH_....xlsx)",
     "upload_guidance": {
         "expected_file": "Historical replacement export",
         "required_columns": ["ACCOUNT_NUMBER", *REPLACE_MAPPING_COLS],
         "matching_key": "ACCOUNT_NUMBER",
         "overwrite_fields": REPLACE_MAPPING_COLS,
         "duplicate_handling": "The last row for each duplicated ACCOUNT_NUMBER is used.",
         "unresolved_label": "Accounts not found in this file keep their current values.",
     }},
    {"id": "reset_cms", "title": "Inputs required from CMS", "type": "auto", "stage": 1},
    {"id": "id_dob_validate", "title": "Missing ID & DoB", "type": "manual_edit", "stage": 1},
    {"id": "address_fix", "title": "Address Auto-Fix", "type": "auto", "stage": 1},
    {"id": "mobile_fill", "title": "Missing Mobile Numbers", "type": "manual_edit", "stage": 1},
    {"id": "stage1_dispatch", "title": "Stage 1 Dispatch", "type": "stage1", "stage": 1},
    # Name issues are never in Haider's Stage 1 file -- they're deferred and
    # only ever handed to him bundled into the Stage 2 dispatch file, but
    # this is still its own blocking review gate between the two dispatches
    # (not just a data classification used when building that file).
    {"id": "name_validate", "title": "Name Validation", "type": "manual_edit", "stage": 2},
    {"id": "stage2_dispatch", "title": "Stage 2 Dispatch", "type": "stage2", "stage": 2},
    {"id": "final_id_check", "title": "Final ID Validation", "type": "manual_edit", "stage": 3},
    {"id": "default_id", "title": "Default ID Assignment", "type": "confirm", "stage": 3},
    {"id": "done", "title": "Final Output", "type": "done", "stage": 3},
]

AUTO_HANDLERS = {
    "clean": stage_clean_linebreaks,
    "reset_cms": stage_reset_cms_fields,
    "address_fix": stage_address_fix,
}

UPLOAD_HANDLERS = {
    "replace": stage_replace_reference,
}

CONFIRM_HANDLERS = {
    "default_id": stage_default_id_assign,
}

CONFIRM_PREVIEW = {
    "default_id": lambda df: int(default_id_invalid_mask(df).sum()),
}

MANUAL_STAGES = {
    "name_validate": {
        "validator": mask_name_invalid,
        "reasons": validation_reasons_name,
        "editable_cols": ["ACCOUNT_FIRST_NAME", "ACCOUNT_MIDDLE_NAME", "ACCOUNT_LAST_NAME"],
        "context_cols": ["ACCOUNT_NUMBER"],
        "instructions": "These accounts have a first, middle, or last name with only 1 character. Correct the name(s) below.",
    },
    "id_dob_validate": {
        "validator": mask_id_dob_invalid,
        "reasons": validation_reasons_id_dob,
        "editable_cols": ["ID_TYPE", "ID_NUMBER", "ACCOUNT_HOLDER_DOB"],
        "context_cols": ["ACCOUNT_NUMBER", "ACCOUNT_FIRST_NAME", "ACCOUNT_LAST_NAME"],
        "instructions": "ID_TYPE must be Passport / National Id / Civil Id, ID_NUMBER must match the expected format, "
                        "and DoB must be a valid date for an adult (18+).",
    },
    "mobile_fill": {
        "validator": mask_mobile_missing,
        "reasons": validation_reasons_mobile,
        "editable_cols": ["PHONE_NUMBER"],
        "context_cols": ["ACCOUNT_NUMBER", "ACCOUNT_FIRST_NAME", "ACCOUNT_LAST_NAME"],
        "instructions": "These accounts are missing a mobile number. Fill in PHONE_NUMBER for each.",
    },
    "final_id_check": {
        "validator": mask_id_only_invalid,
        "reasons": validation_reasons_id_only,
        "editable_cols": ["ID_TYPE", "ID_NUMBER"],
        "context_cols": ["ACCOUNT_NUMBER", "ACCOUNT_FIRST_NAME", "ACCOUNT_LAST_NAME"],
        "instructions": "Final pass on ID_TYPE/ID_NUMBER before any remaining invalid records get an auto-generated default ID.",
    },
}
