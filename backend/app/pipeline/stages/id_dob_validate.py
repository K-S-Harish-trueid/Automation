import pandas as pd

from ..toolbox import _reasons_by_row, _s, compute_id_validity, id_reason_checks, parse_dob_series, series_available


def compute_dob_validity(df: pd.DataFrame) -> pd.Series:
    dob_s = _s(df, "ACCOUNT_HOLDER_DOB")
    avail = series_available(df.get("ACCOUNT_HOLDER_DOB", pd.Series([""] * len(df), index=df.index)))
    parsed = parse_dob_series(dob_s)
    valid_format = parsed.notna()
    not_default = parsed.dt.year.ne(1900)
    age_years = (pd.Timestamp.today() - parsed).dt.days / 365.25
    is_adult = age_years >= 18
    return (avail & valid_format & not_default & is_adult).fillna(False)


def mask_id_dob_invalid(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    dob_valid = compute_dob_validity(df)
    return ~(type_valid & num_valid & dob_valid)


def validation_reasons_id_dob(df: pd.DataFrame) -> dict[int, list[str]]:
    dob = _s(df, "ACCOUNT_HOLDER_DOB")
    dob_available = series_available(dob)
    parsed = parse_dob_series(dob)
    age_years = (pd.Timestamp.today() - parsed).dt.days / 365.25
    checks = [
        *id_reason_checks(df),
        (~dob_available, "DOB is missing."),
        (dob_available & parsed.isna(), "DOB is not a recognized date."),
        (parsed.notna() & parsed.dt.year.eq(1900), "DOB cannot use the default year 1900."),
        (parsed.notna() & parsed.dt.year.ne(1900) & age_years.lt(18),
         "DOB indicates the customer is under 18."),
    ]
    return _reasons_by_row(df, mask_id_dob_invalid(df), checks)
