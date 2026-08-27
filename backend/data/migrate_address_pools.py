#!/usr/bin/env python
"""One-time migration: copy the address denylist + per-province address pool
that used to be hardcoded in pipeline/stages/address_fix.py into Postgres
(address_denylist / address_pool tables -- see app/address_pools_db.py).

Thin CLI wrapper around app.address_pools_db.seed_defaults -- see that
function's docstring for why this exists (data + schema in one place, so
this script and the app can never disagree). Same idea as
data/seed_historical.py wrapping app.historical_db.seed_from_file.

Run once, after which those two tables are the live source of truth --
editable directly in the database (pgAdmin or any SQL client) from then on,
no code change or redeploy needed for a new entry. Safe to re-run: it
replaces both tables' contents wholesale rather than appending, so running
it twice doesn't duplicate rows. Also reachable from the /test page's "Seed
default address pools" button, for a fresh/empty install without shell
access to the server.

Needs a reachable Postgres server -- set DATABASE_URL (see
app.historical_db.DEFAULT_DATABASE_URL for the local-dev default) before
running this if you're not using that default.

Usage:
    python backend/data/migrate_address_pools.py
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/data/ -> backend/
sys.path.insert(0, str(BACKEND_DIR))

from app.address_pools_db import seed_defaults  # noqa: E402
from app.historical_db import DATABASE_URL  # noqa: E402


def main():
    denylist_count, pool_count = seed_defaults()
    print(f"Seeded {denylist_count} denylist value(s) and {pool_count} address pool entry(ies) into:\n  {DATABASE_URL}")
    print("Edit address_denylist / address_pool directly in Postgres from now on -- takes effect within 60s, no restart needed.")


if __name__ == "__main__":
    main()
