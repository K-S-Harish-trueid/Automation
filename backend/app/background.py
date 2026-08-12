import threading

from . import pipeline, store
from .helpers import _append_audit_events, _current_stage
from .pipeline.registry import HIDDEN_STAGE_IDS

# Toggle: while True, manual_edit stages are auto-advanced by the background
# runner itself (like an auto stage) instead of stopping and waiting for an
# operator -- the frontend never sees them. Flip to False to restore the
# normal interactive gate. Nothing behind this flag has been removed.
BYPASS_MANUAL_EDIT_STAGES = True


def _advance_with_progress(job_id: str):
    """Runs consecutive `auto` stages, reporting live progress after each one,
    and stops at the next gate (upload/manual_edit/confirm) or the terminal
    `done` stage. Must only be called from a background thread (raises plain
    exceptions on failure -- no request context to attach an HTTPException to)."""
    status = store.get_status(job_id)
    df = store.get_df(job_id)
    stages = status["stages"]
    total = len(stages)

    while status["stage_index"] < len(stages):
        idx = status["stage_index"]
        stage = stages[idx]

        if stage["type"] == "auto":
            # Hidden stages (registry.py's HIDDEN_STAGE_IDS) don't get their
            # name shown on the busy overlay either -- leaves whatever the
            # last visible step's label was on screen instead of flashing a
            # stage that isn't in the sidebar. The percent still advances
            # (below, after the handler runs) so progress doesn't stall.
            if stage["id"] not in HIDDEN_STAGE_IDS:
                store.set_progress(
                    job_id, status="processing", current_step_index=idx + 1,
                    total_steps=total, current_step_name=stage["title"],
                    percent=round(idx / total * 100),
                )
            handler = pipeline.AUTO_HANDLERS[stage["id"]]
            before = df.copy(deep=True)
            df, summary = handler(df)

            # The historical-override step (stage_replace_from_sql) returns
            # an unchanged df + a warning summary instead of raising when
            # historical.db is empty/unseeded -- rather than silently
            # continuing (easy to miss in the stage history), pause the
            # auto-chain here and surface it as a gate, same shape as an
            # upload/confirm stage, so the operator explicitly chooses to go
            # on. Checked directly against the real data (historical_db.has_
            # data()), not by pattern-matching stage_replace_from_sql's
            # display text -- that text is only ever shown to the operator,
            # never used for control flow, so rewording it later can't
            # silently break this pause. status["historical_ack"] is set by
            # the /continue-historical route once they acknowledge it, so
            # this only pauses once per job even though the handler re-runs
            # (idempotently) on resume.
            from .historical_db import has_data as _historical_has_data
            if stage["id"] == "replace" and not _historical_has_data() and not status.get("historical_ack"):
                status["pending_warning"] = {"stage_id": stage["id"], "title": stage["title"], "message": summary}
                store.set_df(job_id, df)
                store.persist(job_id)
                break

            auto_labels = {
                "clean": ("System corrected", "Line breaks were removed during initial cleaning."),
                "replace": ("Source-file updated", "Matched value supplied by the cached historical SQL store."),
                "reset_cms": ("System reset", "CMS fields were cleared before CMS integration."),
                "address_fix": ("System corrected", "Invalid address auto-filled from the province/Baghdad pool."),
            }
            label, reason = auto_labels.get(stage["id"], ("System corrected", "Automated pipeline update."))
            # "replace" pulls from the historical SQL cache, not the job's own
            # source file -- the audit trail should say so, not attribute the
            # value to whatever the operator originally uploaded.
            source_file = "Historical SQL store" if stage["id"] == "replace" else status.get("filename", "Source file")
            _append_audit_events(
                status, before, df, stage_id=stage["id"], label=label, reason=reason,
                source_file=source_file, operator="System",
            )
            stage["status"] = "done"
            status["history"].append({"stage_id": stage["id"], "title": stage["title"], "summary": summary})
            status["stage_index"] += 1
            store.set_df(job_id, df)
            store.persist(job_id)
            store.set_progress(job_id, percent=round((idx + 1) / total * 100))
            continue

        if stage["type"] == "done":
            stage["status"] = "done"
            store.set_progress(
                job_id, current_step_index=total, total_steps=total,
                current_step_name=stage["title"], percent=100,
            )
            break

        if stage["type"] == "manual_edit" and BYPASS_MANUAL_EDIT_STAGES:
            store.set_progress(
                job_id, status="processing", current_step_index=idx + 1,
                total_steps=total, current_step_name=stage["title"],
                percent=round(idx / total * 100),
            )
            cfg = pipeline.MANUAL_STAGES[stage["id"]]
            remaining = int(cfg["validator"](df).sum())
            stage["status"] = "done"
            status["history"].append({
                "stage_id": stage["id"], "title": stage["title"],
                "summary": f"Manual review bypassed (temporarily disabled). {remaining} row(s) left unresolved.",
                "metrics": {"remaining_flagged": remaining},
            })
            status["stage_index"] += 1
            store.persist(job_id)
            store.set_progress(job_id, percent=round((idx + 1) / total * 100))
            continue

        stage["status"] = "current"
        break

    store.set_df(job_id, df)
    store.persist(job_id)


def _run_in_background(job_id: str, resolve_gate):
    """Applies `resolve_gate()` (the gate-specific action -- upload merge,
    manual edits, or a confirm handler; a no-op for a brand-new job) and then
    runs the auto-stage chain, all in a background thread. Reports progress
    throughout via store.set_progress, per BACKEND_REQUIREMENTS.md, and only
    flips status away from "processing" after all state is persisted."""

    def _bg():
        try:
            resolve_gate()
            store.persist(job_id)
            _advance_with_progress(job_id)

            final_status = store.get_status(job_id)
            final_stage = _current_stage(final_status)
            final = "done" if (final_stage is None or final_stage["type"] == "done") else "idle"
            if final == "done":
                store.set_progress(
                    job_id, status=final, current_step_index=len(final_status["stages"]),
                    total_steps=len(final_status["stages"]), current_step_name="Final Output",
                    percent=100,
                )
            else:
                store.set_progress(job_id, status=final)
            if final == "done":
                # This job just became the newest completed one, so it's never
                # the one pruned here -- only older finished jobs are at risk.
                store.enforce_job_retention()
        except Exception as e:
            store.set_progress(job_id, status="error", message=str(e))
        finally:
            store.end_processing(job_id)

    threading.Thread(target=_bg, daemon=True).start()
