import pandas as pd

from ..toolbox import _reasons_by_row, compute_id_validity, id_missing_mask, id_missing_reason_checks, id_reason_checks


def mask_id_only_invalid(df: pd.DataFrame) -> pd.Series:
    """Regex-based ID_TYPE/ID_NUMBER validity -- still used for the Stage
    1/2 dispatch "ID Corrections" sheet content (Naresh/Haider need real
    format flags to know what to fix) and the completion quality-summary
    stat. NOT the final_id_check manual-review gate itself anymore -- see
    final_id_check_invalid_mask below, which only cares whether Haider left
    something blank, not whether it matches the expected format."""
    type_valid, num_valid = compute_id_validity(df)
    return ~(type_valid & num_valid)


def validation_reasons_id_only(df: pd.DataFrame) -> dict[int, list[str]]:
    return _reasons_by_row(df, mask_id_only_invalid(df), id_reason_checks(df))


def final_id_check_invalid_mask(df: pd.DataFrame) -> pd.Series:
    """The actual final_id_check gate, run after Haider's Stage 3 response
    is merged in: whatever Haider provided is treated as final, format and
    all -- only a genuinely blank ID_TYPE/ID_NUMBER (or the "doc"
    placeholder, which means the same thing: he doesn't have it) still
    needs attention here."""
    return id_missing_mask(df)


def validation_reasons_final_id_check(df: pd.DataFrame) -> dict[int, list[str]]:
    return _reasons_by_row(df, final_id_check_invalid_mask(df), id_missing_reason_checks(df))
