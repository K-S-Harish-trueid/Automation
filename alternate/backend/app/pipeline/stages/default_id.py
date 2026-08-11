import random

import pandas as pd

from ..toolbox import _s, compute_id_validity, series_available


def default_id_invalid_mask(df: pd.DataFrame) -> pd.Series:
    type_valid, num_valid = compute_id_validity(df)
    return ~(type_valid & num_valid)


def stage_default_id_assign(df: pd.DataFrame, **_):
    invalid_mask = default_id_invalid_mask(df)

    # Do NOT overwrite accounts that already carry a real ID_NUMBER value --
    # even if that value fails the strict format check (e.g. a passport
    # number that doesn't start with a letter). These values come from
    # Haider's manually reviewed corrections and must be preserved as-is.
    # Only accounts whose ID_NUMBER is genuinely blank / a placeholder receive
    # a generated default Civil ID.
    has_existing_id = series_available(
        df.get("ID_NUMBER", pd.Series([""] * len(df), index=df.index))
    )
    assign_mask = invalid_mask & ~has_existing_id

    # Avoid every existing value, including IDs on rows that currently fail
    # validation, so generated Civil IDs cannot duplicate data already present.
    used = set(_s(df, "ID_NUMBER"))
    rnd = random.Random()
    new_ids = []
    for _ in range(int(assign_mask.sum())):
        while True:
            candidate = "00" + f"{rnd.randint(0, 999999):06d}"
            if candidate not in used:
                used.add(candidate)
                new_ids.append(candidate)
                break
    df.loc[assign_mask, "ID_NUMBER"] = new_ids
    df.loc[assign_mask, "ID_TYPE"] = "Civil Id"

    skipped = int(invalid_mask.sum()) - int(assign_mask.sum())
    msg = f"Assigned default Civil ID numbers to {int(assign_mask.sum())} remaining invalid records."
    if skipped:
        msg += f" Preserved existing non-blank ID values on {skipped} account(s) that failed format validation."
    return df, msg
