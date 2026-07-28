import pandas as pd

from ..toolbox import _reasons_by_row, compute_id_validity, id_reason_checks


def mask_id_only_invalid(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    return ~(type_valid & num_valid)


def validation_reasons_id_only(df: pd.DataFrame) -> dict[int, list[str]]:
    return _reasons_by_row(df, mask_id_only_invalid(df), id_reason_checks(df))
