import pandas as pd

from ...rules_config import NAME_INVALID_LENGTH
from ..toolbox import _reasons_by_row, _s

_len_label = f"only {NAME_INVALID_LENGTH} character" + ("" if NAME_INVALID_LENGTH == 1 else "s")


def mask_name_invalid(df: pd.DataFrame) -> pd.Series:
    first = _s(df, "ACCOUNT_FIRST_NAME").str.len().eq(NAME_INVALID_LENGTH)
    middle = _s(df, "ACCOUNT_MIDDLE_NAME").str.len().eq(NAME_INVALID_LENGTH)
    last = _s(df, "ACCOUNT_LAST_NAME").str.len().eq(NAME_INVALID_LENGTH)
    return first | middle | last


def validation_reasons_name(df: pd.DataFrame) -> dict[int, list[str]]:
    checks = [
        (_s(df, "ACCOUNT_FIRST_NAME").str.len().eq(NAME_INVALID_LENGTH), f"First name contains {_len_label}."),
        (_s(df, "ACCOUNT_MIDDLE_NAME").str.len().eq(NAME_INVALID_LENGTH), f"Middle name contains {_len_label}."),
        (_s(df, "ACCOUNT_LAST_NAME").str.len().eq(NAME_INVALID_LENGTH), f"Last name contains {_len_label}."),
    ]
    return _reasons_by_row(df, mask_name_invalid(df), checks)
