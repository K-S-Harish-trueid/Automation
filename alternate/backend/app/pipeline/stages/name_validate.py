import pandas as pd

from ..toolbox import _reasons_by_row, _s


def mask_name_invalid(df: pd.DataFrame) -> pd.Series:
    first = _s(df, "ACCOUNT_FIRST_NAME").str.len().eq(1)
    middle = _s(df, "ACCOUNT_MIDDLE_NAME").str.len().eq(1)
    last = _s(df, "ACCOUNT_LAST_NAME").str.len().eq(1)
    return first | middle | last


def validation_reasons_name(df: pd.DataFrame) -> dict[int, list[str]]:
    checks = [
        (_s(df, "ACCOUNT_FIRST_NAME").str.len().eq(1), "First name contains only one character."),
        (_s(df, "ACCOUNT_MIDDLE_NAME").str.len().eq(1), "Middle name contains only one character."),
        (_s(df, "ACCOUNT_LAST_NAME").str.len().eq(1), "Last name contains only one character."),
    ]
    return _reasons_by_row(df, mask_name_invalid(df), checks)
