"""SQLite-backed cache for the historical-override reference data used by
the `replace` stage.

Why this exists: parsing Historical_Dataset.xlsx via pandas/calamine costs
28-30s per job (profiled: 130MB, 797k rows) even though the actual match
logic is already fast and hash-indexed (~4s, see pipeline/stages/replace.py).
Importing that data into SQLite once lets later jobs query it directly
instead of re-parsing the xlsx from scratch every time.

Seeded now from the existing Historical_Dataset.xlsx (see seed_from_file,
run once via a one-off script/shell command -- not called automatically by
the app). Later: a button on the completed-job screen is planned to also
insert/replace each finished job's rows here (see TODO), so the store grows
over time. upsert_rows is keyed on ACCOUNT_NUMBER (INSERT OR REPLACE) so
re-adding an account updates it in place instead of duplicating -- that
button isn't wired up yet, this function is just ready for it.
"""
import sqlite3
from pathlib import Path

import pandas as pd

from .pipeline.stages.replace import REPLACE_MAPPING_COLS

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "historical.db"
TABLE = "historical"
KEY = "ACCOUNT_NUMBER"
COLUMNS = [KEY, *REPLACE_MAPPING_COLS]

# SQLite's default compiled-in bound-parameter limit (SQLITE_MAX_VARIABLE_NUMBER).
# Chunk any IN (...) query well under this so a large job never trips it.
_QUERY_CHUNK = 500


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _clean_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df[KEY] = df[KEY].astype(str).str.strip()
    return df


def seed_from_file(path) -> int:
    """One-time (or re-run-to-refresh) full import of a historical reference
    file (xlsx/csv) into SQLite -- replaces the table entirely. Not called by
    the running app; run manually when (re)seeding from a fresh export."""
    from . import store  # local import: avoid a cycle, store imports little from here

    path = Path(path)
    df = store.read_table(path.read_bytes(), path.name)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Reference file missing columns: {missing}")
    df = _clean_key(df[COLUMNS])
    df = df.sort_values(by=[KEY]).drop_duplicates(subset=[KEY], keep="last")

    conn = _connect()
    try:
        df.to_sql(TABLE, conn, if_exists="replace", index=False)
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_{KEY} ON {TABLE} ({KEY})")
        conn.commit()
    finally:
        conn.close()
    return len(df)


def upsert_rows(df: pd.DataFrame) -> int:
    """Insert-or-replace rows keyed on ACCOUNT_NUMBER -- for the planned
    'add this completed job to historical' button. Safe to call repeatedly
    with overlapping accounts; never duplicates a row."""
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Cannot add to historical store, missing columns: {missing}")
    rows = _clean_key(df[COLUMNS]).drop_duplicates(subset=[KEY], keep="last")

    conn = _connect()
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} ({", ".join(f'"{c}"' for c in COLUMNS)})
        """)
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_{KEY} ON {TABLE} ({KEY})")
        placeholders = ",".join("?" for _ in COLUMNS)
        col_list = ",".join(f'"{c}"' for c in COLUMNS)
        conn.executemany(
            f"INSERT OR REPLACE INTO {TABLE} ({col_list}) VALUES ({placeholders})",
            rows[COLUMNS].itertuples(index=False, name=None),
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def has_data() -> bool:
    if not DB_PATH.exists():
        return False
    conn = _connect()
    try:
        cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,))
        if cur.fetchone() is None:
            return False
        return conn.execute(f"SELECT 1 FROM {TABLE} LIMIT 1").fetchone() is not None
    finally:
        conn.close()


def load_reference_df(account_numbers) -> pd.DataFrame:
    """Fetch only the rows this job's ACCOUNT_NUMBERs need, chunked to stay
    under SQLite's bound-parameter limit. Empty/no-match input returns an
    empty (but correctly-columned) frame -- callers already handle a
    zero-match reference file the same way an xlsx upload would."""
    numbers = sorted({str(n).strip() for n in account_numbers if str(n).strip()})
    if not numbers:
        return pd.DataFrame(columns=COLUMNS)

    conn = _connect()
    try:
        chunks = []
        for i in range(0, len(numbers), _QUERY_CHUNK):
            chunk = numbers[i:i + _QUERY_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            chunks.append(pd.read_sql_query(
                f"SELECT * FROM {TABLE} WHERE {KEY} IN ({placeholders})", conn, params=chunk,
            ))
        return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=COLUMNS)
    finally:
        conn.close()
