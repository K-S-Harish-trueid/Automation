"""
Validate the first N addresses from K2_DATA_PAH_20260422_address.xlsx
using a local Ollama model.

Usage:
    python validate_addresses.py
"""
import re
import sys
import time
from datetime import datetime

import openpyxl
import pandas as pd
import ollama

MODEL = "command-r7b-arabic"
INPUT_FILE = "llms/data/K2_DATA_PAH_20260422_address.xlsx"
OUTPUT_FILE = "llms/data/address_validation_results.xlsx"
REPORT_FILE = "llms/report.txt"
NUM_ROWS = 10

PROMPT_TEMPLATE = """You are an address validation assistant. The addresses may be written in Arabic, English, or a transliteration of Arabic, and may be incomplete (e.g. neighborhood/district only, no house or street number).

Task: decide whether the text below is a plausible, real-world postal/residential address (e.g. it names a real place, district, street, or landmark), as opposed to gibberish, a placeholder, or clearly unrelated text.

Respond in exactly this format, nothing else:
Verdict: VALID or INVALID
Reason: <one short sentence>

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

    df = pd.read_excel(INPUT_FILE, engine="calamine")
    total_input_rows = len(df)
    sample = df["ACCOUNT_ADDRESS"].head(NUM_ROWS)

    results = []
    row_times = []
    run_start = time.perf_counter()
    for i, address in enumerate(sample, start=1):
        prompt = PROMPT_TEMPLATE.format(address=address)
        row_start = time.perf_counter()
        response = ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        row_seconds = time.perf_counter() - row_start
        row_times.append(row_seconds)
        reply = response["message"]["content"].strip()
        verdict, reason = parse_reply(reply)
        print(f"[{i}] ({row_seconds:.2f}s) {address} -> {verdict}: {reason}")
        results.append(
            {
                "ACCOUNT_ADDRESS": address,
                "VALID_INVALID": verdict,
                "REASON": reason,
                "TIME_SECONDS": round(row_seconds, 2),
            }
        )
    total_seconds = time.perf_counter() - run_start

    results_df = pd.DataFrame(results)
    results_df.to_excel(OUTPUT_FILE, index=False)
    autofit_columns(OUTPUT_FILE)

    valid_count = (results_df["VALID_INVALID"] == "VALID").sum()
    invalid_count = (results_df["VALID_INVALID"] == "INVALID").sum()
    unknown_count = len(results_df) - valid_count - invalid_count
    avg_seconds = total_seconds / len(row_times)
    fastest_i = min(range(len(row_times)), key=row_times.__getitem__)
    slowest_i = max(range(len(row_times)), key=row_times.__getitem__)

    report_lines = [
        f"Address Validation Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Model: {MODEL}",
        f"Input file: {INPUT_FILE}",
        f"Output file: {OUTPUT_FILE}",
        f"Total rows in input file: {total_input_rows}",
        f"Rows processed (sent to model): {len(results_df)}",
        f"Valid: {valid_count}",
        f"Invalid: {invalid_count}",
        f"Unknown/unparsed: {unknown_count}",
        "",
        "Timing:",
        f"  Total time: {total_seconds:.2f}s",
        f"  Average per row: {avg_seconds:.2f}s",
        f"  Fastest row: #{fastest_i + 1} ({row_times[fastest_i]:.2f}s)",
        f"  Slowest row: #{slowest_i + 1} ({row_times[slowest_i]:.2f}s)",
        "  Per-row breakdown:",
    ]
    for i, t in enumerate(row_times, start=1):
        report_lines.append(f"    Row {i}: {t:.2f}s")

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print("\n".join(report_lines))
    print(f"\nSaved {len(results_df)} results to {OUTPUT_FILE}")
    print(f"Saved report to {REPORT_FILE}")


if __name__ == "__main__":
    main()
