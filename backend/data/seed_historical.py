#!/usr/bin/env python
"""(Re)build the historical SQL store from a Historical_Dataset.xlsx export.

Thin CLI wrapper around app.historical_db.seed_from_file -- see that file's
docstring for why this exists (xlsx parsing is slow; SQLite lookups aren't).
Schema (columns, key, table name) lives in app.historical_db, not here, so
this script and the app can never disagree on what "the schema" is.

Lives in data/ (next to the historical.db it builds) rather than loose in
backend/ -- everything about the historical store, script and database
alike, in one place.

Usage:
    python backend/data/seed_historical.py
    python backend/data/seed_historical.py --source "D:\\path\\to\\export.xlsx"
    python backend/data/seed_historical.py --source in.xlsx --output D:\\out\\historical.db

Defaults to this repo's real fixture and the app's own live db path, so a
bare `python backend/data/seed_historical.py` reproduces backend/data/historical.db.
"""
import argparse
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/data/ -> backend/
sys.path.insert(0, str(BACKEND_DIR))

from app.historical_db import DB_PATH, seed_from_file  # noqa: E402

DEFAULT_SOURCE = BACKEND_DIR.parent / "dummy_data" / "Input Data" / "Stage 1" / "Historical_Dataset.xlsx"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=DEFAULT_SOURCE, type=Path, help=f"Historical_Dataset xlsx/csv to import (default: {DEFAULT_SOURCE})")
    parser.add_argument("--output", default=DB_PATH, type=Path, help=f"Where to write the SQLite db (default: {DB_PATH}, the app's live store)")
    args = parser.parse_args()

    if not args.source.exists():
        parser.error(f"source file not found: {args.source}")

    print(f"Reading {args.source} ...")
    t0 = time.monotonic()
    row_count = seed_from_file(args.source, db_path=args.output)
    elapsed = time.monotonic() - t0

    print(f"Seeded {row_count:,} rows into {args.output} ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
