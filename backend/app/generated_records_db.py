"""PostgreSQL log of every value the pipeline fabricated on an account's
behalf -- default Civil IDs (stage: default_id) and auto-filled addresses
(stage: address_fix). Append-only, unlike historical_db.py's upsert store:
this is provenance ("we made this up, here, at this time") not "current
truth" -- a later real correction must never erase the record that a value
was once generated, so nothing here is ever updated or deleted, only added
to. One row per (account, field) generation event, tagged with the job
that generated it, so both "every account that currently has any
fabricated data" (GROUP BY account_number) and "everything one job
generated" (WHERE job_id = ...) are plain queries over the same table.

Shares historical_db's Postgres connection (get_engine) -- same database,
different table, no reason to open a second connection pool to the same
server."""
from datetime import datetime

import pandas as pd
from sqlalchemy import text

from .historical_db import get_engine

TABLE = "generated_records"
COLUMNS = ["account_number", "field", "value", "stage", "reason", "job_id", "generated_at"]


def _ensure_table(conn) -> None:
    conn.execute(text(f'''
        CREATE TABLE IF NOT EXISTS "{TABLE}" (
            id SERIAL PRIMARY KEY,
            account_number TEXT NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            stage TEXT NOT NULL,
            reason TEXT NOT NULL,
            job_id TEXT NOT NULL,
            generated_at TIMESTAMP NOT NULL
        )
    '''))
    conn.execute(text(f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_account ON "{TABLE}" (account_number)'))
    conn.execute(text(f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_job ON "{TABLE}" (job_id)'))


def record_generated(job_id: str, audit_events: list[dict]) -> int:
    """Insert one row per audit event already produced for a generation
    stage (see helpers._append_audit_events, whose return value is exactly
    what's passed in here) -- reuses its account_number/field/new_value/
    reason instead of recomputing anything, so this table can never
    disagree with the job's own audit.jsonl about what happened. A no-op
    (never even opens a connection) when there's nothing to log, so a job
    with zero generated IDs/addresses doesn't touch Postgres at all."""
    if not audit_events:
        return 0
    rows = [
        {
            "account_number": e["account_number"],
            "field": e["field"],
            "value": e["new_value"],
            "stage": e["stage"],
            "reason": e["reason"],
            "job_id": job_id,
            "generated_at": datetime.now(),
        }
        for e in audit_events
    ]
    engine = get_engine()
    with engine.begin() as conn:
        _ensure_table(conn)
        conn.execute(
            text(f'''
                INSERT INTO "{TABLE}" (account_number, field, value, stage, reason, job_id, generated_at)
                VALUES (:account_number, :field, :value, :stage, :reason, :job_id, :generated_at)
            '''),
            rows,
        )
    return len(rows)


def fetch_for_job(job_id: str) -> pd.DataFrame:
    """Every generated_records row for one job, in COLUMNS order -- backs
    the K2_Generated_Records.xlsx copy frozen alongside final.xlsx (see
    routes/jobs.py's _write_and_get_final_path). Returns an empty (but
    correctly-columned) frame rather than raising if the table doesn't
    exist yet or this job generated nothing -- "nothing was fabricated for
    this job" is a normal, common outcome, not an error."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": TABLE},
            ).fetchone()
            if exists is None:
                return pd.DataFrame(columns=COLUMNS)
            return pd.read_sql_query(
                text(f'SELECT {", ".join(COLUMNS)} FROM "{TABLE}" WHERE job_id = :job_id ORDER BY id'),
                conn, params={"job_id": job_id},
            )
    except Exception:
        return pd.DataFrame(columns=COLUMNS)
