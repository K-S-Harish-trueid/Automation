"""Business-rule thresholds pulled out of pipeline stage code and into env
vars (see .env.example) -- so these can be tuned from the deployed server's
.env file, no code change or redeploy needed.

Every default below matches exactly what used to be hardcoded in the stage
files this replaces (id_dob_validate.py, name_validate.py, toolbox.py,
default_id.py) -- an absent/untouched .env changes nothing.

Not everything the client might call "a rule" lives here -- see the sweep
that led to this file: address_fix.py's address denylist/pools are real
data (dozens of Arabic/English address strings), not scalar config, and
don't fit a flat env-file format. Those stay in code.

Loaded once, here, rather than relying on whichever entry point (run.py,
uvicorn directly, a standalone script) to have called load_dotenv() first --
this module is imported (directly or transitively) by every stage that
needs a rule value, so loading it here guarantees .env is read regardless
of how the process was started.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_date(name: str, default_iso: str):
    import pandas as pd
    raw = os.environ.get(name)
    return pd.Timestamp(raw) if raw and raw.strip() else pd.Timestamp(default_iso)


def _env_list(name: str, default_csv: str) -> list[str]:
    raw = os.environ.get(name, default_csv)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _env_positive_int(name: str, default: int) -> int:
    """Like _env_int, but never crashes the app on a malformed value --
    falls back to `default` on a parse error and clamps below 1 up to 1.
    Used for operational capacity settings (MAX_STORED_JOBS) where a typo'd
    env value shouldn't be able to take the whole app down at import time,
    unlike the business-rule values above where a bad value getting through
    silently is the bigger risk."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# ── Tier 1: clean scalars ────────────────────────────────────────────────

# id_dob_validate.py: any DOB on/before this date is treated as a
# placeholder, not a real birthdate.
DOB_CUTOFF = _env_date("DOB_CUTOFF_DATE", "1905-01-01")

# id_dob_validate.py: age (both today and at DATE_OPENED) must be >= this.
AGE_OF_MAJORITY = _env_int("AGE_OF_MAJORITY", 18)

# name_validate.py: a first/middle/last name that's exactly this many
# characters is flagged invalid (default 1 -- catches single-initial junk
# like "A"). Not a minimum-length check -- an exact-length match, same as
# the original hardcoded `.eq(1)`.
NAME_INVALID_LENGTH = _env_int("NAME_INVALID_LENGTH", 1)

# toolbox.py: the sentinel string upstream systems use for "field not
# collected". Also feeds PLACEHOLDERS below (lowercased) automatically.
NOT_COLLECTED_PLACEHOLDER = _env_str("NOT_COLLECTED_PLACEHOLDER", "XXX_NOT_COLLECTED_XXX")


# ── Tier 2: small regex/lists -- riskier, a typo here silently changes
# validation results instead of erroring, so edit with care. ─────────────

# toolbox.py: ID_NUMBER format per ID_TYPE. If you change these, the
# reason-text strings in toolbox.py's id_reason_checks describe the OLD
# format in plain English and won't auto-update (can't generate accurate
# human text from an arbitrary regex) -- update those messages to match.
PASSPORT_ID_REGEX = _env_str("PASSPORT_ID_REGEX", r"[A-Za-z][A-Za-z0-9]{7,8}")
NATIONAL_ID_REGEX = _env_str("NATIONAL_ID_REGEX", r"\d{12}")

# toolbox.py: which ID_TYPE text values map to each canonical type
# (case-insensitive). The three canonical types themselves (Passport /
# National Id / Civil Id) are NOT configurable -- only what counts as a
# synonym for each.
PASSPORT_ID_SYNONYMS = _env_list("PASSPORT_ID_SYNONYMS", "passport")
NATIONAL_ID_SYNONYMS = _env_list("NATIONAL_ID_SYNONYMS", "national id,nid,nationalid")
CIVIL_ID_SYNONYMS = _env_list("CIVIL_ID_SYNONYMS", "civil id,civilid,civil_id")

# toolbox.py's PLACEHOLDERS set: extra values (besides "" and the
# NOT_COLLECTED sentinel above, which are always included) treated as
# "field is empty" across every stage.
EXTRA_EMPTY_PLACEHOLDER_VALUES = _env_list("EXTRA_EMPTY_PLACEHOLDER_VALUES", "0,00000,null,none,na,n/a")

# default_id.py: format of a generated Civil ID -- PREFIX + N random
# digits, zero-padded (default "00" + 6 digits = 8 characters total).
GENERATED_ID_PREFIX = _env_str("GENERATED_ID_PREFIX", "00")
GENERATED_ID_RANDOM_DIGITS = _env_int("GENERATED_ID_RANDOM_DIGITS", 6)

# store.py: rolling cap on how many completed job folders are kept on disk
# (job folders contain KYC data) before the oldest non-processing one is
# pruned to make room for a new one. Moved here from store.py itself --
# that module gets imported before anything triggers this file's
# load_dotenv() call (main.py imports store before routes.handoff, which
# is what actually loads .env), so a value set only in .env was silently
# never picked up. Importing it from here instead guarantees .env is
# already loaded, since this module loads it itself, in its own file,
# before any other top-level statement here runs.
MAX_STORED_JOBS = _env_positive_int("K2_MAX_STORED_JOBS", 100)
