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
