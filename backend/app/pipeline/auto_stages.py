import random

import pandas as pd

from .constants import BAGHDAD_ADDRESS_POOL, INVALID_ADDRESSES, NOT_COLLECTED, PROVINCE_ADDRESS_MAP
from .utils import _s, balanced_assign, series_available
from .validators import compute_id_validity


def stage_clean_linebreaks(df: pd.DataFrame, **_):
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.replace("\n", " ", regex=False).str.replace("\r", " ", regex=False)
    return df, "Removed line breaks from all fields."


def stage_reset_cms_fields(df: pd.DataFrame, **_):
    for col in ["ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]:
        if col in df.columns:
            df[col] = ""
    return df, "Cleared ACCOUNT_TYPE, CARD_TYPE, CARD_PROGRAM, CARD_STATUS (to be repopulated from CMS)."


def stage_address_fix(df: pd.DataFrame, **_):
    for col in ["PHONE_NUMBER", "ADDRESS_CITY", "ADDRESS_PROVINCE", "ACCOUNT_ADDRESS"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    phone_str = _s(df, "PHONE_NUMBER")
    mask_phone_fix = phone_str.eq("") | phone_str.str.fullmatch(r"0+", na=False)
    df.loc[mask_phone_fix, "PHONE_NUMBER"] = NOT_COLLECTED

    addr_str = _s(df, "ACCOUNT_ADDRESS")
    prov_str = _s(df, "ADDRESS_PROVINCE")
    city_str = _s(df, "ADDRESS_CITY")
    mask_addr_invalid = addr_str.isin(INVALID_ADDRESSES)

    # Outside-country + invalid address -> force Baghdad
    mask_outside = prov_str.str.lower().eq("outside the country") & mask_addr_invalid
    idx = df.index[mask_outside].tolist()
    if idx:
        assigned = balanced_assign(len(idx), BAGHDAD_ADDRESS_POOL)
        df.loc[mask_outside, "ADDRESS_CITY"] = "Baghdad"
        df.loc[mask_outside, "ADDRESS_PROVINCE"] = "Baghdad"
        df.loc[mask_outside, "ACCOUNT_ADDRESS"] = assigned

    city_str = _s(df, "ADDRESS_CITY")
    addr_str = _s(df, "ACCOUNT_ADDRESS")
    mask_addr_invalid = addr_str.isin(INVALID_ADDRESSES)

    # city == 00000 and address invalid -> Baghdad + pool address
    mask_a = city_str.eq("00000") & mask_addr_invalid
    idx = df.index[mask_a].tolist()
    if idx:
        assigned = balanced_assign(len(idx), BAGHDAD_ADDRESS_POOL)
        df.loc[mask_a, "ADDRESS_CITY"] = "Baghdad"
        df.loc[mask_a, "ADDRESS_PROVINCE"] = "Baghdad"
        df.loc[mask_a, "ACCOUNT_ADDRESS"] = assigned

    city_str = _s(df, "ADDRESS_CITY")
    addr_str = _s(df, "ACCOUNT_ADDRESS")
    prov_str = _s(df, "ADDRESS_PROVINCE")
    mask_addr_invalid = addr_str.isin(INVALID_ADDRESSES)

    # city != 00000 and address invalid -> province-mapped address
    mask_b = (~city_str.eq("00000")) & mask_addr_invalid
    mask_b_repaired = pd.Series(False, index=df.index)
    mask_b_unmapped = pd.Series(False, index=df.index)
    for province, group_idx in df.loc[mask_b].groupby(prov_str[mask_b]).groups.items():
        options = PROVINCE_ADDRESS_MAP.get(str(province).strip(), [])
        group_mask = df.index.isin(group_idx)
        if not options:
            mask_b_unmapped |= group_mask
            continue
        assigned = balanced_assign(len(group_idx), options)
        df.loc[group_mask, "ACCOUNT_ADDRESS"] = assigned
        mask_b_repaired |= group_mask

    city_str = _s(df, "ADDRESS_CITY")
    addr_str = _s(df, "ACCOUNT_ADDRESS")
    mask_addr_invalid = addr_str.isin(INVALID_ADDRESSES)

    # city == 00000 and address NOT invalid -> just fix city/province
    mask_c = city_str.eq("00000") & (~mask_addr_invalid)
    df.loc[mask_c, "ADDRESS_CITY"] = "Baghdad"
    df.loc[mask_c, "ADDRESS_PROVINCE"] = "Baghdad"

    repaired_count = int((mask_outside | mask_a | mask_b_repaired | mask_c).sum())
    unmapped_count = int(mask_b_unmapped.sum())
    summary = (
        f"Phone blanked to NOT_COLLECTED: {int(mask_phone_fix.sum())}. "
        f"Addresses auto-repaired: {repaired_count}. "
        f"Invalid addresses left unchanged due to unmapped province: {unmapped_count}."
    )
    return df, summary


def default_id_invalid_mask(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    return ~(type_valid & num_valid)


def stage_default_id_assign(df: pd.DataFrame, **_):
    invalid_mask = default_id_invalid_mask(df)
    # Avoid every existing value, including IDs on rows that currently fail
    # validation, so generated Civil IDs cannot duplicate data already present.
    used = set(_s(df, "ID_NUMBER"))
    rnd = random.Random()
    new_ids = []
    for _ in range(int(invalid_mask.sum())):
        while True:
            candidate = "00" + f"{rnd.randint(0, 999999):06d}"
            if candidate not in used:
                used.add(candidate)
                new_ids.append(candidate)
                break
    df.loc[invalid_mask, "ID_NUMBER"] = new_ids
    df.loc[invalid_mask, "ID_TYPE"] = "Civil Id"
    return df, f"Assigned default Civil ID numbers to {int(invalid_mask.sum())} remaining invalid records."
