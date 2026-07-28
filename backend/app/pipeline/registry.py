from .auto_stages import (
    default_id_invalid_mask,
    stage_address_fix,
    stage_clean_linebreaks,
    stage_default_id_assign,
    stage_reset_cms_fields,
)
from .constants import CMS_UPDATE_COLS, REPLACE_MAPPING_COLS
from .reasons import (
    validation_reasons_id_dob,
    validation_reasons_id_only,
    validation_reasons_mobile,
    validation_reasons_name,
)
from .upload_stages import stage_cms_integration, stage_replace_reference
from .validators import mask_id_dob_invalid, mask_id_only_invalid, mask_mobile_missing, mask_name_invalid

STAGES = [
    {"id": "clean", "title": "Initial Data Cleaning", "type": "auto"},
    {"id": "replace", "title": "Data Consistency Update", "type": "upload", "skippable": True,
     "upload_label": "Historical override file (matches on ACCOUNT_NUMBER, e.g. K2_DATA_PAH_....xlsx)",
     "upload_guidance": {
         "expected_file": "Historical replacement export",
         "required_columns": ["ACCOUNT_NUMBER", *REPLACE_MAPPING_COLS],
         "matching_key": "ACCOUNT_NUMBER",
         "overwrite_fields": REPLACE_MAPPING_COLS,
         "duplicate_handling": "The last row for each duplicated ACCOUNT_NUMBER is used.",
         "unresolved_label": "Accounts not found in this file keep their current values.",
     }},
    {"id": "reset_cms", "title": "Reset CMS Fields", "type": "auto"},
    {"id": "name_validate", "title": "Name Validation", "type": "manual_edit"},
    {"id": "id_dob_validate", "title": "ID & DoB Validation", "type": "manual_edit"},
    {"id": "address_fix", "title": "Address & Contact Auto-Fix", "type": "auto"},
    {"id": "mobile_fill", "title": "Missing Mobile Numbers", "type": "manual_edit"},
    {"id": "cms_integration", "title": "CMS Data Integration", "type": "upload",
     "skippable": True, "api_invoke_planned": True,
     "upload_label": "CMS export with ACCOUNT_NUMBER, CARD_NUMBER, ACCOUNT_TYPE, CARD_TYPE, CARD_PROGRAM, CARD_STATUS",
     "upload_guidance": {
         "expected_file": "CMS export",
         "required_columns": ["ACCOUNT_NUMBER", *CMS_UPDATE_COLS],
         "matching_key": "ACCOUNT_NUMBER",
         "overwrite_fields": CMS_UPDATE_COLS,
         "duplicate_handling": "The first row for each duplicated ACCOUNT_NUMBER is used.",
         "unresolved_label": "Accounts not found in this file remain unresolved for CMS fields.",
     }},
    {"id": "send_email", "title": "Send Email", "type": "email", "skippable": True},
    {"id": "final_id_check", "title": "Final ID Validation", "type": "manual_edit"},
    {"id": "default_id", "title": "Default ID Assignment", "type": "confirm"},
    {"id": "done", "title": "Final Output", "type": "done"},
]

AUTO_HANDLERS = {
    "clean": stage_clean_linebreaks,
    "reset_cms": stage_reset_cms_fields,
    "address_fix": stage_address_fix,
}

UPLOAD_HANDLERS = {
    "replace": stage_replace_reference,
    "cms_integration": stage_cms_integration,
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
