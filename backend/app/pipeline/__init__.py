"""
Pipeline stage logic, ported from the standalone scripts in the project root
(clean.py, replace.py, cmsdata.py, idvalid.py, dobvalid.py, pah3.py, mobileupd.py,
correctid.py, dobupd.py) so they can run against an in-memory DataFrame instead
of fixed file paths, and expose flagged rows for inline correction in the UI.

Organized one file per pipeline stage under stages/ (name matches the stage
id and the matching rules/NN-<id>.txt doc), so a change scoped to one stage
only touches that stage's file. toolbox.py holds the handful of helpers 2+
stages genuinely share (generic Series helpers, the ID_TYPE/ID_NUMBER check
reused by id_dob_validate/final_id_check/default_id). registry.py is the
short list wiring stage id -> handler/validator/config, in pipeline order.

This file re-exports the public surface so callers keep doing
`from . import pipeline` and `pipeline.STAGES`, `pipeline.mask_name_invalid(...)`,
etc. unchanged, regardless of which stages/ file something actually lives in.
"""
from .toolbox import (
    NOT_COLLECTED,
    PLACEHOLDERS,
    _reasons_by_row,
    _s,
    compute_id_validity,
    id_reason_checks,
    parse_dob_series,
    series_available,
    series_has_letter,
)
from .stages.clean import stage_clean_linebreaks
from .stages.replace import (
    REPLACE_MAPPING_COLS,
    stage_replace_reference,
    validate_replace_reference_inputs,
)
from .stages.reset_cms import stage_reset_cms_fields
from .stages.name_validate import mask_name_invalid, validation_reasons_name
from .stages.id_dob_validate import compute_dob_validity, mask_id_dob_invalid, validation_reasons_id_dob
from .stages.address_fix import mask_address_invalid, stage_address_fix
from .stages.mobile_fill import mask_mobile_missing, validation_reasons_mobile
from .stages.cms_integration import (
    CMS_SHEET_COLS,
    CMS_UPDATE_COLS,
    stage_cms_integration,
    validate_cms_reference_inputs,
)
from .stages.final_id_check import mask_id_only_invalid, validation_reasons_id_only
from .stages.default_id import default_id_invalid_mask, stage_default_id_assign
from .registry import (
    AUTO_HANDLERS,
    CONFIRM_HANDLERS,
    CONFIRM_PREVIEW,
    MANUAL_STAGES,
    RAW_REQUIRED_COLS,
    STAGES,
    UPLOAD_HANDLERS,
    validate_upload_inputs,
)

__all__ = [
    "NOT_COLLECTED", "PLACEHOLDERS",
    "REPLACE_MAPPING_COLS", "CMS_UPDATE_COLS", "CMS_SHEET_COLS", "RAW_REQUIRED_COLS",
    "_s", "series_available", "series_has_letter", "parse_dob_series",
    "stage_clean_linebreaks", "stage_reset_cms_fields", "stage_address_fix",
    "default_id_invalid_mask", "stage_default_id_assign",
    "validate_replace_reference_inputs", "validate_cms_reference_inputs", "validate_upload_inputs",
    "stage_replace_reference", "stage_cms_integration",
    "mask_name_invalid", "compute_id_validity", "compute_dob_validity",
    "mask_id_dob_invalid", "mask_id_only_invalid", "mask_mobile_missing", "mask_address_invalid",
    "_reasons_by_row", "validation_reasons_name", "id_reason_checks",
    "validation_reasons_id_dob", "validation_reasons_mobile", "validation_reasons_id_only",
    "STAGES", "AUTO_HANDLERS", "UPLOAD_HANDLERS", "CONFIRM_HANDLERS", "CONFIRM_PREVIEW", "MANUAL_STAGES",
]
