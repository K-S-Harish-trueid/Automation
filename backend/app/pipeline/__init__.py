"""
Pipeline stage logic, ported from the standalone scripts in the project root
(clean.py, replace.py, cmsdata.py, idvalid.py, dobvalid.py, pah3.py, mobileupd.py,
correctid.py, dobupd.py) so they can run against an in-memory DataFrame instead
of fixed file paths, and expose flagged rows for inline correction in the UI.

Split into submodules by concern (constants, utils, auto_stages,
upload_stages, validators, reasons, registry); this file re-exports the
public surface so callers keep doing `from . import pipeline` and
`pipeline.STAGES`, `pipeline.mask_name_invalid(...)`, etc. unchanged.
"""
from .constants import (
    BAGHDAD_ADDRESS_POOL,
    CMS_SHEET_COLS,
    CMS_UPDATE_COLS,
    INVALID_ADDRESSES,
    NOT_COLLECTED,
    PLACEHOLDERS,
    PROVINCE_ADDRESS_MAP,
    RAW_REQUIRED_COLS,
    REPLACE_MAPPING_COLS,
)
from .utils import _s, balanced_assign, parse_dob_series, series_available
from .auto_stages import (
    default_id_invalid_mask,
    stage_address_fix,
    stage_clean_linebreaks,
    stage_default_id_assign,
    stage_reset_cms_fields,
)
from .upload_stages import (
    stage_cms_integration,
    stage_replace_reference,
    validate_cms_reference_inputs,
    validate_replace_reference_inputs,
    validate_upload_inputs,
)
from .validators import (
    compute_dob_validity,
    compute_id_validity,
    mask_id_dob_invalid,
    mask_id_only_invalid,
    mask_mobile_missing,
    mask_name_invalid,
)
from .reasons import (
    _id_reason_checks,
    _reasons_by_row,
    validation_reasons_id_dob,
    validation_reasons_id_only,
    validation_reasons_mobile,
    validation_reasons_name,
)
from .registry import (
    AUTO_HANDLERS,
    CONFIRM_HANDLERS,
    CONFIRM_PREVIEW,
    MANUAL_STAGES,
    STAGES,
    UPLOAD_HANDLERS,
)

__all__ = [
    "NOT_COLLECTED", "PLACEHOLDERS", "INVALID_ADDRESSES", "BAGHDAD_ADDRESS_POOL",
    "PROVINCE_ADDRESS_MAP", "REPLACE_MAPPING_COLS", "CMS_UPDATE_COLS", "CMS_SHEET_COLS", "RAW_REQUIRED_COLS",
    "_s", "series_available", "balanced_assign", "parse_dob_series",
    "stage_clean_linebreaks", "stage_reset_cms_fields", "stage_address_fix",
    "default_id_invalid_mask", "stage_default_id_assign",
    "validate_replace_reference_inputs", "validate_cms_reference_inputs", "validate_upload_inputs",
    "stage_replace_reference", "stage_cms_integration",
    "mask_name_invalid", "compute_id_validity", "compute_dob_validity",
    "mask_id_dob_invalid", "mask_id_only_invalid", "mask_mobile_missing",
    "_reasons_by_row", "validation_reasons_name", "_id_reason_checks",
    "validation_reasons_id_dob", "validation_reasons_mobile", "validation_reasons_id_only",
    "STAGES", "AUTO_HANDLERS", "UPLOAD_HANDLERS", "CONFIRM_HANDLERS", "CONFIRM_PREVIEW", "MANUAL_STAGES",
]
