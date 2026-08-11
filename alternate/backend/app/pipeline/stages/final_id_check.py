import pandas as pd

from ..toolbox import _reasons_by_row, _s, compute_id_validity, id_reason_checks, series_available


def mask_id_only_invalid(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    return ~(type_valid & num_valid)


def mask_id_missing(df: pd.DataFrame) -> pd.Series:
    type_avail = series_available(df.get("ID_TYPE", pd.Series([""] * len(df), index=df.index)))
    num_avail = series_available(df.get("ID_NUMBER", pd.Series([""] * len(df), index=df.index)))
    return ~(type_avail & num_avail)


def validation_reasons_id_only(df: pd.DataFrame) -> dict[int, list[str]]:
    return _reasons_by_row(df, mask_id_only_invalid(df), id_reason_checks(df))


def validation_reasons_id_missing(df: pd.DataFrame) -> dict[int, list[str]]:
    type_avail = series_available(df.get("ID_TYPE", pd.Series([""] * len(df), index=df.index)))
    num_avail = series_available(df.get("ID_NUMBER", pd.Series([""] * len(df), index=df.index)))
    checks = [
        (~type_avail, "ID type is missing."),
        (~num_avail, "ID number is missing."),
    ]
    return _reasons_by_row(df, mask_id_missing(df), checks)

