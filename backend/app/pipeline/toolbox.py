"""Shared helpers used by 2+ stage files. If something is only used by one
stage, it belongs in that stage's own file under stages/, not here -- keep
this file small so "do I need to open the toolbox?" stays a rare question.
"""
import pandas as pd

from ..rules_config import (
    CIVIL_ID_SYNONYMS,
    EXTRA_EMPTY_PLACEHOLDER_VALUES,
    NATIONAL_ID_REGEX,
    NATIONAL_ID_SYNONYMS,
    NOT_COLLECTED_PLACEHOLDER,
    PASSPORT_ID_REGEX,
    PASSPORT_ID_SYNONYMS,
)

NOT_COLLECTED = NOT_COLLECTED_PLACEHOLDER

# "" is always empty regardless of config; NOT_COLLECTED is folded in here
# (lowercased) so the two settings can never drift out of sync with each
# other.
PLACEHOLDERS = {"", NOT_COLLECTED.lower(), *EXTRA_EMPTY_PLACEHOLDER_VALUES}


def _s(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return df[col].astype(str).str.strip()


def series_available(s: pd.Series) -> pd.Series:
    ss = s.astype(str).str.strip()
    ll = ss.str.lower()
    return (~ss.eq("")) & (~ll.isin(PLACEHOLDERS))


def series_has_letter(s: pd.Series) -> pd.Series:
    """True where the value contains at least one alphabetic character, in
    any script (Arabic included). Deliberately uses Python's own
    str.isalpha() per value via .map() instead of a `\\w`/`\\W` regex --
    pandas' vectorized .str.contains() on this pandas version's default
    string dtype gives wrong answers for non-Latin text (tested: it misses
    letters in Arabic addresses entirely), while .astype(object) + isalpha()
    matches Python's real Unicode behavior."""
    return s.astype(str).map(lambda value: any(ch.isalpha() for ch in value))


def balanced_assign(n_rows: int, options: list[str], seed: int = 42) -> list[str]:
    """Near-equally distribute `options` across `n_rows` slots, then shuffle
    deterministically (fixed seed -- same input always produces the same
    assignment, so re-running a job doesn't silently reshuffle synthetic
    values already handed out)."""
    if n_rows <= 0:
        return []
    clean_options = [o for o in options if isinstance(o, str) and o.strip() != ""]
    if not clean_options:
        return [""] * n_rows

    base = n_rows // len(clean_options)
    rem = n_rows % len(clean_options)

    assigned = []
    for opt in clean_options:
        assigned.extend([opt] * base)
    assigned.extend(clean_options[:rem])

    import random
    rnd = random.Random(seed)
    rnd.shuffle(assigned)
    return assigned


def parse_dob_series(values: pd.Series) -> pd.Series:
    """Parse standard K2 timestamps exactly, then fall back for corrections.

    Three passes, each only touching what the previous one left as NaT:
    1. The exact raw-export shape ("%Y-%m-%d %H:%M:%S").
    2. A bare ISO date ("%Y-%m-%d") -- year-month-day order is unambiguous
       by construction, so this must NOT go through dayfirst inference.
       Skipping this pass and falling straight to (3) used to send every
       time-less ISO date (any hand-typed correction, e.g. "2008-08-11")
       through a dayfirst=True "mixed format" guess, which silently swapped
       month and day for any date where both are <=12 (~39% of all dates) --
       e.g. "2008-08-11" (11 Aug) was silently becoming 8 Nov. Real raw K2
       exports always carry a time suffix so pass 1 catches them and this
       bug never showed up there, but any manually-typed correction from
       Naresh/Haider without one was exposed to it.
    3. Everything else (e.g. genuinely ambiguous DD/MM/YYYY-style slash
       dates) -- dayfirst=True here is intentional and correct, this is the
       one place month/day order actually needs to be inferred."""
    normalized = values.astype(str).str.strip()
    parsed = pd.to_datetime(
        normalized, errors="coerce", format="%Y-%m-%d %H:%M:%S"
    )
    needs_iso_fallback = parsed.isna() & normalized.ne("")
    if needs_iso_fallback.any():
        parsed.loc[needs_iso_fallback] = pd.to_datetime(
            normalized.loc[needs_iso_fallback], errors="coerce", format="%Y-%m-%d",
        )
    needs_mixed_fallback = parsed.isna() & normalized.ne("")
    if needs_mixed_fallback.any():
        parsed.loc[needs_mixed_fallback] = pd.to_datetime(
            normalized.loc[needs_mixed_fallback],
            errors="coerce",
            dayfirst=True,
            format="mixed",
        )
    return parsed


def _reasons_by_row(df: pd.DataFrame, mask: pd.Series, checks: list[tuple[pd.Series, str]]) -> dict[int, list[str]]:
    """Return concise operator-facing reasons for the rows a validator flagged."""
    reasons: dict[int, list[str]] = {}
    for row_key in df.index[mask]:
        row_reasons = [message for check, message in checks if bool(check.loc[row_key])]
        reasons[int(row_key)] = row_reasons or ["This row does not meet the validation rule for this step."]
    return reasons


def compute_id_validity(df: pd.DataFrame):
    """Shared by id_dob_validate, final_id_check, and default_id -- all
    three need the exact same ID_TYPE/ID_NUMBER rule (id_dob_validate and
    final_id_check as a validity check, default_id to know which rows still
    need a generated ID after final_id_check)."""
    id_type_s = _s(df, "ID_TYPE")
    id_type_l = id_type_s.str.lower()
    id_num_s = _s(df, "ID_NUMBER")

    type_avail = series_available(df.get("ID_TYPE", pd.Series([""] * len(df), index=df.index)))
    num_avail = series_available(df.get("ID_NUMBER", pd.Series([""] * len(df), index=df.index)))

    is_doc = (id_type_l == "doc") & type_avail
    type_avail = type_avail & ~is_doc
    num_avail = num_avail & ~is_doc

    is_passport = id_type_l.isin(PASSPORT_ID_SYNONYMS) & type_avail
    is_nid = id_type_l.isin(NATIONAL_ID_SYNONYMS) & type_avail
    is_civil = id_type_l.isin(CIVIL_ID_SYNONYMS) & type_avail

    type_valid = (is_passport | is_nid | is_civil) & type_avail

    passport_ok = id_num_s.str.fullmatch(PASSPORT_ID_REGEX, na=False)
    nid_ok = id_num_s.str.fullmatch(NATIONAL_ID_REGEX, na=False)

    num_valid = pd.Series(False, index=df.index)
    num_valid |= is_passport & num_avail & passport_ok
    num_valid |= is_nid & num_avail & nid_ok
    num_valid |= is_civil & num_avail

    return type_valid, num_valid


def id_reason_checks(df: pd.DataFrame) -> list[tuple[pd.Series, str]]:
    """Shared by id_dob_validate and final_id_check -- same ID_TYPE/ID_NUMBER
    reason messages, both steps just apply them at different points."""
    id_type = _s(df, "ID_TYPE")
    id_type_lower = id_type.str.lower()
    id_number = _s(df, "ID_NUMBER")
    type_available = series_available(id_type)
    number_available = series_available(id_number)
    is_passport = id_type_lower.isin(PASSPORT_ID_SYNONYMS)
    is_national_id = id_type_lower.isin(NATIONAL_ID_SYNONYMS)
    is_civil_id = id_type_lower.isin(CIVIL_ID_SYNONYMS)
    accepted_type = is_passport | is_national_id | is_civil_id

    return [
        (id_type_lower.eq("doc") & type_available,
         "ID type 'doc' is not accepted. Use Passport, National Id, or Civil Id."),
        ((~type_available) | ((~accepted_type) & type_available & ~id_type_lower.eq("doc")),
         "ID type must be Passport, National Id, or Civil Id."),
        # These two message strings describe the DEFAULT regex in plain
        # English -- if PASSPORT_ID_REGEX/NATIONAL_ID_REGEX get overridden
        # via .env to something else, update this text to match (can't be
        # derived automatically from an arbitrary regex).
        (is_passport & ~id_number.str.fullmatch(PASSPORT_ID_REGEX, na=False),
         "Passport number must contain 8-9 alphanumeric characters and start with a letter."),
        (is_national_id & ~id_number.str.fullmatch(NATIONAL_ID_REGEX, na=False),
         "National ID must contain exactly 12 digits."),
        (is_civil_id & ~number_available, "Civil ID number is required."),
        ((~accepted_type) & ~number_available,
         "ID number cannot be validated until a valid ID type is selected."),
    ]
