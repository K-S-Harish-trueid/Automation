#!/usr/bin/env python
"""(Re)build the historical Postgres store from a Historical_Dataset.xlsx export.

Thin CLI wrapper around app.historical_db.seed_from_file -- see that file's
docstring for why this exists (xlsx parsing is slow; indexed SQL lookups
aren't). Schema (columns, key, table name) lives in app.historical_db, not
here, so this script and the app can never disagree on what "the schema" is.

Lives in data/ (next to where historical.db used to live, back when this
was SQLite) rather than loose in backend/ -- everything about the
historical store, script included, in one place.

Friendly for someone who just cloned the repo and doesn't know the flags:
if you don't pass --source (and the default fixture isn't sitting where
this repo expects it), it just asks for the file interactively instead of
failing with a wall of argparse text. Double-click seed_historical.bat for
an even more hands-off version of the same thing.

Needs a reachable Postgres server -- set DATABASE_URL (see
app.historical_db.DEFAULT_DATABASE_URL for the local-dev default) before
running this if you're not using that default.

Usage:
    python backend/data/seed_historical.py
    python backend/data/seed_historical.py --source "D:\\path\\to\\export.xlsx"
    python backend/data/seed_historical.py --source in.xlsx --database-url postgresql+psycopg://user:pw@host/db

Defaults to this repo's real fixture and the app's own live database (via
DATABASE_URL / its built-in default), so a bare
`python backend/data/seed_historical.py` reproduces the app's live store.
"""
import argparse
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/data/ -> backend/
sys.path.insert(0, str(BACKEND_DIR))

from app.historical_db import DATABASE_URL, seed_from_file  # noqa: E402

DEFAULT_SOURCE = BACKEND_DIR.parent / "dummy_data" / "Stage 1" / "Historical_Dataset.xlsx"


def _clean_path(raw: str) -> Path:
    """Handles a path pasted/dragged into the terminal, which usually comes
    wrapped in quotes (drag-and-drop) or with stray whitespace."""
    return Path(raw.strip().strip('"').strip("'"))


def _ask_for_source() -> Path:
    print(f"\nCouldn't find the historical data file at:\n  {DEFAULT_SOURCE}\n")
    while True:
        raw = input("Drag & drop (or paste the path to) Historical_Dataset.xlsx here, then press Enter: ").strip()
        if not raw:
            print("(nothing entered -- try again, or Ctrl+C to give up)")
            continue
        path = _clean_path(raw)
        if not path.exists():
            print(f"Can't find that file: {path}\nCheck the path and try again.")
            continue
        return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=None, help=f"Historical_Dataset xlsx/csv to import (default: {DEFAULT_SOURCE})")
    parser.add_argument("--database-url", default=DATABASE_URL, help=f"Postgres connection string to seed (default: DATABASE_URL env, or {DATABASE_URL})")
    args = parser.parse_args()

    source = args.source or DEFAULT_SOURCE
    if not source.exists():
        if args.source is not None:
            # explicit --source that doesn't exist is a real mistake -- fail loud, don't guess
            parser.error(f"source file not found: {source}")
        try:
            source = _ask_for_source()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled -- no database was created.")
            sys.exit(1)

    print(f"\nReading {source} ...")
    print("(this can take a minute for the full ~800k row export -- it's not stuck)")
    t0 = time.monotonic()
    try:
        row_count = seed_from_file(source, database_url=args.database_url)
    except ValueError as e:
        print(f"\nThat file doesn't look right: {e}")
        print("Make sure it's the real Historical_Dataset.xlsx export, not a partial/renamed copy.")
        sys.exit(1)
    except Exception as e:
        print(f"\nCouldn't reach Postgres at {args.database_url}: {e}")
        print("Make sure the Postgres service is running and DATABASE_URL (or --database-url) points at it.")
        sys.exit(1)
    elapsed = time.monotonic() - t0

    print(f"\nDone! Seeded {row_count:,} rows into:\n  {args.database_url}\n({elapsed:.1f}s)")
    print("Restart the backend (python run.py) and the 'not seeded' error should be gone.")


if __name__ == "__main__":
    main()
