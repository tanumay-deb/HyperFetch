"""Completed-download history + simple stats, persisted to ``history.json`` in
the app-data dir.

Records are appended when a download finishes (idempotent, deduped by task id);
the v2 History dialog reads them for the stats dashboard. This is separate from
``downloads.json`` (the live queue) so clearing/finishing the queue never loses
the historical totals.
"""
import os
import time

import utils

_MAX = 2000  # cap stored records to keep the file small


def _path():
    return os.path.join(utils.app_data_dir(), "history.json")


def load():
    data = utils.load_json(_path(), [])
    return data if isinstance(data, list) else []


def _save(records):
    utils.save_json(_path(), records[-_MAX:])


def record(task):
    """Append one completed task. Deduped by task id, so calling it again for
    an already-recorded download is a no-op."""
    recs = load()
    if any(r.get("id") == task.id for r in recs):
        return
    try:
        size = int(getattr(task, "total_size", 0) or getattr(task, "downloaded", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    recs.append({
        "id": task.id,
        "filename": task.filename or "",
        "url": getattr(task, "url", "") or "",
        "size": size,
        "category": utils.category_for(task.filename or ""),
        "path": getattr(task, "save_path", "") or "",
        "completed_at": time.time(),
    })
    _save(recs)


def clear():
    utils.save_json(_path(), [])


def stats():
    """Aggregate totals for the dashboard."""
    recs = load()
    total = sum(int(r.get("size", 0) or 0) for r in recs)
    by_cat = {}
    for r in recs:
        c = r.get("category", "Other") or "Other"
        by_cat[c] = by_cat.get(c, 0) + 1
    return {"count": len(recs), "total_bytes": total, "by_category": by_cat}
