#!/usr/bin/env python
"""Convert any .xlsx into a SQLite .db file, or load it into a Postgres
table. Standalone -- no dependency on the rest of this app (doesn't import
app.historical_db or its schema), just pandas + sqlite3/psycopg. Whatever
columns the sheet has become the table's columns.

Lives in data/ alongside historical.db and seed_historical.py -- the
historical-store scripts, and the store itself, in one place. This script
itself has no fixed location dependency (output defaults next to whatever
source file you point it at), so it works the same wherever it's run from.

Usage (SQLite, unchanged from before):
    python backend/data/create_xlsx_2_db.py <source.xlsx>
    python backend/data/create_xlsx_2_db.py <source.xlsx> --output out.db --table historical
    python backend/data/create_xlsx_2_db.py <source.xlsx> --sheet "Sheet2"

Usage (Postgres -- pass --database-url instead of/as well as --output):
    python backend/data/create_xlsx_2_db.py <source.xlsx> --database-url postgresql+psycopg://user:pw@host/db --table sometable
    python backend/data/create_xlsx_2_db.py <source.xlsx> --database-url ... --table historical --mode upsert --key ACCOUNT_NUMBER

Two Postgres modes:
  --mode replace (default)  Drops and recreates the table from the xlsx's
                             own columns. Simple, but if the table already
                             has EXTRA columns you don't know about (e.g.
                             this app's own historical table has
                             UPDATED_BY_JOB_ID/UPDATED_AT bolted on by
                             historical_db.py), replace mode wipes those
                             out -- the new table only has what's in the
                             xlsx. Fine for a scratch/throwaway table; NOT
                             what you want against a table another part of
                             this app depends on.
  --mode upsert              Requires --key (a column name, e.g.
                             ACCOUNT_NUMBER). Never drops the table --
                             CREATE TABLE IF NOT EXISTS, then
                             INSERT ... ON CONFLICT (key) DO UPDATE. Leaves
                             any extra columns (and their existing values on
                             rows this file doesn't touch) alone. This is
                             the safe one to point at a table something
                             else already relies on.

Defaults: output is <source>.db next to the source file (SQLite mode only),
table name is "data", first sheet is read. Uses the calamine engine
(already a project dependency) since it's dramatically faster than
openpyxl on large sheets.
"""
import argparse
import sqlite3
import time
from pathlib import Path

import pandas as pd


def _read_sheet(source: Path, sheet) -> pd.DataFrame:
    # dtype=str matters: without it, a numeric-looking column like
    # ACCOUNT_NUMBER gets inferred as int64 and silently loses leading
    # zeros ('0000000455' -> 455) -- a completely different key from what
    # every other seed path in this app uses (see store.read_table, which
    # forces dtype=str for exactly this reason). Caught this the hard way
    # testing --mode upsert against a real key column.
    df = pd.read_excel(source, sheet_name=sheet, engine="calamine", dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def xlsx_to_sqlite(source: Path, output: Path, table: str, sheet) -> int:
    df = _read_sheet(source, sheet)
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output)
    try:
        df.to_sql(table, conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()
    return len(df)


def _copy_insert(table, conn, keys, data_iter):
    """pandas to_sql 'method' callable -- bulk-loads via Postgres COPY
    instead of row-by-row INSERTs. Duplicated from historical_db.py rather
    than imported from it -- this script is meant to have zero dependency
    on this app's code, only on pandas/sqlalchemy/psycopg themselves."""
    dbapi_conn = conn.connection.driver_connection
    col_list = ", ".join(f'"{k}"' for k in keys)
    table_name = f'"{table.schema}"."{table.name}"' if table.schema else f'"{table.name}"'
    with dbapi_conn.cursor() as cur, cur.copy(f"COPY {table_name} ({col_list}) FROM STDIN") as copy:
        for row in data_iter:
            copy.write_row(row)


def _ensure_database_exists(url) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

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


def xlsx_to_postgres_replace(source: Path, database_url: str, table: str, sheet) -> int:
    from sqlalchemy import create_engine

    df = _read_sheet(source, sheet)
    _ensure_database_exists(database_url)
    engine = create_engine(database_url)
    df.to_sql(table, engine, if_exists="replace", index=False, method=_copy_insert)
    return len(df)


def xlsx_to_postgres_upsert(source: Path, database_url: str, table: str, sheet, key: str) -> int:
    from sqlalchemy import create_engine, text

    df = _read_sheet(source, sheet)
    if key not in df.columns:
        raise ValueError(f"--key {key!r} not found in this sheet's columns: {list(df.columns)}")
    df[key] = df[key].astype(str).str.strip()
    df = df.sort_values(by=[key]).drop_duplicates(subset=[key], keep="last")

    _ensure_database_exists(database_url)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table},
        ).fetchall()
        if existing:
            # Table already exists (e.g. this app's own `historical` table,
            # which has columns -- like UPDATED_BY_JOB_ID -- the xlsx knows
            # nothing about, and is missing others -- like CARD_NUMBER --
            # that the xlsx has). Only touch the intersection: never add
            # columns the table doesn't have, never drop ones it does.
            existing_cols = {row[0] for row in existing}
            columns = [c for c in df.columns if c in existing_cols]
            skipped = [c for c in df.columns if c not in existing_cols]
            if key not in columns:
                raise ValueError(f"--key {key!r} isn't a column on the existing table {table!r}")
            if skipped:
                print(f"Note: {table!r} already exists and has no columns named {skipped} -- those are in the xlsx but will be ignored, not added.")
        else:
            columns = list(df.columns)
            col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
            conn.execute(text(f'CREATE TABLE "{table}" ({col_defs})'))

        col_list = ", ".join(f'"{c}"' for c in columns)
        value_list = ", ".join(f":{c}" for c in columns)
        update_list = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in columns if c != key)
        # If key is the only shared column, there's nothing to update on a
        # conflict -- DO UPDATE SET with an empty list is invalid SQL.
        conflict_clause = f"DO UPDATE SET {update_list}" if update_list else "DO NOTHING"
        conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_{key} ON "{table}" ("{key}")'))
        conn.execute(
            text(f'INSERT INTO "{table}" ({col_list}) VALUES ({value_list}) '
                 f'ON CONFLICT ("{key}") {conflict_clause}'),
            df[columns].to_dict(orient="records"),
        )
    return len(df)


def _sheet_arg(value: str):
    """Sheet names are usually strings, but people also pass a 0-based
    index -- accept either."""
    try:
        return int(value)
    except ValueError:
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Source .xlsx file")
    parser.add_argument("--output", type=Path, default=None, help="SQLite output path (default: <source>.db). Ignored if --database-url is given.")
    parser.add_argument("--database-url", default=None, help="Postgres connection string -- switches this to Postgres mode instead of SQLite.")
    parser.add_argument("--table", default="data", help="Table name to write into (default: data)")
    parser.add_argument("--sheet", default=0, type=_sheet_arg, help="Sheet name or 0-based index (default: first sheet)")
    parser.add_argument("--mode", choices=["replace", "upsert"], default="replace", help="Postgres only: 'replace' drops/recreates the table (default); 'upsert' preserves it and any extra columns -- see this script's docstring.")
    parser.add_argument("--key", default=None, help="Postgres --mode upsert only: column to upsert on (e.g. ACCOUNT_NUMBER)")
    args = parser.parse_args()

    if not args.source.exists():
        parser.error(f"source file not found: {args.source}")
    if args.mode == "upsert" and not args.database_url:
        parser.error("--mode upsert only applies to Postgres -- pass --database-url")
    if args.mode == "upsert" and not args.key:
        parser.error("--mode upsert requires --key <column name>")

    print(f"Reading {args.source} ...")
    t0 = time.monotonic()

    if args.database_url:
        if args.mode == "upsert":
            row_count = xlsx_to_postgres_upsert(args.source, args.database_url, args.table, args.sheet, args.key)
        else:
            row_count = xlsx_to_postgres_replace(args.source, args.database_url, args.table, args.sheet)
        destination = f"{args.database_url} (table '{args.table}', mode={args.mode})"
    else:
        output = args.output or args.source.with_suffix(".db")
        row_count = xlsx_to_sqlite(args.source, output, args.table, args.sheet)
        destination = f"{output} (table '{args.table}')"

    elapsed = time.monotonic() - t0
    print(f"Wrote {row_count:,} rows into {destination} in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
