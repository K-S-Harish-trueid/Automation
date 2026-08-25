"""Postgres-backed address denylist + per-province replacement pool for the
`address_fix` stage -- editable directly in the database (pgAdmin or any SQL
client), so adding or changing an address never needs a code change or a
redeploy. See pipeline/stages/address_fix.py for how these get used.

Two tables, deliberately separate (different shapes, not the same data):
  address_denylist(value)          -- exact-match junk address strings
  address_pool(province, address)  -- real replacement addresses per
                                        province; "Baghdad" rows also serve
                                        as the general fallback pool
                                        (outside-the-country / unmapped-city
                                        cases), same as the old hardcoded
                                        BAGHDAD_ADDRESS_POOL was reused for
                                        both -- see data/migrate_address_pools.py
                                        for the one-time migration of what
                                        used to be hardcoded here.

Cached in memory with a short TTL, not queried fresh on every call --
address_fix.py's mask_address_invalid gets called ~5 times within a single
stage_address_fix() run, and querying Postgres 5 times for the same data
every job would be wasteful. An edit made in pgAdmin still takes effect
within CACHE_TTL_SECONDS, no restart needed.

Same graceful-degradation philosophy as historical_db.py: if Postgres is
unreachable or the tables are empty, this returns empty results rather than
raising -- address_fix has never failed a job and shouldn't start now just
because a lookup is unavailable. A row that can't be fixed is already an
accepted, existing outcome (see stage_address_fix's "unmapped province"
case)."""
import logging
import time

from sqlalchemy import text

from .historical_db import get_engine

logger = logging.getLogger(__name__)

DENYLIST_TABLE = "address_denylist"
POOL_TABLE = "address_pool"

CACHE_TTL_SECONDS = 60

_cache: dict = {"denylist": None, "pool": None, "loaded_at": 0.0}


def ensure_tables(conn) -> None:
    conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{DENYLIST_TABLE}" (value TEXT NOT NULL)'))
    conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{POOL_TABLE}" (province TEXT NOT NULL, address TEXT NOT NULL)'))


def _refresh_cache() -> None:
    try:
        engine = get_engine()
        with engine.begin() as conn:
            ensure_tables(conn)
            denylist_rows = conn.execute(text(f'SELECT value FROM "{DENYLIST_TABLE}"')).fetchall()
            pool_rows = conn.execute(text(f'SELECT province, address FROM "{POOL_TABLE}"')).fetchall()
        denylist = {row[0].strip().lower() for row in denylist_rows if row[0] and row[0].strip()}
        pool_map: dict[str, list[str]] = {}
        for province, address in pool_rows:
            if not province or not address:
                continue
            pool_map.setdefault(province.strip(), []).append(address.strip())
        _cache["denylist"] = denylist
        _cache["pool"] = pool_map
        _cache["loaded_at"] = time.time()
    except Exception:
        # Postgres unreachable or the query failed -- log it (visible in the
        # server log for troubleshooting) but never raise. If something was
        # already cached, keep using it (better a few-seconds-stale list than
        # none). If nothing was ever successfully loaded, fall back to empty
        # -- see module docstring, address_fix must never fail a job over
        # this. Either way, stamp loaded_at so a persistently-down Postgres
        # doesn't get hammered every single call.
        logger.exception("address_pools_db failed to refresh -- using %s", "last cached copy" if _cache["denylist"] is not None else "empty fallback")
        if _cache["denylist"] is None:
            _cache["denylist"] = set()
        if _cache["pool"] is None:
            _cache["pool"] = {}
        _cache["loaded_at"] = time.time()


def _ensure_fresh() -> None:
    if _cache["denylist"] is None or time.time() - _cache["loaded_at"] > CACHE_TTL_SECONDS:
        _refresh_cache()


def load_denylist() -> set[str]:
    """Lowercased, trimmed junk-value strings -- compare against an
    already-lowercased/trimmed ACCOUNT_ADDRESS the same way the old
    hardcoded INVALID_ADDRESS_VALUES set was used."""
    _ensure_fresh()
    return _cache["denylist"]


def load_pool_map() -> dict[str, list[str]]:
    """{province: [address, ...]} -- same shape as the old hardcoded
    PROVINCE_ADDRESS_MAP."""
    _ensure_fresh()
    return _cache["pool"]


def load_baghdad_pool() -> list[str]:
    """The general fallback pool (outside-the-country / unmapped-city
    cases) -- same role as the old hardcoded BAGHDAD_ADDRESS_POOL, just the
    "Baghdad" province's own rows reused, not separate data."""
    return load_pool_map().get("Baghdad", [])
