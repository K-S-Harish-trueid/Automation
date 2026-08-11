import pandas as pd

from ..toolbox import NOT_COLLECTED, _reasons_by_row, _s


def mask_mobile_missing(df: pd.DataFrame) -> pd.Series:
    phone = _s(df, "PHONE_NUMBER")
    return phone.eq(NOT_COLLECTED) | phone.eq("") | phone.str.fullmatch(r"0+", na=False)


def validation_reasons_mobile(df: pd.DataFrame) -> dict[int, list[str]]:
    return _reasons_by_row(
        df,
        mask_mobile_missing(df),
        [(mask_mobile_missing(df), "Phone number is missing or not a valid number.")],
    )
