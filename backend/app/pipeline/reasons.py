import pandas as pd

from .utils import _s, parse_dob_series, series_available
from .validators import mask_id_dob_invalid, mask_id_only_invalid, mask_mobile_missing, mask_name_invalid


def _reasons_by_row(df: pd.DataFrame, mask: pd.Series, checks: list[tuple[pd.Series, str]]) -> dict[int, list[str]]:
    """Return concise operator-facing reasons for the rows a validator flagged."""
    reasons: dict[int, list[str]] = {}
    for row_key in df.index[mask]:
        row_reasons = [message for check, message in checks if bool(check.loc[row_key])]
        reasons[int(row_key)] = row_reasons or ["This row does not meet the validation rule for this step."]
    return reasons


def validation_reasons_name(df: pd.DataFrame) -> dict[int, list[str]]:
    checks = [
        (_s(df, "ACCOUNT_FIRST_NAME").str.len().eq(1), "First name contains only one character."),
        (_s(df, "ACCOUNT_MIDDLE_NAME").str.len().eq(1), "Middle name contains only one character."),
        (_s(df, "ACCOUNT_LAST_NAME").str.len().eq(1), "Last name contains only one character."),
    ]
    return _reasons_by_row(df, mask_name_invalid(df), checks)


def _id_reason_checks(df: pd.DataFrame) -> list[tuple[pd.Series, str]]:
    id_type = _s(df, "ID_TYPE")
    id_type_lower = id_type.str.lower()
    id_number = _s(df, "ID_NUMBER")
    type_available = series_available(id_type)
    number_available = series_available(id_number)
    is_passport = id_type_lower.eq("passport")
    is_national_id = id_type_lower.isin(["national id", "nid", "nationalid"])
    is_civil_id = id_type_lower.isin(["civil id", "civilid", "civil_id"])
    accepted_type = is_passport | is_national_id | is_civil_id

    return [
        (id_type_lower.eq("doc") & type_available,
         "ID type 'doc' is not accepted. Use Passport, National Id, or Civil Id."),
        ((~type_available) | ((~accepted_type) & type_available & ~id_type_lower.eq("doc")),
         "ID type must be Passport, National Id, or Civil Id."),
        (is_passport & ~id_number.str.fullmatch(r"[A-Za-z][A-Za-z0-9]{7,8}", na=False),
         "Passport number must contain 8-9 alphanumeric characters and start with a letter."),
        (is_national_id & ~id_number.str.fullmatch(r"\d{12}", na=False),
         "National ID must contain exactly 12 digits."),
        (is_civil_id & ~number_available, "Civil ID number is required."),
        ((~accepted_type) & ~number_available,
         "ID number cannot be validated until a valid ID type is selected."),
    ]


def validation_reasons_id_dob(df: pd.DataFrame) -> dict[int, list[str]]:
    dob = _s(df, "ACCOUNT_HOLDER_DOB")
    dob_available = series_available(dob)
    parsed = parse_dob_series(dob)
    age_years = (pd.Timestamp.today() - parsed).dt.days / 365.25
    checks = [
        *_id_reason_checks(df),
        (~dob_available, "DOB is missing."),
        (dob_available & parsed.isna(), "DOB is not a recognized date."),
        (parsed.notna() & parsed.dt.year.eq(1900), "DOB cannot use the default year 1900."),
        (parsed.notna() & parsed.dt.year.ne(1900) & age_years.lt(18),
         "DOB indicates the customer is under 18."),
    ]
    return _reasons_by_row(df, mask_id_dob_invalid(df), checks)


def validation_reasons_mobile(df: pd.DataFrame) -> dict[int, list[str]]:
    return _reasons_by_row(
        df,
        mask_mobile_missing(df),
        [(mask_mobile_missing(df), "Phone number is missing or not a valid number.")],
    )


def validation_reasons_id_only(df: pd.DataFrame) -> dict[int, list[str]]:
    return _reasons_by_row(df, mask_id_only_invalid(df), _id_reason_checks(df))
