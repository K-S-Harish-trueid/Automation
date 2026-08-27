import pandas as pd

from ... import address_pools_db
from ..toolbox import (
    _s,
    balanced_assign,
    series_available,
    series_has_leading_zero_run,
    series_has_letter,
    series_has_long_digit_run,
)


def mask_address_invalid(df: pd.DataFrame) -> pd.Series:
    address = _s(df, "ACCOUNT_ADDRESS")
    missing = ~series_available(address)
    # No letters at all -- e.g. a phone number typed into the address field,
    # or leftover punctuation like "-": no real street/area name is ever
    # pure digits/symbols. Also covers "missing" (an empty string has none).
    no_letters = ~series_has_letter(address)
    # Exact matches against known-junk values that DO contain letters (e.g.
    # "JUNE", "A") and so slip past the no_letters check above. Denylist
    # comes from Postgres (address_pools_db.py), not a hardcoded set --
    # editable directly in the database, no code change/redeploy needed.
    known_bad = address.str.lower().isin(address_pools_db.load_denylist())
    # Catches a real place name with junk numbers glued on -- has_letter
    # and known_bad above both miss this, since the string genuinely
    # contains letters and isn't an exact denylist match. See
    # series_has_leading_zero_run/series_has_long_digit_run's own
    # docstrings for exactly what each catches and why (in particular: the
    # zero-run check deliberately requires the zeros to LEAD the digit run,
    # not just appear in it, to avoid flagging a real Iraqi postal code).
    leading_zero_run = series_has_leading_zero_run(address, min_zeros=3)
    long_digit_run = series_has_long_digit_run(address, min_len=7)
    return missing | no_letters | known_bad | leading_zero_run | long_digit_run


def stage_address_fix(df: pd.DataFrame, **_):
    """Auto-fills invalid ACCOUNT_ADDRESS values from a Baghdad/province
    address pool -- ported from the legacy script (aman/pah3.py), which used
    the exact same denylist. Uses this pipeline's own mask_address_invalid
    (missing, or no letters in any script, or an exact denylist match) to
    decide what's invalid, which is broader than the legacy script's
    denylist-only check. No manual review follows this step -- see
    rules/06-address_fix.txt for what happens to rows nothing here can fix
    (unmapped province).

    The denylist and address pools both come from Postgres
    (address_pools_db.py) -- editable directly in the database (pgAdmin or
    any SQL client), not hardcoded here anymore. Loaded once per call below
    (not once per mask_address_invalid call above), so one job run costs at
    most one cache refresh, not several."""
    fixed_rows = 0
    baghdad_pool = address_pools_db.load_baghdad_pool()
    pool_map = address_pools_db.load_pool_map()

    # Case 1: province is "outside the country" -> can't address-map by
    # province, so treat as Baghdad and pull from the general pool.
    # `and baghdad_pool` guards below: skip the whole case, touching nothing,
    # rather than half-fix a row (city/province set to Baghdad but no real
    # address to put there) if the pool table is empty/unreachable -- the
    # row just stays counted in "remaining invalid", same as an unmapped
    # province in Case 3 below.
    province = _s(df, "ADDRESS_PROVINCE")
    outside_country = province.str.lower().eq("outside the country") & mask_address_invalid(df)
    idx = df.index[outside_country]
    if len(idx) and baghdad_pool:
        df.loc[idx, "ADDRESS_CITY"] = "Baghdad"
        df.loc[idx, "ADDRESS_PROVINCE"] = "Baghdad"
        df.loc[idx, "ACCOUNT_ADDRESS"] = balanced_assign(len(idx), baghdad_pool)
        fixed_rows += len(idx)

    # Case 2: city is the "00000" placeholder and the address is invalid ->
    # default city/province to Baghdad and pull from the general pool.
    city = _s(df, "ADDRESS_CITY")
    city_placeholder_invalid = city.eq("00000") & mask_address_invalid(df)
    idx = df.index[city_placeholder_invalid]
    if len(idx) and baghdad_pool:
        df.loc[idx, "ADDRESS_CITY"] = "Baghdad"
        df.loc[idx, "ADDRESS_PROVINCE"] = "Baghdad"
        df.loc[idx, "ACCOUNT_ADDRESS"] = balanced_assign(len(idx), baghdad_pool)
        fixed_rows += len(idx)

    # Case 3: city is real but the address is invalid -> replace ACCOUNT_ADDRESS
    # only, from that row's own province pool. City/province are left as-is.
    # A province with no pool entry is left invalid -- there is nothing to
    # synthesize it from and no manual step catches it afterward, so it
    # shows up in the "invalid addresses remaining" total. (Also true if the
    # pool table itself is empty/unreachable -- see address_pools_db.py.)
    city = _s(df, "ADDRESS_CITY")
    province = _s(df, "ADDRESS_PROVINCE")
    city_real_invalid = (~city.eq("00000")) & mask_address_invalid(df)
    unmapped_rows = 0
    if city_real_invalid.any():
        provinces_by_row = province[city_real_invalid]
        for prov_key, group_idx in provinces_by_row.groupby(provinces_by_row).groups.items():
            options = pool_map.get(prov_key, [])
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
