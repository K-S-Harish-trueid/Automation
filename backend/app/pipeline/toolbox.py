"""Shared helpers used by 2+ stage files. If something is only used by one
stage, it belongs in that stage's own file under stages/, not here -- keep
this file small so "do I need to open the toolbox?" stays a rare question.
"""
import re

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
# other. "nan"/"none"/"nat" are always included too, unconditionally (not
# left to EXTRA_EMPTY_PLACEHOLDER_VALUES) -- those are Python/pandas' own
# text spellings of "no value" (str(None), str(float("nan")), str(pd.NaT)),
# not a business rule an operator should be able to accidentally unset via
# .env. Data sources are expected to clean their own NaNs before handing a
# dataframe to the pipeline (see historical_db.load_reference_df's
# .fillna("")) -- this is a second line of defense, not the fix itself.
PLACEHOLDERS = {"", "nan", "none", "nat", NOT_COLLECTED.lower(), *EXTRA_EMPTY_PLACEHOLDER_VALUES}


def _s(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    # fillna BEFORE astype(str): a genuinely-missing (NaN/None) cell doesn't
    # reliably turn into the text "nan" here -- pandas' current default
    # string dtype (as of pandas 3.0) preserves it as an actual missing
    # value straight through .astype(str).str.strip(), not a "nan" string.
    # Flattening it to "" first, on the raw column, is what actually closes
    # that gap for every one of this helper's many callers.
    return df[col].fillna("").astype(str).str.strip()


def series_available(s: pd.Series) -> pd.Series:
    # Checked on the raw input, before stringifying -- same reasoning as
    # _s() above. Several callers (address_fix.py, id_dob_validate.py,
    # toolbox.py's own ID-availability checks) pass a raw df column
    # straight in, not _s()'s already-cleaned output, so this can't assume
    # the NaN has already been flattened to "" upstream.
    missing = s.isna()
    ss = s.astype(str).str.strip()
    ll = ss.str.lower()
    return (~missing) & (~ss.eq("")) & (~ll.isin(PLACEHOLDERS))


def series_has_letter(s: pd.Series) -> pd.Series:
    """True where the value contains at least one alphabetic character, in
    any script (Arabic included). Deliberately uses Python's own
    str.isalpha() per value via .map() instead of a `\\w`/`\\W` regex --
    pandas' vectorized .str.contains() on this pandas version's default
    string dtype gives wrong answers for non-Latin text (tested: it misses
    letters in Arabic addresses entirely), while .astype(object) + isalpha()
    matches Python's real Unicode behavior."""
    return s.astype(str).map(lambda value: any(ch.isalpha() for ch in value))


_DIGIT_RUN_RE = re.compile(r"[0-9]+")


def series_has_leading_zero_run(s: pd.Series, min_zeros: int = 3) -> pd.Series:
    """True where the value contains a digit run that itself STARTS with
    at least `min_zeros` consecutive zeros (e.g. "000000", "00000...") --
    catches padded/junk numeric values glued onto an otherwise real
    address (rules/06-address_fix.txt's added Rule 1). ASCII digits only
    ([0-9], not \\d -- \\d also matches Arabic-Indic digit characters);
    every real example seen so far uses Western digits even inside Arabic
    text. Same per-value .map() approach as series_has_letter, for the
    same reason: no vectorized pandas regex on this data.

    Deliberately "starts with", not "contains 3+ zeros anywhere" -- a real
    Iraqi postal code (e.g. "10001", "20005") can contain 3 consecutive
    zeros in the middle without being junk; requiring the zeros to lead
    the run avoids flagging those. Known, accepted tradeoff: a short
    trailing-zero value glued onto an address (e.g. "...4000", "...6000")
    is NOT caught by this rule since its zeros trail rather than lead --
    catching those was considered and rejected in favor of not risking
    real postal codes."""
    prefix = "0" * min_zeros
    return s.astype(str).map(lambda value: any(run.startswith(prefix) for run in _DIGIT_RUN_RE.findall(value)))


def series_has_long_digit_run(s: pd.Series, min_len: int = 7) -> pd.Series:
    """True where the value contains a run of `min_len`+ consecutive
    digits anywhere (rules/06-address_fix.txt's added Rule 2) -- a real
    Iraqi address's house number (1-4 digits) or postal code (5 digits)
    never gets this long, so a run this long is essentially always a
    phone number or other corrupted numeric data pasted into the address
    field."""
    return s.astype(str).map(lambda value: any(len(run) >= min_len for run in _DIGIT_RUN_RE.findall(value)))


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
    """Full regex-based ID_TYPE/ID_NUMBER validity. Used by id_dob_validate
    (Stage 1, before Haider's Stage 3 response exists) and, via
    mask_id_only_invalid, the Stage 1/2 dispatch sheet content and the
    completion quality-summary stat -- all cases where flagging a format
    problem is the point. NOT used by final_id_check or default_id anymore
    (see id_missing_mask below) -- once Haider's response is in, whatever he
    provided is treated as final regardless of format; only a value he left
    blank (or the "doc" placeholder) still needs anything done about it."""
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


def id_missing_mask(df: pd.DataFrame) -> pd.Series:
    """Post-Haider-response definition of "still needs an ID": ID_TYPE or
    ID_NUMBER left blank, or ID_TYPE is the "doc" placeholder (a known junk
    value from earlier in the pipeline, not a real Haider answer -- treated
    the same as blank). Nothing about format/regex -- anything else Haider
    filled in is accepted as final even if it wouldn't pass
    compute_id_validity above. Shared by final_id_check.py (the manual-
    review gate) and default_id.py (which must never overwrite a real,
    non-blank Haider answer just because it doesn't match the expected
    format -- the two need to agree on exactly which rows are "missing")."""
    id_type = _s(df, "ID_TYPE")
    id_number = _s(df, "ID_NUMBER")
    type_avail = series_available(id_type)
    num_avail = series_available(id_number)
    is_doc = id_type.str.lower().eq("doc")
    return ~type_avail | ~num_avail | is_doc


def id_missing_reason_checks(df: pd.DataFrame) -> list[tuple[pd.Series, str]]:
    id_type = _s(df, "ID_TYPE")
    id_number = _s(df, "ID_NUMBER")
    type_avail = series_available(id_type)
    num_avail = series_available(id_number)
    is_doc = id_type.str.lower().eq("doc")
    return [
        (is_doc, "ID type 'doc' is not a real ID type -- provide one, or leave blank to auto-generate a Civil ID."),
        (~type_avail & ~is_doc, "ID type is missing."),
        (~num_avail, "ID number is missing."),
    ]


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
