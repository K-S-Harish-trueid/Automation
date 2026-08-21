from datetime import datetime, timezone

import pandas as pd
from fastapi import HTTPException

from . import audit_log_db, pipeline, store
from .pipeline import stage_merge
from .schemas import EditItem


def _live_stage_title(stage_id: str) -> str | None:
    from .pipeline.registry import STAGES
    return {s["id"]: s["title"] for s in STAGES}.get(stage_id)


def _current_stage(status: dict) -> dict | None:
    idx = status["stage_index"]
    if idx >= len(status["stages"]):
        return None
    # The real stage dict, not a copy -- callers mutate this in place
    # (cur["status"] = "done", history entries keyed off cur["id"]) and rely
    # on that persisting when the job status is saved. Re-resolve the title
    # from the live registry instead of trusting whatever was snapshotted
    # into this job's status at creation time -- otherwise a job created
    # before a stage got renamed keeps showing the old title forever. This
    # also self-heals the on-disk snapshot the next time it's saved, which
    # is a feature, not a side effect: no reason to keep serving a stale
    # title once we know the live one.
    stage = status["stages"][idx]
    live_title = _live_stage_title(stage.get("id"))
    if live_title:
        stage["title"] = live_title
    return stage


def _phase_progress(status: dict, stage_index: int, sub_step: int = 0, sub_total: int = 1) -> tuple[int, int, int]:
    """(current_step_index, total_steps, percent) scoped to just the phase
    (registry.py stage entries' "stage": 1/2/3) that `stage_index` belongs
    to, excluding hidden stages (registry.HIDDEN_STAGE_IDS) -- the single
    source of truth for every store.set_progress call in the app, so the
    busy-overlay ring's "Step X/Y" always agrees with what the sidebar's own
    scoped count (app.js's visibleStageEntries) shows for that phase,
    instead of counting across all 3 phases combined (that mismatch, plus
    Stage 2/3 never reporting progress mid-gate, is why the ring used to
    look stuck/wrong outside Stage 1 -- see git history around this
    function).

    sub_step/sub_total let one registry stage that actually does several
    things internally (e.g. Stage 3's stage2_dispatch gate: CMS mobile, CMS
    card, Haider's corrections, DOB normalisation, all under one entry)
    report fractional progress through itself instead of jumping straight
    from start to 100% -- pass e.g. sub_step=2, sub_total=4 partway through."""
    from .pipeline.registry import HIDDEN_STAGE_IDS
    stages = status["stages"]
    done = stage_index >= len(stages)
    phase = stages[-1]["stage"] if done else stages[stage_index]["stage"]
    phase_stages = [s for s in stages if s["stage"] == phase and s["id"] not in HIDDEN_STAGE_IDS]
    total = len(phase_stages) or 1
    if done:
        done_before, sub_step, sub_total = total, 0, 1
    else:
        done_before = len([
            s for i, s in enumerate(stages)
            if i < stage_index and s["stage"] == phase and s["id"] not in HIDDEN_STAGE_IDS
        ])
    current_step_index = min(done_before + 1, total)
    fraction = min((done_before + (sub_step / sub_total)) / total, 1.0)
    return current_step_index, total, round(fraction * 100)


def _require_job(job_id: str) -> dict:
    if not store.job_exists(job_id):
        raise HTTPException(404, "job not found")
    return store.get_status(job_id)


def _public_job_status(status: dict) -> dict:
    """Keep large draft data out of the polling response. Audit never lives
    in `status` at all (see store.append_audit_events), so there's nothing
    to strip for that anymore -- audit_event_count below reads it from its
    own file, cheaply (a line count, not a full parse)."""
    rollback_targets = store.get_rollback_targets(status["job_id"])
    public_status = {
        key: value for key, value in status.items()
        if key not in {"drafts", "checkpoints"}
    }
    if "stages" in public_status:
        from .pipeline.registry import HIDDEN_STAGE_IDS
        public_status["stages"] = [
            {
                **s,
                "title": _live_stage_title(s.get("id")) or s["title"],
                # Display-only: the stage still runs normally, this just
                # tells the frontend not to show it in the sidebar/progress
                # meter (see registry.py's HIDDEN_STAGE_IDS).
                "hidden": s.get("id") in HIDDEN_STAGE_IDS,
            }
            for s in public_status["stages"]
        ]
    return public_status | {
        "audit_event_count": store.count_audit_events(status["job_id"]),
        "rollback_available": bool(rollback_targets),
        "rollback_label": rollback_targets[-1]["label"] if rollback_targets else "",
        "rollback_targets": rollback_targets,
    }


def _audit_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _append_audit_events(
    status: dict,
    before: pd.DataFrame,
    after: pd.DataFrame,
    *,
    stage_id: str,
    fields: list[str] | None = None,
    label: str | dict[str, str],
    reason: str | dict[str, str],
    source_file: str,
    operator: str,
):
    """Store field-level changes without changing any pipeline decision or
    value. Appended straight to the job's audit.jsonl (store.py) -- never
    held on `status` itself, so callers don't need to persist it separately."""
    fields = fields or [column for column in after.columns if column in before.columns]
    account_numbers = after.get("ACCOUNT_NUMBER", pd.Series("", index=after.index))
    events = []
    for field in fields:
        if field not in before.columns or field not in after.columns:
            continue
        before_values = before[field].map(_audit_text)
        after_values = after[field].map(_audit_text)
        changed = before_values.ne(after_values)
        for row_key in after.index[changed]:
            event_label = label.get(field, "System corrected") if isinstance(label, dict) else label
            event_reason = reason.get(field, "Value updated") if isinstance(reason, dict) else reason
            events.append({
                "row_key": int(row_key),
                "account_number": _audit_text(account_numbers.loc[row_key]),
                "field": field,
                "old_value": before_values.loc[row_key],
                "new_value": after_values.loc[row_key],
                "stage": stage_id,
                "operator": operator,
                "time": datetime.now(timezone.utc).isoformat(),
                "reason": event_reason,
                "source_file": source_file,
                "label": event_label,
            })
    store.append_audit_events(status["job_id"], events)
    # Best-effort mirror into Postgres (see audit_log_db.py) -- never raises,
    # so a Postgres hiccup can't affect the local audit.jsonl write above or
    # the pipeline step that triggered this call.
    audit_log_db.record_events(status["job_id"], events)
    return events


def _history_metrics(status: dict, stage_id: str) -> dict:
    for event in reversed(status.get("history", [])):
        if event.get("stage_id") == stage_id:
            return event.get("metrics", {})
    return {}


def _quality_summary(df: pd.DataFrame, status: dict, audit: list[dict]) -> dict:
    # A real correction now comes from Stage 2/3 (Naresh/Haider) or, if
    # manual-edit stages are ever re-enabled, an operator's inline edit --
    # never from a fixed stage id or exact label string, both of which
    # stopped matching once corrections moved off the old per-field manual
    # stages. Every audit event records who made it, so "not System" is the
    # one signal that still means "a person actually fixed this."
    names_corrected = len({event["row_key"] for event in audit
                           if event.get("field") in stage_merge.NAME_COLS and event.get("operator") != "System"})
    generated_ids = len({event["row_key"] for event in audit
                         if event.get("stage") == "default_id" and event.get("field") == "ID_NUMBER"})
    operator_corrections = len({(event["row_key"], event["field"]) for event in audit
                                if event.get("operator") != "System"})
    cms = _history_metrics(status, "cms_integration")
    remaining = {
        "invalid_names_remaining": int(pipeline.mask_name_invalid(df).sum()),
        "invalid_dobs_remaining": int((~pipeline.compute_dob_validity(df)).sum()),
        "invalid_addresses_remaining": int(pipeline.mask_address_invalid(df).sum()),
        "missing_phones_remaining": int(pipeline.mask_mobile_missing(df).sum()),
        "invalid_ids_remaining": int(pipeline.mask_id_only_invalid(df).sum()),
    }
    has_exceptions = any(remaining.values())
    return {
        "total_rows": int(len(df)),
        "names_corrected": names_corrected,
        "generated_ids_assigned": generated_ids,
        "operator_corrections": operator_corrections,
        "cms_matches": int(cms.get("matched_rows", 0)),
        "cms_unmatched": int(cms.get("unmatched_rows", 0)),
        "status": "COMPLETED WITH EXCEPTIONS" if has_exceptions else "PASSED",
        **remaining,
    }


def _validate_edit_items(df: pd.DataFrame, cfg: dict, edits: list[EditItem]):
    for edit in edits:
        if edit.field not in cfg["editable_cols"]:
            raise HTTPException(400, f"field '{edit.field}' is not editable at this stage")
        if edit.row_key not in df.index:
            raise HTTPException(400, f"row_key {edit.row_key} not found")


def _upload_metrics(df: pd.DataFrame, ref_df: pd.DataFrame) -> dict:
    base_keys = df["ACCOUNT_NUMBER"].astype(str).str.strip()
    reference_keys = ref_df["ACCOUNT_NUMBER"].astype(str).str.strip()
    matched = base_keys.isin(reference_keys)
    return {
        "matched_rows": int(matched.sum()),
        "matched_accounts": int(base_keys[matched].nunique()),
        "unmatched_rows": int((~matched).sum()),
    }
