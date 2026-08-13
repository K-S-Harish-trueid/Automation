"""
Build a small labeled ground-truth set for evaluating address-validation
models -- 50 real valid addresses sampled from the actual K2 dataset, 50
synthetic invalid ones covering the kinds of garbage that actually shows up
in KYC intake data (gibberish, placeholders, wrong-field data, etc.), and 20
"special" ones: a real valid address with a fragment from another field
(phone/ID/account number/DOB/email/name) spliced in -- these are corrupted,
not gibberish, so the right verdict is arguably neither a clean VALID nor a
clean INVALID. Labeled SPECIAL rather than folded into either bucket, so a
model's behavior on them can be looked at separately instead of silently
counting as wrong (or right) against a binary label.

The benchmark scripts (benchmark_models.py) only ever tested against the
first 10 rows of the real dataset -- which happened to all be genuinely
valid addresses, so no benchmark run has ever confirmed a model correctly
says INVALID when it should. This set exists to close that gap.

Usage:
    python build_training_data.py
"""
import random

import pandas as pd

INPUT_FILE = "llms/data/K2_DATA_PAH_20260422_address.xlsx"
OUTPUT_FILE = "llms/training_data/address_validation.csv"
SEED = 42
N_VALID = 50
N_SPECIAL = 20

# Fragments from OTHER K2 fields (phone/ID/account number/DOB/email/name),
# in the real formats those fields actually use elsewhere in this pipeline
# (see rules/05-id_dob_validate.txt for the ID_NUMBER formats, PHONE_NUMBER
# examples from mobile_fill). Spliced onto a real address to simulate a
# data-entry mistake -- the wrong field's content landing in ACCOUNT_ADDRESS.
OTHER_FIELD_FRAGMENTS = [
    "07901234567", "07712345678", "07809876543",           # PHONE_NUMBER
    "A1234567", "B7654321Z",                                 # Passport ID_NUMBER
    "123456789012", "987654321098",                          # National ID_NUMBER
    "0001459809", "917326513590",                             # ACCOUNT_NUMBER
    "1985-04-12", "1992-11-03", "2008-08-11",                # DOB / DATE_OPENED
    "ahmed.ali@example.com", "fatima.hassan@mail.com",       # EMAIL_ADDRESS
    "احمد محمد علي", "فاطمة عبدالله حسن", "Mohammed Hassan Ali",  # NAME
]

# Real garbage seen in messy KYC intake data, grouped by why it's invalid.
# Not addresses at all -- deliberately NOT edge cases (an incomplete-but-real
# neighborhood name), which is a separate, harder problem from "is this text
# an address in the first place."
INVALID_ADDRESSES = [
    # Keyboard mash / gibberish (Latin)
    "asdkjfh aslkdjf", "qwertyuiop", "zxcvbnm123", "lkjhgfdsa poiuytre",
    "xxxxxxxxxx", "aaaaaaaaaa", "1q2w3e4r5t", "mnbvcxz lkjhgf",
    # Keyboard mash / gibberish (Arabic script, not real words)
    "ضصثقفغعهخ", "ئءؤرلاىةوز", "طظكمنتالب", "شسيبلاهتن",
    # Placeholder / "not collected" style values a form might contain
    "N/A", "n/a", "None", "Not Provided", "unknown", "Unknown", "TBD",
    "test", "TEST123", "-", "--", "...", "غير معروف", "لا يوجد", "test test",
    # Numbers-only / punctuation-only (not a street number+name, just digits)
    "123456789", "00000000", "0000", "12345", "999999999", "....",
    # Wrong-field data: something real but not an address (phone/ID/name/date)
    "07901234567", "07712345678", "1985-04-12", "A12345678",
    "Ahmed Mohammed Ali", "mohammed@example.com", "1234-5678-9012",
    "male, 34 years old", "https://example.com/profile",
    # Unrelated real text (not gibberish, but clearly not an address)
    "the quick brown fox jumps", "please call before delivery",
    "customer requested callback", "see attached document",
    "الرجاء الاتصال قبل التسليم", "شكرا لتعاونكم", "بيانات غير مكتملة",
    # Single/near-empty meaningless fragments
    "x", "?", "..", "na", "0", "-1", "null", "NULL", "()", "[]",
]


def main():
    random.seed(SEED)

    df = pd.read_excel(INPUT_FILE, engine="calamine")
    addresses = df["ACCOUNT_ADDRESS"].astype(str).str.strip()
    # Keep only reasonably substantial, unique entries -- short (<4 char)
    # real values are too ambiguous to use as confident ground-truth "valid"
    # (e.g. "V", "R6" could be legitimate block codes or could be junk; not
    # what this set is for).
    candidates = addresses[addresses.str.len() >= 4].unique().tolist()

    rng = random.Random(SEED)
    valid_sample = rng.sample(candidates, N_VALID)

    invalid_sample = list(INVALID_ADDRESSES)
    assert len(invalid_sample) >= 50, f"only {len(invalid_sample)} invalid examples, need 50"
    invalid_sample = rng.sample(invalid_sample, 50)

    # Different real addresses from the VALID sample above, so the special
    # set doesn't just re-corrupt the same 50 -- draws from whatever's left
    # after removing what valid_sample already took.
    remaining = [a for a in candidates if a not in set(valid_sample)]
    special_base = rng.sample(remaining, N_SPECIAL)
    special_sample = []
    for address in special_base:
        fragment = rng.choice(OTHER_FIELD_FRAGMENTS)
        # Randomize which side the fragment lands on -- a data-entry mistake
        # could paste the wrong field's value before or after the real text.
        corrupted = f"{address} {fragment}" if rng.random() < 0.5 else f"{fragment} {address}"
        special_sample.append(corrupted)

    rows = (
        [{"ACCOUNT_ADDRESS": a, "LABEL": "VALID"} for a in valid_sample]
        + [{"ACCOUNT_ADDRESS": a, "LABEL": "INVALID"} for a in invalid_sample]
        + [{"ACCOUNT_ADDRESS": a, "LABEL": "SPECIAL"} for a in special_sample]
    )
    rng.shuffle(rows)

    out_df = pd.DataFrame(rows)
    # Deliberately includes literal "None"/"null"/"NULL"/"N/A"/"-" strings as
    # INVALID examples (real placeholder garbage). pandas' default read_csv
    # treats those exact strings as NA and silently blanks them out -- always
    # read this file back with keep_default_na=False (see how store.py's own
    # CSV path in the K2 backend already does this for the same reason).
    out_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    counts = out_df["LABEL"].value_counts()
    print(f"Wrote {len(out_df)} rows ({counts.get('VALID', 0)} valid, "
          f"{counts.get('INVALID', 0)} invalid, {counts.get('SPECIAL', 0)} special) to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
