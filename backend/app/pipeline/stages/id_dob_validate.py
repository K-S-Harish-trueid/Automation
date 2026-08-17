import pandas as pd

from ...rules_config import AGE_OF_MAJORITY, DOB_CUTOFF
from ..toolbox import _reasons_by_row, _s, compute_id_validity, id_reason_checks, parse_dob_series, series_available

# The default/placeholder DOB is 1 Jan 1905 -- per requirement, any DOB after
# this exact date is valid (so e.g. 1905-02-01 is fine), only this date
# itself or anything earlier is treated as a non-real DOB. Configurable via
# DOB_CUTOFF_DATE in .env -- see rules_config.py.
DEFAULT_DOB_CUTOFF = DOB_CUTOFF


def _age_years(reference_date, parsed_dob: pd.Series) -> pd.Series:
    """Exact calendar age in whole years -- not a days/365.25 approximation,
    which can misjudge someone within about half a day of an exact
    year-boundary birthday depending on how leap days happen to land in
    their specific span (e.g. 18 years and 0 days can compute to 17.9986).
    `reference_date` may be a single Timestamp (broadcast to every row) or a
    per-row Series aligned with parsed_dob."""
    if isinstance(reference_date, pd.Timestamp):
        ref_year, ref_month, ref_day = reference_date.year, reference_date.month, reference_date.day
    else:
        ref_year, ref_month, ref_day = reference_date.dt.year, reference_date.dt.month, reference_date.dt.day
    years = ref_year - parsed_dob.dt.year
    birthday_passed = (parsed_dob.dt.month < ref_month) | (
        (parsed_dob.dt.month == ref_month) & (parsed_dob.dt.day <= ref_day)
    )
    return years - (~birthday_passed).astype(int)


def _age_years_now(parsed_dob: pd.Series) -> pd.Series:
    """Current age, as of today -- independent of DATE_OPENED, so a bogus or
    future-dated DATE_OPENED can't mask someone who is actually under 18
    right now (see _age_years_at_opening below, which age-at-opening alone
    couldn't catch)."""
    return _age_years(pd.Timestamp.today(), parsed_dob)


def _opened_parsed(df: pd.DataFrame) -> pd.Series:
    return parse_dob_series(_s(df, "DATE_OPENED"))


def _age_years_at_opening(df: pd.DataFrame, parsed_dob: pd.Series) -> pd.Series:
    """Age at DATE_OPENED, not today -- otherwise an account opened for a
    minor years ago looks fine today once enough time has passed. Falls back
    to today only where DATE_OPENED itself can't be parsed, so a messy
    opening date doesn't spuriously flag an otherwise-clean DOB. This is
    deliberately a second, independent check from _age_years_now above --
    someone can be an adult today (passes that check) and still have had
    their account opened while they were a minor (fails this one)."""
    opened_parsed = _opened_parsed(df)
    reference_date = opened_parsed.where(opened_parsed.notna(), pd.Timestamp.today())
    return _age_years(reference_date, parsed_dob)


def compute_dob_validity(df: pd.DataFrame) -> pd.Series:
    dob_s = _s(df, "ACCOUNT_HOLDER_DOB")
    avail = series_available(df.get("ACCOUNT_HOLDER_DOB", pd.Series([""] * len(df), index=df.index)))
    parsed = parse_dob_series(dob_s)
    today = pd.Timestamp.today()
    valid_format = parsed.notna()
    after_cutoff = parsed > DEFAULT_DOB_CUTOFF
    not_future_dob = parsed <= today
    opened_parsed = _opened_parsed(df)
    not_future_opened = opened_parsed.isna() | (opened_parsed <= today)
    is_adult_now = _age_years_now(parsed) >= AGE_OF_MAJORITY
    is_adult_at_opening = _age_years_at_opening(df, parsed) >= AGE_OF_MAJORITY
    return (
        avail & valid_format & after_cutoff & not_future_dob & not_future_opened
        & is_adult_now & is_adult_at_opening
    ).fillna(False)


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
    today = pd.Timestamp.today()
    age_now = _age_years_now(parsed)
    age_at_opening = _age_years_at_opening(df, parsed)
    opened_parsed = _opened_parsed(df)
    # A DOB that's missing/unparseable/before-cutoff/in-the-future is its own
    # distinct problem -- gate every check below it on the DOB itself being a
    # real, sane date, so those rows get exactly one DOB-shape reason, not
    # also a nonsensical age reason derived from a broken date (e.g. a future
    # DOB producing a negative "current age" that would otherwise misreport
    # as "currently under 18").
    sane_dob = parsed.notna() & (parsed > DEFAULT_DOB_CUTOFF) & (parsed <= today)
    # Dynamic, not hardcoded text -- if DOB_CUTOFF_DATE/AGE_OF_MAJORITY get
    # overridden via .env, the operator-facing reason still matches the
    # actual rule instead of quoting the old default.
    cutoff_label = f"{DEFAULT_DOB_CUTOFF.day} {DEFAULT_DOB_CUTOFF.strftime('%B %Y')}"
    return [
        (~dob_available, "DOB is missing."),
        (dob_available & parsed.isna(), "DOB is not a recognized date."),
        (parsed.notna() & (parsed <= DEFAULT_DOB_CUTOFF), f"DOB must be after {cutoff_label}."),
        (parsed.notna() & (parsed > DEFAULT_DOB_CUTOFF) & (parsed > today), "DOB is in the future."),
        # Opening-date plausibility and age are separate, both-can-be-true
        # facts about a row (unlike the two age checks below, which are two
        # measurements of one underlying problem) -- both can show together.
        (sane_dob & opened_parsed.notna() & (opened_parsed > today), "Account opening date is in the future."),
        # Checked in order: currently under 18 first; only if they've since
        # turned 18 do we separately check whether the account was opened
        # while they were still a minor -- so a row gets exactly one of
        # these two reasons, not both, for what's really one underlying age
        # problem.
        (sane_dob & age_now.lt(AGE_OF_MAJORITY), f"Customer is currently under {AGE_OF_MAJORITY}."),
        (sane_dob & age_now.ge(AGE_OF_MAJORITY) & age_at_opening.lt(AGE_OF_MAJORITY),
         f"Customer was under {AGE_OF_MAJORITY} when the account was opened."),
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
