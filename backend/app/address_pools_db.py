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
case).

Seedable from an xlsx export the same way historical_db.py's historical
table is (see seed_from_bytes/seed_from_file, routes/seed.py's /seed page,
and export_to_xlsx for producing that export from a live table) -- two
sheets, one per table, named after the tables themselves so the round trip
(export -> edit -> re-upload) can't drift out of sync with what's actually
in Postgres."""
import io
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .historical_db import get_engine

logger = logging.getLogger(__name__)

DENYLIST_TABLE = "address_denylist"
POOL_TABLE = "address_pool"
META_TABLE = "address_pools_meta"

CACHE_TTL_SECONDS = 60

_cache: dict = {"denylist": None, "pool": None, "loaded_at": 0.0}


def ensure_tables(conn) -> None:
    conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{DENYLIST_TABLE}" (value TEXT NOT NULL)'))
    conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{POOL_TABLE}" (province TEXT NOT NULL, address TEXT NOT NULL)'))


# Ported verbatim from the old pipeline/stages/address_fix.py hardcoded
# constants -- seed_defaults() exists to (re)load this exact data into
# Postgres, not to change any of it. Lives here (not data/migrate_address_pools.py)
# so the schema and the default data can never disagree, same reasoning as
# historical_db.py keeping seed_from_file next to DATABASE_URL/the schema.
DENYLIST = [
    "-", "0", "00", "000", "0000", "00000", "00000000000000000000",
    "198085468215", "00000000000000", "000000000000000", "198606284463",
    "198638377984", "1997199797", "07511943292", "07705597095",
    "07711195891", "07803571894", "07808357798", "07826951542",
    "07827336943", "11", "+9647508582831", "009647816045945",
    "009647830673883", "11519", "1213", "123", "june", "12", "15",
    "198qgghjkk0", "197684137576", "2462305", "27 02 2034", "3453369",
    "5026655", "52292", "5332", "22", "6", "77", "88", "89", "90",
    "5213720403889723", "6928", "377998", "000000000000",
    "0000000000000000", "0000000000", "0000000000000", "a", "000000",
]

BAGHDAD_ADDRESS_POOL = [
    "ABI GHARIB AL-NASR WA AL-SALAM",
    "ADHAMIYA CHARA OMAR BN ABDULAZIZ",
    "AL AMRIYA KRB NADI ALKHTOT",
    "AL-AMARIYA HAY AL-FIRDAWS",
    "BAGHDAD - AL-MAAMOUN",
    "BAGHDAD AL-BALADIYAT",
    "MADINAT AL-SADR",
    "حي الخضراء",
    "الكاظمية",
    "بغداد الجديدة",
    "شارع فلسطين",
    "الدوره",
    "الحريه",
    "الصليخ",
    "بغداد حي العامل",
    "جسر ديالى",
    "المامون",
    "بغداد السيدية",
    "الغزاليه",
    "بغداد الشعله",
]

PROVINCE_ADDRESS_MAP = {
    "Baghdad": BAGHDAD_ADDRESS_POOL,
    "Al Anbar": ["الانبار القائم", "الرمادي", "الفلوجة"],
    "Al Basrah": ["قضاء المدينة", "خور الزبير"],
    "Al Munthana": ["سماوة حيل عسكري الجربوعية الثنية", "قضاء الخضر", "حي العسكري"],
    "Al Najaf": ["شارع المدينه", "حي الانصار", "كوفه حي ميسان"],
    "Al Quadisiya": ["قضاء الشاميه", "الديوانية القادسية", "حي الجامعه"],
    "Al Sulaymaniah": ["كلار شهيدان"],
    "Al Ta'amim": ["كركوك رحيم اوه", "كركوك حي الواسطي"],
    "Arbil": ["اربيل خبات", "اربيل قضاء خبات"],
    "Babil": ["قضاء القاسم", "القريه العصريه مكتب"],
    "Dahouk": ["عقرة مجمع ئازادي"],
    "Deyala": ["خان بني سعد", "ديالى بلدروز", "بعقوبة التحرير"],
    "Karbala": ["حي العسكري", "كربلاء حي الغدير", "كربلاء حي العامل"],
    "Kirkuk": ["ازادي الشورجة", "ازادى جديد", "ازدی ، جامع ازادی"],
    "Maysan": ["المجر الكبير", "قضاء الكحلاء", "حي الحسين القديم"],
    "Mousl (Nainawa)": ["قضاء تلعفر", "حي الانتصار", "تلعفر حي النور", "موصل نينوى", "موصل حي البكر"],
    "Salah Al Deen": ["صلاح الدين", "صلاحدين طوز خورماتوو جموري", "صلاح الدين قضاء بلد", "صلاح الدين بلد", "سامراء حي المثنى"],
    "Thi Qar": ["قضاء الفجر", "ذي قار قلعة سكر", "قضاء الشطره"],
    "Wasit": ["واسط قضاء الحي", "كوت حي الحكيم"],
}


def seed_defaults() -> tuple[int, int]:
    """(Re)load the built-in denylist + per-province pool into Postgres,
    replacing both tables wholesale (safe to call repeatedly -- never
    duplicates rows). Returns (denylist_count, pool_count).

    Used by data/migrate_address_pools.py (CLI, one-time on a fresh
    install) and the /test page's "Seed default address pools" button (for
    fixing an empty table without needing shell access to the server)."""
    engine = get_engine()
    with engine.begin() as conn:
        ensure_tables(conn)
        conn.execute(text(f'DELETE FROM "{DENYLIST_TABLE}"'))
        conn.execute(text(f'DELETE FROM "{POOL_TABLE}"'))
        conn.execute(
            text(f'INSERT INTO "{DENYLIST_TABLE}" (value) VALUES (:value)'),
            [{"value": v} for v in DENYLIST],
        )
        pool_rows = [
            {"province": province, "address": address}
            for province, addresses in PROVINCE_ADDRESS_MAP.items()
            for address in addresses
        ]
        conn.execute(
            text(f'INSERT INTO "{POOL_TABLE}" (province, address) VALUES (:province, :address)'),
            pool_rows,
        )
    _cache["loaded_at"] = 0.0  # force a fresh reload on next read instead of waiting out CACHE_TTL_SECONDS
    return len(DENYLIST), len(pool_rows)


def _touch_seeded_at(conn) -> None:
    conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{META_TABLE}" (key TEXT PRIMARY KEY, value TEXT)'))
    conn.execute(
        text(f"""
            INSERT INTO "{META_TABLE}" (key, value) VALUES ('seeded_at', :ts)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """),
        {"ts": datetime.now().isoformat()},
    )


def export_to_xlsx(engine: Engine | None = None) -> bytes:
    """Current address_denylist/address_pool table contents as an xlsx --
    one sheet per table, sheet names matching the table names exactly. This
    is the file seed_from_bytes below expects back, so export -> edit in
    Excel -> re-upload round-trips cleanly.

    The denylist column is full of numeric-looking junk values ("0", "00",
    "000", "00000", ...) that are distinct strings, not numbers -- without
    forcing the cell format to Text, Excel silently treats them as numbers
    and displays (and, if the file is then edited and saved, actually
    stores) every one of them as a bare 0, collapsing what were 15+ distinct
    denylist entries into one. Applies to the address column too on the
    off chance a real address is all-digits."""
    engine = engine or get_engine()
    with engine.connect() as conn:
        ensure_tables(conn)
        denylist_df = pd.read_sql_query(f'SELECT value FROM "{DENYLIST_TABLE}" ORDER BY value', conn)
        pool_df = pd.read_sql_query(f'SELECT province, address FROM "{POOL_TABLE}" ORDER BY province, address', conn)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        denylist_df.to_excel(writer, sheet_name=DENYLIST_TABLE, index=False)
        pool_df.to_excel(writer, sheet_name=POOL_TABLE, index=False)
        _force_text_format(writer.sheets[DENYLIST_TABLE], ["A"])
        _force_text_format(writer.sheets[POOL_TABLE], ["A", "B"])
    return buf.getvalue()


def _force_text_format(worksheet, columns: list[str]) -> None:
    """Set an openpyxl column's number_format to Text ('@') for every data
    cell (row 1 is the header) -- see export_to_xlsx's docstring for why
    this matters. Must be set on the cells themselves, not just the column;
    Excel doesn't retroactively reformat existing cell values otherwise."""
    for col in columns:
        for row in range(2, worksheet.max_row + 1):
            worksheet[f"{col}{row}"].number_format = "@"


def seed_from_bytes(raw: bytes, filename: str) -> tuple[int, int]:
    """(Re)load address_denylist/address_pool from an xlsx with two sheets
    named after the tables (see export_to_xlsx) -- wholesale replace, same
    as seed_defaults, just from uploaded data instead of the hardcoded
    fallback. Returns (denylist_count, pool_count)."""
    try:
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, dtype=str, engine="openpyxl")
    except Exception as e:
        raise ValueError(f"Could not read Excel file {filename}: {e}") from e

    missing = [s for s in (DENYLIST_TABLE, POOL_TABLE) if s not in sheets]
    if missing:
        raise ValueError(f"Missing sheet(s): {missing} -- expected one sheet per table, named exactly "
                          f"'{DENYLIST_TABLE}' and '{POOL_TABLE}' (same as export_to_xlsx produces).")

    denylist_df = sheets[DENYLIST_TABLE].fillna("")
    pool_df = sheets[POOL_TABLE].fillna("")
    if "value" not in denylist_df.columns:
        raise ValueError(f"'{DENYLIST_TABLE}' sheet must have a 'value' column")
    if not {"province", "address"}.issubset(pool_df.columns):
        raise ValueError(f"'{POOL_TABLE}' sheet must have 'province' and 'address' columns")

    denylist_rows = [{"value": v.strip()} for v in denylist_df["value"] if v.strip()]
    pool_rows = [
        {"province": row.province.strip(), "address": row.address.strip()}
        for row in pool_df.itertuples()
        if row.province.strip() and row.address.strip()
    ]

    engine = get_engine()
    with engine.begin() as conn:
        ensure_tables(conn)
        conn.execute(text(f'DELETE FROM "{DENYLIST_TABLE}"'))
        conn.execute(text(f'DELETE FROM "{POOL_TABLE}"'))
        if denylist_rows:
            conn.execute(text(f'INSERT INTO "{DENYLIST_TABLE}" (value) VALUES (:value)'), denylist_rows)
        if pool_rows:
            conn.execute(text(f'INSERT INTO "{POOL_TABLE}" (province, address) VALUES (:province, :address)'), pool_rows)
        _touch_seeded_at(conn)
    _cache["loaded_at"] = 0.0
    return len(denylist_rows), len(pool_rows)


def stats() -> dict:
    """Counts + last-seeded timestamp, for the /seed page's status line --
    same shape/spirit as historical_db.stats()."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            ensure_tables(conn)
            denylist_count = conn.execute(text(f'SELECT COUNT(*) FROM "{DENYLIST_TABLE}"')).scalar()
            pool_count = conn.execute(text(f'SELECT COUNT(*) FROM "{POOL_TABLE}"')).scalar()
            try:
                seeded_at = conn.execute(text(f"SELECT value FROM \"{META_TABLE}\" WHERE key = 'seeded_at'")).scalar()
            except Exception:
                seeded_at = None  # meta table doesn't exist yet -- never seeded via seed_defaults/seed_from_bytes
    except Exception:
        logger.exception("address_pools_db.stats() failed -- Postgres unreachable?")
        return {"seeded": False, "denylist_count": 0, "pool_count": 0, "seeded_at": None}
    return {
        "seeded": bool(denylist_count or pool_count),
        "denylist_count": denylist_count,
        "pool_count": pool_count,
        "seeded_at": seeded_at,
    }


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
