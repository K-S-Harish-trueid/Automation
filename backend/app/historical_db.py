"""PostgreSQL-backed cache for the historical-override reference data used
by the `replace` stage. (Migrated from SQLite -- see git history for the
old file-based version if you need to compare.)

Why this exists: parsing Historical_Dataset.xlsx via pandas/calamine costs
28-30s per job (profiled: 130MB, 797k rows) even though the actual match
logic is already fast and hash-indexed (~4s, see pipeline/stages/replace.py).
Importing that data into a real database once lets later jobs query it
directly instead of re-parsing the xlsx from scratch every time.

Connection comes from the DATABASE_URL env var; falls back to a local dev
default (postgres/postgres@localhost:5432/k2_historical) matching the
Postgres install used to build this out. The target database is created
automatically on first connect if it doesn't exist yet -- same "just
works" spirit the SQLite version had, minus needing a running server.

Seeded now from the existing Historical_Dataset.xlsx (see seed_from_file,
run once via data/seed_historical.py -- not called automatically by the
app). The completed-job screen's "Update historical data" button
(routes/stage.py's update_historical) also insert/replaces that job's own
rows here via upsert_rows, so the store grows over time. Keyed on
ACCOUNT_NUMBER (ON CONFLICT DO UPDATE) so re-adding an account updates it
in place instead of duplicating.

Every row (bulk-seeded or upserted) carries UPDATED_BY_JOB_ID and
UPDATED_AT -- an upsert overwrites the row in place, so without these
there'd be no way to tell which job last touched a given account or when.
UPDATED_BY_JOB_ID is NULL for rows that came from a bulk xlsx seed rather
than a specific job.
"""
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from .pipeline.stages.replace import REPLACE_MAPPING_COLS

DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:1234@localhost:5433/k2_historical"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

TABLE = "historical"
META_TABLE = "historical_meta"
KEY = "ACCOUNT_NUMBER"
COLUMNS = [KEY, *REPLACE_MAPPING_COLS]  # the actual reference data -- what a seed file/job df must contain
# Provenance columns, stamped on every write but never required from a caller's
# input df -- see module docstring for why these exist.
JOB_ID_COL = "UPDATED_BY_JOB_ID"
UPDATED_AT_COL = "UPDATED_AT"
META_COLUMNS = [JOB_ID_COL, UPDATED_AT_COL]
STORED_COLUMNS = [*COLUMNS, *META_COLUMNS]

# Query-size safety net for load_reference_df -- a single job could in
# theory carry an enormous account list. Postgres itself has no SQLite-style
# bound-parameter ceiling here (it's one array param via ANY(), not N
# placeholders), but chunking still caps how big any one query gets.
_QUERY_CHUNK = 5000

_engine: Engine | None = None


def _ensure_database_exists(url: str) -> None:
    """CREATE DATABASE can't run inside a transaction, so this connects to
    Postgres's always-present `postgres` maintenance db with autocommit,
    checks pg_database, and creates the target db if it's missing."""
    target = make_url(url)
    db_name = target.database
    admin_engine = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name}).fetchone()
            if exists is None:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin_engine.dispose()


def _engine_for(database_url: str) -> Engine:
    _ensure_database_exists(database_url)
    return create_engine(database_url, pool_pre_ping=True, future=True)


def _get_engine() -> Engine:
    """Cached module-level engine for the app's own live store. Seeding
    into a different target (see seed_from_file's database_url param) uses
    _engine_for directly instead, so it never touches this cache."""
    global _engine
    if _engine is None:
        _engine = _engine_for(DATABASE_URL)
    return _engine


def _clean_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df[KEY] = df[KEY].astype(str).str.strip()
    return df


def _copy_insert(table, conn, keys, data_iter):
    """pandas to_sql 'method' callable -- bulk-loads via Postgres COPY
    instead of row-by-row INSERTs, which is what keeps the ~800k-row
    historical seed in the tens of seconds instead of minutes over a
    network connection. See pandas' documented psql_insert_copy pattern;
    `driver_connection` is SQLAlchemy 2.0's hook for reaching the
    underlying psycopg (v3) connection to use its native copy() API."""
    dbapi_conn = conn.connection.driver_connection
    col_list = ", ".join(f'"{k}"' for k in keys)
    table_name = f'"{table.schema}"."{table.name}"' if table.schema else f'"{table.name}"'
    with dbapi_conn.cursor() as cur, cur.copy(f"COPY {table_name} ({col_list}) FROM STDIN") as copy:
        for row in data_iter:
            copy.write_row(row)


def _touch_seeded_at(conn) -> None:
    conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{META_TABLE}" (key TEXT PRIMARY KEY, value TEXT)'))
    conn.execute(
        text(f"""
            INSERT INTO "{META_TABLE}" (key, value) VALUES ('seeded_at', :ts)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """),
        {"ts": datetime.now().isoformat()},
    )


def _seed_from_df(df: pd.DataFrame, engine: Engine, source: str) -> int:
    """Shared by seed_from_file and seed_from_bytes -- replaces the whole
    historical table with an already-parsed DataFrame. One write path either
    way, so a seed done from a file path and one done from raw bytes can
    never disagree on validation/dedup/index behavior."""
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Reference file missing columns: {missing}")
    df = _clean_key(df[COLUMNS])
    df = df.sort_values(by=[KEY]).drop_duplicates(subset=[KEY], keep="last")
    # Bulk seed isn't any one job's doing -- stamp who/what ran it (not a
    # real job_id) instead of leaving this NULL, so it reads clearly in
    # pgAdmin next to rows that *do* have a real job id.
    df[JOB_ID_COL] = source
    df[UPDATED_AT_COL] = datetime.now()

    df.to_sql(TABLE, engine, if_exists="replace", index=False, method=_copy_insert)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_{KEY} ON "{TABLE}" ("{KEY}")'))
        _touch_seeded_at(conn)
    return len(df)


def seed_from_file(path, database_url: str | None = None) -> int:
    """One-time (or re-run-to-refresh) full import of a historical reference
    file (xlsx/csv) into the store -- replaces the table entirely. Not
    called by the running app; run manually when (re)seeding from a fresh
    export. database_url defaults to the app's own store (DATABASE_URL) but
    can be pointed elsewhere (see data/seed_historical.py) without touching
    the live db."""
    from . import store  # local import: avoid a cycle, store imports little from here

    path = Path(path)
    df = store.read_table(path.read_bytes(), path.name)
    engine = _engine_for(database_url) if database_url else _get_engine()
    return _seed_from_df(df, engine, source="CLI seed")


def seed_from_bytes(raw: bytes, filename: str, database_url: str | None = None) -> int:
    """Same import as seed_from_file, for a caller that already has the raw
    bytes in memory (routes/historical.py's web upload) -- skips writing
    them to a temp file just to immediately read them back off disk."""
    from . import store  # local import: avoid a cycle, store imports little from here

    df = store.read_table(raw, filename)
    engine = _engine_for(database_url) if database_url else _get_engine()
    return _seed_from_df(df, engine, source="Dashboard")


def upsert_rows(df: pd.DataFrame, job_id: str | None = None) -> int:
    """Insert-or-replace rows keyed on ACCOUNT_NUMBER -- backs the
    completed-job screen's 'Update historical data' button. Safe to call
    repeatedly with overlapping accounts; never duplicates a row, just
    refreshes it (and re-stamps job_id/UPDATED_AT to this call's)."""
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Cannot add to historical store, missing columns: {missing}")
    rows = _clean_key(df[COLUMNS]).drop_duplicates(subset=[KEY], keep="last")
    rows[JOB_ID_COL] = job_id
    rows[UPDATED_AT_COL] = datetime.now()

    engine = _get_engine()
    col_list = ", ".join(f'"{c}"' for c in STORED_COLUMNS)
    col_defs = ", ".join(f'"{c}" TIMESTAMP' if c == UPDATED_AT_COL else f'"{c}" TEXT' for c in STORED_COLUMNS)
    value_list = ", ".join(f":{c}" for c in STORED_COLUMNS)
    update_list = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in STORED_COLUMNS if c != KEY)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{TABLE}" ({col_defs})'))
        conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_{KEY} ON "{TABLE}" ("{KEY}")'))
        conn.execute(
            text(f'INSERT INTO "{TABLE}" ({col_list}) VALUES ({value_list}) '
                 f'ON CONFLICT ("{KEY}") DO UPDATE SET {update_list}'),
            rows[STORED_COLUMNS].to_dict(orient="records"),
        )
        _touch_seeded_at(conn)
    return len(rows)


def has_data() -> bool:
    """Also doubles as the "is Postgres even reachable" check -- any
    connection failure means "not seeded" rather than crashing whatever
    called this (matches the old SQLite version's DB_PATH.exists() being a
    cheap, always-safe pre-check)."""
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": TABLE},
            ).fetchone()
            if exists is None:
                return False
            return conn.execute(text(f'SELECT 1 FROM "{TABLE}" LIMIT 1')).fetchone() is not None
    except Exception:
        return False


def stats() -> dict:
    """Row count + when historical.db was last (re)seeded, for a status
    display on the web UI (see routes/historical.py) -- so an operator can
    see "seeded, 797,000 rows, updated 2026-08-12" or "not seeded" without
    SSHing in and poking the database."""
    if not has_data():
        return {"seeded": False, "row_count": 0, "seeded_at": None}
    engine = _get_engine()
    with engine.connect() as conn:
        row_count = conn.execute(text(f'SELECT COUNT(*) FROM "{TABLE}"')).scalar()
        try:
            seeded_at = conn.execute(text(f"SELECT value FROM \"{META_TABLE}\" WHERE key = 'seeded_at'")).scalar()
        except Exception:
            seeded_at = None  # pre-existing table with no meta row yet (e.g. from upsert_rows before this field existed)
    return {"seeded": True, "row_count": row_count, "seeded_at": seeded_at}


def load_reference_df(account_numbers) -> pd.DataFrame:
    """Fetch only the rows this job's ACCOUNT_NUMBERs need, chunked to keep
    any single query bounded. Empty/no-match input returns an empty (but
    correctly-columned) frame -- callers already handle a zero-match
    reference file the same way an xlsx upload would."""
    numbers = sorted({str(n).strip() for n in account_numbers if str(n).strip()})
    if not numbers:
        return pd.DataFrame(columns=COLUMNS)

    engine = _get_engine()
    stmt = text(f'SELECT * FROM "{TABLE}" WHERE "{KEY}" = ANY(:numbers)')
    with engine.connect() as conn:
        chunks = [
            pd.read_sql_query(stmt, conn, params={"numbers": numbers[i:i + _QUERY_CHUNK]})
            for i in range(0, len(numbers), _QUERY_CHUNK)
        ]
        return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=COLUMNS)
