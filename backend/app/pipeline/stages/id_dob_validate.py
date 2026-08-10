import pandas as pd

from ..toolbox import _reasons_by_row, _s, compute_id_validity, id_reason_checks, parse_dob_series, series_available

# The default/placeholder DOB is 1 Jan 1905 -- per requirement, any DOB after
# this exact date is valid (so e.g. 1905-02-01 is fine), only this date
# itself or anything earlier is treated as a non-real DOB.
DEFAULT_DOB_CUTOFF = pd.Timestamp("1905-01-01")


def _age_years_at_opening(df: pd.DataFrame, parsed_dob: pd.Series) -> pd.Series:
    """Age must be checked against when the account was actually opened, not
    today -- otherwise an account opened for a minor years ago looks fine
    today once enough time has passed. Falls back to today only where
    DATE_OPENED itself can't be parsed, so a messy opening date doesn't
    spuriously flag an otherwise-clean DOB."""
    opened_parsed = parse_dob_series(_s(df, "DATE_OPENED"))
    reference_date = opened_parsed.where(opened_parsed.notna(), pd.Timestamp.today())
    return (reference_date - parsed_dob).dt.days / 365.25


def compute_dob_validity(df: pd.DataFrame) -> pd.Series:
    dob_s = _s(df, "ACCOUNT_HOLDER_DOB")
    avail = series_available(df.get("ACCOUNT_HOLDER_DOB", pd.Series([""] * len(df), index=df.index)))
    parsed = parse_dob_series(dob_s)
    valid_format = parsed.notna()
    after_cutoff = parsed > DEFAULT_DOB_CUTOFF
    is_adult = _age_years_at_opening(df, parsed) >= 18
    return (avail & valid_format & after_cutoff & is_adult).fillna(False)


def mask_id_dob_invalid(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    dob_valid = compute_dob_validity(df)
    return ~(type_valid & num_valid & dob_valid)


def mask_dob_invalid(df: pd.DataFrame) -> pd.Series:
    """DOB-only half of mask_id_dob_invalid -- used by the Stage 1 Naresh
    handoff, which reviews DOB separately from ID."""
    return ~compute_dob_validity(df)


def _dob_reason_checks(df: pd.DataFrame, dob: pd.Series, parsed: pd.Series) -> list[tuple[pd.Series, str]]:
    dob_available = series_available(dob)
    age_years = _age_years_at_opening(df, parsed)
    return [
        (~dob_available, "DOB is missing."),
        (dob_available & parsed.isna(), "DOB is not a recognized date."),
        (parsed.notna() & (parsed <= DEFAULT_DOB_CUTOFF), "DOB must be after 1 January 1905."),
        (parsed.notna() & (parsed > DEFAULT_DOB_CUTOFF) & age_years.lt(18),
         "Customer was under 18 when the account was opened."),
    ]


def validation_reasons_dob_only(df: pd.DataFrame) -> dict[int, list[str]]:
    dob = _s(df, "ACCOUNT_HOLDER_DOB")
    parsed = parse_dob_series(dob)
    return _reasons_by_row(df, mask_dob_invalid(df), _dob_reason_checks(df, dob, parsed))


def validation_reasons_id_dob(df: pd.DataFrame) -> dict[int, list[str]]:
    dob = _s(df, "ACCOUNT_HOLDER_DOB")
    parsed = parse_dob_series(dob)
    checks = [*id_reason_checks(df), *_dob_reason_checks(df, dob, parsed)]
    return _reasons_by_row(df, mask_id_dob_invalid(df), checks)
