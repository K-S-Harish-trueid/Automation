"""
Benchmark lightweight Ollama models on the same address-validation task
used by validate_addresses.py, to compare speed and output quality.

Usage:
    python benchmark_models.py
"""
import os
import re
import sys
import time
from datetime import datetime

import openpyxl
import pandas as pd
import ollama

MODELS = ["llama3.2:3b", "llama3.2:1b"]
# qwen3:4b, gemma3:4b, command-r7b-arabic excluded: too slow (well over the 10s/row hard max) or not yet worth the wait. Focusing on Llama models for raw speed.
INPUT_FILE = "llms/data/K2_DATA_PAH_20260422_address.xlsx"
OUTPUT_DIR = "llms/data"
REPORT_FILE = "llms/benchmark_report.txt"
NUM_ROWS = 10

# Synced from evaluate_model.py's v5 prompt (2026-08-12) -- see that file's
# docstring/comment history for why this wording was chosen (criteria-based,
# no per-example patching, no few-shot examples pulled from the eval set).
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


def run_model(model: str, addresses):
    results = []
    row_times = []
    start = time.perf_counter()
    for i, address in enumerate(addresses, start=1):
        prompt = PROMPT_TEMPLATE.format(address=address)
        row_start = time.perf_counter()
        response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        row_seconds = time.perf_counter() - row_start
        row_times.append(row_seconds)
        reply = response["message"]["content"].strip()
        verdict, reason = parse_reply(reply)
        print(f"  [{i}] ({row_seconds:.2f}s) {address} -> {verdict}: {reason}", flush=True)
        results.append(
            {
                "ACCOUNT_ADDRESS": address,
                "VALID_INVALID": verdict,
                "REASON": reason,
                "TIME_SECONDS": round(row_seconds, 2),
            }
        )
    total_seconds = time.perf_counter() - start
    return results, row_times, total_seconds


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    df = pd.read_excel(INPUT_FILE, engine="calamine")
    addresses = df["ACCOUNT_ADDRESS"].head(NUM_ROWS).tolist()

    report_lines = [
        f"Lightweight Model Benchmark - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Task: address validation (same {NUM_ROWS} rows, same prompt, for all models)",
        f"Input file: {INPUT_FILE}",
        "",
    ]

    summary_rows = []

    for model in MODELS:
        safe_name = model.replace(":", "_")
        out_file = f"{OUTPUT_DIR}/benchmark_{safe_name}.xlsx"

        if os.path.exists(out_file):
            print(f"\n=== {model} (already done, skipping - {out_file}) ===")
            results_df = pd.read_excel(out_file, engine="calamine")
            row_times = results_df["TIME_SECONDS"].tolist()
            total_seconds = sum(row_times)
        else:
            print(f"\n=== {model} ===", flush=True)
            results, row_times, total_seconds = run_model(model, addresses)
            results_df = pd.DataFrame(results)
            results_df.to_excel(out_file, index=False)
            autofit_columns(out_file)

        valid_count = (results_df["VALID_INVALID"] == "VALID").sum()
        invalid_count = (results_df["VALID_INVALID"] == "INVALID").sum()
        unknown_count = len(results_df) - valid_count - invalid_count
        avg_seconds = total_seconds / len(row_times)

        summary_rows.append(
            {
                "model": model,
                "total_s": total_seconds,
                "avg_s": avg_seconds,
                "min_s": min(row_times),
                "max_s": max(row_times),
                "valid": valid_count,
                "invalid": invalid_count,
                "unknown": unknown_count,
                "output_file": out_file,
            }
        )

        report_lines += [
            f"Model: {model}",
            f"  Output file: {out_file}",
            f"  Total time: {total_seconds:.2f}s | Avg/row: {avg_seconds:.2f}s | Min: {min(row_times):.2f}s | Max: {max(row_times):.2f}s",
            f"  Valid: {valid_count}  Invalid: {invalid_count}  Unknown: {unknown_count}",
            "  Per-row:",
        ]
        for i, (t, verdict) in enumerate(zip(row_times, results_df["VALID_INVALID"]), start=1):
            report_lines.append(f"    Row {i}: {t:.2f}s -> {verdict}")
        report_lines.append("")

    report_lines.append("Summary (fastest first):")
    for row in sorted(summary_rows, key=lambda r: r["avg_s"]):
        report_lines.append(
            f"  {row['model']:<12} avg={row['avg_s']:.2f}s total={row['total_s']:.2f}s "
            f"valid={row['valid']} invalid={row['invalid']} unknown={row['unknown']}"
        )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print("\n" + "\n".join(report_lines[-len(summary_rows) - 1 :]))
    print(f"\nSaved benchmark report to {REPORT_FILE}")


if __name__ == "__main__":
    main()
