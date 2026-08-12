#!/usr/bin/env python
"""Convert any .xlsx into a SQLite .db file. Standalone -- no dependency on
the rest of this app (doesn't touch app.historical_db or its schema), just
pandas + sqlite3. Whatever columns the sheet has become the table's columns.

Lives in data/ alongside historical.db and seed_historical.py -- the
historical-store scripts, and the store itself, in one place. This script
itself has no fixed location dependency (output defaults next to whatever
source file you point it at), so it works the same wherever it's run from.

Usage:
    python backend/data/create_xlsx_2_db.py <source.xlsx>
    python backend/data/create_xlsx_2_db.py <source.xlsx> --output out.db --table historical
    python backend/data/create_xlsx_2_db.py <source.xlsx> --sheet "Sheet2"

Defaults: output is <source>.db next to the source file, table name is
"data", first sheet is read. Uses the calamine engine (already a project
dependency) since it's dramatically faster than openpyxl on large sheets.
"""
import argparse
import sqlite3
import time
from pathlib import Path

import pandas as pd


def xlsx_to_db(source: Path, output: Path, table: str, sheet) -> int:
    df = pd.read_excel(source, sheet_name=sheet, engine="calamine")
    df.columns = [str(c).strip() for c in df.columns]

    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output)
    try:
        df.to_sql(table, conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()
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
    parser.add_argument("--output", type=Path, default=None, help="Output .db path (default: <source>.db)")
    parser.add_argument("--table", default="data", help="Table name to write into (default: data)")
    parser.add_argument("--sheet", default=0, type=_sheet_arg, help="Sheet name or 0-based index (default: first sheet)")
    args = parser.parse_args()

    if not args.source.exists():
        parser.error(f"source file not found: {args.source}")

    output = args.output or args.source.with_suffix(".db")

    print(f"Reading {args.source} ...")
    t0 = time.monotonic()
    row_count = xlsx_to_db(args.source, output, args.table, args.sheet)
    elapsed = time.monotonic() - t0
    print(f"Wrote {row_count:,} rows into {output} (table '{args.table}') in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
