import pandas as pd

from ..toolbox import _s, balanced_assign, series_available, series_has_letter

# Exact-match denylist of address values observed in real data that are junk
# even though some of them contain letters (so the generic "no letters"
# check below can't catch them on its own) -- e.g. a month name someone
# typed by mistake, keyboard-mash garbage, or a single letter. Compared
# case-insensitively against the trimmed ACCOUNT_ADDRESS value. Ported
# verbatim from the legacy address-fix script (aman/pah3.py).
INVALID_ADDRESS_VALUES = {
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
}

# Fallback pool used to synthesize a Baghdad-area address for a row whose
# ACCOUNT_ADDRESS is invalid and whose province is unknown/unmapped/outside
# the country. Ported verbatim from the legacy address_fix script
# (aman/pah3.py) so generated data matches what production already used.
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

# Per-province address pools used to synthesize a replacement address when
# ACCOUNT_ADDRESS is invalid but ADDRESS_PROVINCE is known and mapped here.
# Ported verbatim from aman/pah3.py.
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


def mask_address_invalid(df: pd.DataFrame) -> pd.Series:
    address = _s(df, "ACCOUNT_ADDRESS")
    missing = ~series_available(address)
    # No letters at all -- e.g. a phone number typed into the address field,
    # or leftover punctuation like "-": no real street/area name is ever
    # pure digits/symbols. Also covers "missing" (an empty string has none).
    no_letters = ~series_has_letter(address)
    # Exact matches against known-junk values that DO contain letters (e.g.
    # "JUNE", "A") and so slip past the no_letters check above.
    known_bad = address.str.lower().isin(INVALID_ADDRESS_VALUES)
    return missing | no_letters | known_bad


def stage_address_fix(df: pd.DataFrame, **_):
    """Auto-fills invalid ACCOUNT_ADDRESS values from a Baghdad/province
    address pool -- ported from the legacy script (aman/pah3.py), which used
    the exact same INVALID_ADDRESS_VALUES denylist. Uses this pipeline's own
    mask_address_invalid (missing, or no letters in any script, or an exact
    denylist match) to decide what's invalid, which is broader than the
    legacy script's denylist-only check. No manual review follows this step
    -- see rules/06-address_fix.txt for what happens to rows nothing here can
    fix (unmapped province)."""
    fixed_rows = 0

    # Case 1: province is "outside the country" -> can't address-map by
    # province, so treat as Baghdad and pull from the general pool.
    province = _s(df, "ADDRESS_PROVINCE")
    outside_country = province.str.lower().eq("outside the country") & mask_address_invalid(df)
    idx = df.index[outside_country]
    if len(idx):
        df.loc[idx, "ADDRESS_CITY"] = "Baghdad"
        df.loc[idx, "ADDRESS_PROVINCE"] = "Baghdad"
        df.loc[idx, "ACCOUNT_ADDRESS"] = balanced_assign(len(idx), BAGHDAD_ADDRESS_POOL)
        fixed_rows += len(idx)

    # Case 2: city is the "00000" placeholder and the address is invalid ->
    # default city/province to Baghdad and pull from the general pool.
    city = _s(df, "ADDRESS_CITY")
    city_placeholder_invalid = city.eq("00000") & mask_address_invalid(df)
    idx = df.index[city_placeholder_invalid]
    if len(idx):
        df.loc[idx, "ADDRESS_CITY"] = "Baghdad"
        df.loc[idx, "ADDRESS_PROVINCE"] = "Baghdad"
        df.loc[idx, "ACCOUNT_ADDRESS"] = balanced_assign(len(idx), BAGHDAD_ADDRESS_POOL)
        fixed_rows += len(idx)

    # Case 3: city is real but the address is invalid -> replace ACCOUNT_ADDRESS
    # only, from that row's own province pool. City/province are left as-is.
    # A province with no pool entry in PROVINCE_ADDRESS_MAP is left invalid --
    # there is nothing to synthesize it from and no manual step catches it
    # afterward, so it shows up in the "invalid addresses remaining" total.
    city = _s(df, "ADDRESS_CITY")
    province = _s(df, "ADDRESS_PROVINCE")
    city_real_invalid = (~city.eq("00000")) & mask_address_invalid(df)
    unmapped_rows = 0
    if city_real_invalid.any():
        provinces_by_row = province[city_real_invalid]
        for prov_key, group_idx in provinces_by_row.groupby(provinces_by_row).groups.items():
            options = PROVINCE_ADDRESS_MAP.get(prov_key, [])
            if not options:
                unmapped_rows += len(group_idx)
                continue
            df.loc[df.index.isin(group_idx), "ACCOUNT_ADDRESS"] = balanced_assign(len(group_idx), options)
            fixed_rows += len(group_idx)

    # Case 4: city is the "00000" placeholder but the address is already
    # valid -> just default city/province to Baghdad, keep the address.
    city = _s(df, "ADDRESS_CITY")
    city_placeholder_valid = city.eq("00000") & ~mask_address_invalid(df)
    idx = df.index[city_placeholder_valid]
    if len(idx):
        df.loc[idx, "ADDRESS_CITY"] = "Baghdad"
        df.loc[idx, "ADDRESS_PROVINCE"] = "Baghdad"

    remaining = int(mask_address_invalid(df).sum())
    summary = f"Auto-filled {fixed_rows} address(es) from the province/Baghdad pool."
    if remaining:
        summary += f" {remaining} row(s) still invalid (unmapped province) and left unresolved."
    return df, summary
