import pandas as pd

from .constants import NOT_COLLECTED
from .utils import _s, parse_dob_series, series_available


def mask_name_invalid(df: pd.DataFrame) -> pd.Series:
    first = _s(df, "ACCOUNT_FIRST_NAME").str.len().eq(1)
    middle = _s(df, "ACCOUNT_MIDDLE_NAME").str.len().eq(1)
    last = _s(df, "ACCOUNT_LAST_NAME").str.len().eq(1)
    return first | middle | last


def compute_id_validity(df: pd.DataFrame):
    id_type_s = _s(df, "ID_TYPE")
    id_type_l = id_type_s.str.lower()
    id_num_s = _s(df, "ID_NUMBER")

    type_avail = series_available(df.get("ID_TYPE", pd.Series([""] * len(df), index=df.index)))
    num_avail = series_available(df.get("ID_NUMBER", pd.Series([""] * len(df), index=df.index)))

    is_doc = (id_type_l == "doc") & type_avail
    type_avail = type_avail & ~is_doc
    num_avail = num_avail & ~is_doc

    is_passport = (id_type_l == "passport") & type_avail
    is_nid = id_type_l.isin(["national id", "nid", "nationalid"]) & type_avail
    is_civil = id_type_l.isin(["civil id", "civilid", "civil_id"]) & type_avail

    type_valid = (is_passport | is_nid | is_civil) & type_avail

    passport_ok = id_num_s.str.fullmatch(r"[A-Za-z][A-Za-z0-9]{7,8}", na=False)
    nid_ok = id_num_s.str.fullmatch(r"\d{12}", na=False)

    num_valid = pd.Series(False, index=df.index)
    num_valid |= is_passport & num_avail & passport_ok
    num_valid |= is_nid & num_avail & nid_ok
    num_valid |= is_civil & num_avail

    return type_valid, num_valid


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


def mask_id_only_invalid(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    return ~(type_valid & num_valid)


def mask_mobile_missing(df: pd.DataFrame) -> pd.Series:
    return _s(df, "PHONE_NUMBER").eq(NOT_COLLECTED) | _s(df, "PHONE_NUMBER").eq("")
