#!/usr/bin/env python
"""One-time migration: copy the address denylist + per-province address pool
that used to be hardcoded in pipeline/stages/address_fix.py into Postgres
(address_denylist / address_pool tables -- see app/address_pools_db.py).

Run once, after which those two tables are the live source of truth --
editable directly in the database (pgAdmin or any SQL client) from then on,
no code change or redeploy needed for a new entry. Safe to re-run: it
replaces both tables' contents wholesale rather than appending, so running
it twice doesn't duplicate rows.

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

from sqlalchemy import text  # noqa: E402

from app.address_pools_db import DENYLIST_TABLE, POOL_TABLE, ensure_tables  # noqa: E402
from app.historical_db import DATABASE_URL, get_engine  # noqa: E402

# Ported verbatim from the old pipeline/stages/address_fix.py hardcoded
# constants -- this script exists to move this exact data into Postgres,
# not to change any of it.
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


def main():
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
    print(f"Seeded {len(DENYLIST)} denylist value(s) and {len(pool_rows)} address pool entry(ies) into:\n  {DATABASE_URL}")
    print("Edit address_denylist / address_pool directly in Postgres from now on -- takes effect within 60s, no restart needed.")


if __name__ == "__main__":
    main()
