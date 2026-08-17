"""
Score a model's address-validation accuracy against the labeled ground-truth
set in llms/training_data/address_validation.csv -- unlike validate_addresses.py
/ benchmark_models.py (which only measure speed and eyeball a few outputs),
this actually checks whether the verdict was RIGHT.

VALID/INVALID rows are scored against their label. SPECIAL rows (a real
address corrupted with another field's data spliced in) are NOT scored
right/wrong -- there's no single correct verdict for those by design (see
build_training_data.py) -- their outcomes are just tallied separately so you
can see how the model actually handles them.

Usage:
    python evaluate_model.py
    (edit MODEL below to test a different one)
"""
import re
import sys
import time
from datetime import datetime

import openpyxl
import pandas as pd
import ollama

MODEL = "gemma3:4b"
INPUT_FILE = "excess/llms/training_data/address_validation.csv"
OUTPUT_DIR = "excess/llms/data"

# v5: earlier versions (see git history) tried patching specific misses with
# hand-picked few-shot examples one at a time (v4 added a 5th example to fix
# gemma3:4b's remaining misses and made accuracy WORSE, 94% -> 91%, by
# overfitting to those examples at the expense of general cases). That's an
# inherently unstable approach -- each patch trades one model's failure mode
# for another's. v5 drops per-example patching entirely and instead states
# the general rule the failures kept violating: this dataset's real addresses
# are informal/terse/landmark-based by default, and models were penalizing
# that informality as if it were a sign of invalidity. No few-shot examples
# are pulled from the training/eval set itself -- that would just be a subtler
# form of the same overfitting.
PROMPT_TEMPLATE = """You are validating the ACCOUNT_ADDRESS field from a KYC customer-intake system used in Iraq. Real addresses here are often informal and incomplete: people describe where they live by neighborhood, district, city, or a landmark ("near <place>"), written in Arabic, English, or a transliteration of Arabic -- not a formatted street+number postal address. Brevity, missing house/street numbers, and non-standard spelling are NORMAL for this dataset and do NOT by themselves make an address invalid.

Judge the text below as VALID if it plausibly names or points to a real-world place -- a neighborhood, district, street, city, landmark, or a "near <place>" description -- no matter how short, informal, or which script/language it uses.

Judge it INVALID only if it falls into one of these buckets, and does not also describe a place:
- Placeholder / not-collected text (e.g. "N/A", "none", "unknown", "TBD", "-", "?"), in any language or script
- Keyboard mash or characters with no recognizable words, in any language or script
- Data belonging to a different field: a phone number, ID/account number, date, email address, or a person's name
- A sentence, instruction, or comment with no place reference (e.g. a request, a status note)
- Only digits and/or punctuation, with no place name

You MUST respond in EXACTLY this two-line format -- both lines are REQUIRED, including the word Verdict, even when the verdict is INVALID:
Verdict: <VALID or INVALID>
Reason: <one short sentence>

Now evaluate this address. Respond with ONLY the two lines in the format above -- no preamble, no extra explanation.

Address: "{address}"
"""

VERDICT_RE = re.compile(r"Verdict:\s*(VALID|INVALID)", re.IGNORECASE)
REASON_RE = re.compile(r"Reason:\s*(.+)", re.IGNORECASE | re.DOTALL)


def parse_reply(reply: str):
    verdict_match = VERDICT_RE.search(reply)
    reason_match = REASON_RE.search(reply)
    verdict = verdict_match.group(1).upper() if verdict_match else "UNKNOWN"
    reason = reason_match.group(1).strip() if reason_match else reply.strip()
    return verdict, reason


def autofit_columns(path: str):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    for col_cells in ws.columns:
        col_letter = col_cells[0].column_letter
        max_len = max(len(str(c.value)) for c in col_cells)
        ws.column_dimensions[col_letter].width = min(max_len + 4, 80)
    wb.save(path)


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    # keep_default_na=False: the INVALID set deliberately contains literal
    # "None"/"null"/"N/A"/"-" strings as garbage examples -- without this,
    # pandas silently reads those as NaN instead of the actual text.
    df = pd.read_csv(INPUT_FILE, encoding="utf-8-sig", keep_default_na=False)

    safe_name = MODEL.replace(":", "_")
    out_file = f"{OUTPUT_DIR}/eval_{safe_name}.xlsx"
    report_file = f"excess/llms/eval_report_{safe_name}.txt"

    results = []
    row_times = []
    run_start = time.perf_counter()
    for i, row in enumerate(df.itertuples(index=False), start=1):
        address, true_label = row.ACCOUNT_ADDRESS, row.LABEL
        prompt = PROMPT_TEMPLATE.format(address=address)
        row_start = time.perf_counter()
        response = ollama.chat(model=MODEL, messages=[{"role": "user", "content": prompt}])
        row_seconds = time.perf_counter() - row_start
        row_times.append(row_seconds)
        reply = response["message"]["content"].strip()
        predicted, reason = parse_reply(reply)

        if true_label == "SPECIAL":
            correct = None  # not scored -- see module docstring
        else:
            correct = predicted == true_label

        mark = "?" if correct is None else ("OK" if correct else "XX")
        print(f"[{i}/{len(df)}] ({row_seconds:.2f}s) [{mark}] true={true_label} pred={predicted} :: {address}", flush=True)
        results.append({
            "ACCOUNT_ADDRESS": address,
            "TRUE_LABEL": true_label,
            "PREDICTED": predicted,
            "CORRECT": correct,
            "REASON": reason,
            "TIME_SECONDS": round(row_seconds, 2),
        })
    total_seconds = time.perf_counter() - run_start

    results_df = pd.DataFrame(results)
    results_df.to_excel(out_file, index=False)
    autofit_columns(out_file)

    scored = results_df[results_df["TRUE_LABEL"] != "SPECIAL"]
    accuracy = scored["CORRECT"].mean() if len(scored) else float("nan")

    # Confusion matrix over the scored (VALID/INVALID) rows -- rows=true,
    # cols=predicted (predicted can be VALID/INVALID/UNKNOWN).
    confusion = pd.crosstab(scored["TRUE_LABEL"], scored["PREDICTED"], dropna=False)

    special = results_df[results_df["TRUE_LABEL"] == "SPECIAL"]
    special_counts = special["PREDICTED"].value_counts()

    avg_seconds = total_seconds / len(row_times)

    report_lines = [
        f"Model Evaluation - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Model: {MODEL}",
        f"Input file: {INPUT_FILE}",
        f"Output file: {out_file}",
        f"Rows: {len(df)} ({len(scored)} scored VALID/INVALID, {len(special)} SPECIAL, not scored)",
        "",
        f"Accuracy (VALID/INVALID only): {accuracy:.1%} ({int(scored['CORRECT'].sum())}/{len(scored)})",
        "",
        "Confusion matrix (rows=true label, cols=predicted):",
        confusion.to_string(),
        "",
        "SPECIAL rows (corrupted-but-real address) -- not scored, just tallied:",
    ]
    for label, count in special_counts.items():
        report_lines.append(f"  predicted {label}: {count}/{len(special)}")
    report_lines += [
        "",
        "Timing:",
        f"  Total time: {total_seconds:.2f}s | Avg/row: {avg_seconds:.2f}s | "
        f"Min: {min(row_times):.2f}s | Max: {max(row_times):.2f}s",
        "",
        "Misclassified (VALID/INVALID only):",
    ]
    wrong = scored[scored["CORRECT"] == False]  # noqa: E712
    if wrong.empty:
        report_lines.append("  (none)")
    else:
        for _, r in wrong.iterrows():
            report_lines.append(f"  true={r['TRUE_LABEL']:<7} pred={r['PREDICTED']:<7} {r['ACCOUNT_ADDRESS']}")

    with open(report_file, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print("\n" + "\n".join(report_lines))
    print(f"\nSaved {len(results_df)} results to {out_file}")
    print(f"Saved report to {report_file}")


if __name__ == "__main__":
    main()
