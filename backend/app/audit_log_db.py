"""PostgreSQL mirror of the per-job audit.jsonl trail (store.py), so audit
events are queryable across every job in one place instead of only within
one job's own file. This is a mirror, not a replacement -- audit.jsonl
stays exactly as-is (still the source the app itself reads from: the
Done-screen audit download, the live audit_event_count on every progress
poll, etc.), because that file is read on a hot polling path (every 900ms
per active job, see frontend/app.js) and this table is not indexed/sized
for that -- a single job can produce hundreds of thousands of events, far
more than generated_records.py's rare fabricated-value rows. Keeping the
local file as the one thing the app depends on means this table (and
Postgres being reachable at all) is never on the critical path for a job
to actually run.

Append-only, same reasoning as generated_records_db.py: an audit trail
that could be edited or overwritten isn't an audit trail.

Every write here is best-effort -- see record_events -- a Postgres hiccup
must never fail a job or lose the local audit.jsonl write, it just means
this mirror falls behind until Postgres is reachable again."""
import logging

from sqlalchemy import text

from .historical_db import get_engine

logger = logging.getLogger(__name__)

TABLE = "audit_log"
COLUMNS = [
    "job_id", "account_number", "field", "old_value", "new_value",
    "stage", "operator", "reason", "source_file", "label", "event_time",
]


def _ensure_table(conn) -> None:
    conn.execute(text(f'''
        CREATE TABLE IF NOT EXISTS "{TABLE}" (
            job_id TEXT NOT NULL,
            account_number TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT NOT NULL,
            new_value TEXT NOT NULL,
            stage TEXT NOT NULL,
            operator TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_file TEXT NOT NULL,
            label TEXT NOT NULL,
            event_time TIMESTAMP NOT NULL
        )
    '''))
    conn.execute(text(f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_job ON "{TABLE}" (job_id)'))
    conn.execute(text(f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_account ON "{TABLE}" (account_number)'))


def record_events(job_id: str, audit_events: list[dict]) -> int:
    """Insert one row per event already written to this job's audit.jsonl
    (see helpers._append_audit_events, whose return value is exactly what's
    passed in here) -- same source data, so this table can never disagree
    with the local file about what happened. row_key is intentionally
    dropped: it's only a position inside one job's own in-memory dataframe,
    meaningless once you're querying across jobs where account_number +
    job_id already identify the row.

    Writes via Postgres's native COPY protocol (same technique
    historical_db.py's _copy_insert uses for its 797k-row historical seed),
    not a parameterized INSERT -- COPY skips per-row/per-batch SQL parsing
    entirely, measured ~3.3x faster (400k rows: 2.8s vs 9.3s) for the kind
    of large single-stage batch a CMS card merge produces. Same table,
    same columns, same data either way -- this only changes how the rows
    get there.

    Best-effort: any failure (Postgres unreachable, table locked, whatever)
    is logged and swallowed, never raised -- this mirror is a convenience,
    not something a job's own processing may ever depend on or be slowed
    down waiting to retry."""
    if not audit_events:
        return 0
    rows = [
        (
            job_id, e["account_number"], e["field"], e["old_value"], e["new_value"],
            e["stage"], e["operator"], e["reason"], e["source_file"], e["label"], e["time"],
        )
        for e in audit_events
    ]
    try:
        engine = get_engine()
        with engine.begin() as conn:
            _ensure_table(conn)
            dbapi_conn = conn.connection.driver_connection
            col_list = ", ".join(f'"{c}"' for c in COLUMNS)
            with dbapi_conn.cursor() as cur, cur.copy(f'COPY "{TABLE}" ({col_list}) FROM STDIN') as copy:
                for row in rows:
                    copy.write_row(row)
        return len(rows)
    except Exception:
        logger.exception("audit_log_db.record_events failed for job %s -- local audit.jsonl is unaffected", job_id)
        return 0
