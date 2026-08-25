"""Checkpoint/rollback logic, split out of store.py.

Works entirely through store.py's own public functions (get_df, get_status,
set_df, set_status, persist) rather than reaching into its private _JOBS
dict directly -- store.py owns that state, this module just calls back into
it, the same as any other caller would. Imported lazily (inside each
function, not at module load time) to avoid a circular import: store.py
imports these functions at module level, so if this module imported store.py
back at module level too, whichever one loads first would find the other
half-initialized."""
import json
import shutil
import uuid
from copy import deepcopy

from .job_paths import job_dir


def create_checkpoint(job_id: str, label: str, stage_id: str | None = None) -> dict:
    """Save the current job state before a user-triggered pipeline action."""
    from . import store

    df = store.get_df(job_id)
    status = store.get_status(job_id)
    stage_index = status["stage_index"]
    stage = status["stages"][stage_index] if stage_index < len(status["stages"]) else {}
    checkpoint_id = f"cp-{len(status['checkpoints']) + 1:02d}-{uuid.uuid4().hex[:6]}"
    checkpoint_dir = job_dir(job_id) / "checkpoints" / checkpoint_id
    checkpoint_dir.mkdir(parents=True, exist_ok=False)

    df.to_parquet(checkpoint_dir / "working.parquet", index=False)
    checkpoint_status = deepcopy(status)
    (checkpoint_dir / "status.json").write_text(
        json.dumps(checkpoint_status, separators=(",", ":")), encoding="utf-8"
    )
    metadata = {
        "id": checkpoint_id,
        "label": label,
        "stage_id": stage_id or stage.get("id", ""),
        "stage_index": stage_index,
        "stage_title": stage.get("title", ""),
    }
    status["checkpoints"].append(metadata)
    store.persist(job_id)
    return metadata


def get_rollback_targets(job_id: str) -> list[dict]:
    """Return valid checkpoint metadata, including legacy snapshots without stage metadata."""
    from . import store

    status = store.get_status(job_id)
    targets = []
    for checkpoint in status.get("checkpoints", []):
        checkpoint_dir = job_dir(job_id) / "checkpoints" / checkpoint["id"]
        status_path = checkpoint_dir / "status.json"
        data_path = checkpoint_dir / "working.parquet"
        if not status_path.exists() or not data_path.exists():
            continue
        try:
            snapshot = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stage_index = snapshot.get("stage_index")
        stages = snapshot.get("stages", [])
        if not isinstance(stage_index, int) or not 0 <= stage_index < len(stages):
            continue
        stage = stages[stage_index]
        targets.append({
            "id": checkpoint["id"],
            "label": checkpoint.get("label", "Saved checkpoint"),
            "stage_id": stage.get("id", checkpoint.get("stage_id", "")),
            "stage_index": stage_index,
            "stage_title": stage.get("title", checkpoint.get("stage_title", "")),
        })
    return targets


def rollback_to_checkpoint(job_id: str, checkpoint_id: str) -> dict:
    """Restore a selected checkpoint and remove every checkpoint after it."""
    import pandas as pd

    from . import store

    status = store.get_status(job_id)
    checkpoints = status.get("checkpoints", [])
    checkpoint_index = next(
        (index for index, checkpoint in enumerate(checkpoints) if checkpoint.get("id") == checkpoint_id),
        None,
    )
    if checkpoint_index is None:
        raise ValueError("The selected rollback checkpoint is no longer available")

    metadata = checkpoints[checkpoint_index]
    checkpoint_dir = job_dir(job_id) / "checkpoints" / metadata["id"]
    status_path = checkpoint_dir / "status.json"
    data_path = checkpoint_dir / "working.parquet"
    if not status_path.exists() or not data_path.exists():
        raise ValueError("The latest rollback checkpoint is incomplete")

    restored_status = json.loads(status_path.read_text(encoding="utf-8"))
    restored_status["job_id"] = job_id
    # Downstream work was created from the state being replaced, so its
    # later restore points no longer apply. The audit log is untouched --
    # it's an append-only historical record (see job_audit.py), not part of
    # this snapshot, so rolling back doesn't erase the record of what
    # happened in the discarded work.
    restored_status["checkpoints"] = checkpoints[:checkpoint_index]
    restored_status.setdefault("drafts", {})
    store.set_df(job_id, pd.read_parquet(data_path))
    store.set_status(job_id, restored_status)
    for discarded in checkpoints[checkpoint_index:]:
        shutil.rmtree(job_dir(job_id) / "checkpoints" / discarded["id"], ignore_errors=True)
    store.persist(job_id)
    return metadata


def rollback_latest_checkpoint(job_id: str) -> dict:
    """Restore and consume the latest checkpoint so repeated rollback steps backwards."""
    from . import store

    checkpoints = store.get_status(job_id).get("checkpoints", [])
    if not checkpoints:
        raise ValueError("No rollback checkpoint is available for this job")
    return rollback_to_checkpoint(job_id, checkpoints[-1]["id"])
