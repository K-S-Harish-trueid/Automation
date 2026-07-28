import random

import pandas as pd

from .constants import PLACEHOLDERS


def _s(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return df[col].astype(str).str.strip()


def series_available(s: pd.Series) -> pd.Series:
    ss = s.astype(str).str.strip()
    ll = ss.str.lower()
    return (~ss.eq("")) & (~ll.isin(PLACEHOLDERS))


def balanced_assign(n_rows: int, options: list, seed: int = 42) -> list:
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
    random.Random(seed).shuffle(assigned)
    return assigned


def parse_dob_series(values: pd.Series) -> pd.Series:
    """Parse standard K2 timestamps exactly, then fall back for corrections."""
    normalized = values.astype(str).str.strip()
    parsed = pd.to_datetime(
        normalized, errors="coerce", format="%Y-%m-%d %H:%M:%S"
    )
    needs_fallback = parsed.isna() & normalized.ne("")
    if needs_fallback.any():
        parsed.loc[needs_fallback] = pd.to_datetime(
            normalized.loc[needs_fallback],
            errors="coerce",
            dayfirst=True,
            format="mixed",
        )
    return parsed
