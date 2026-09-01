"""Who did what on the users site.

Append-only JSONL, deliberately not the JSON store the accounts live in. This
file grows by a line per action, and rewriting a whole document for every
appended row is the wrong shape: it gets slower as it grows, and a crash
mid-write loses the lot rather than the last line.

One caveat worth knowing when reading it back: behind a tunnel every visitor
genuinely arrives as 127.0.0.1, because the tunnel runs on this machine. The
address column is recorded anyway for the LAN case, but **the account name is
the identifier that means anything**. `X-Forwarded-For` is set by the caller
and would be worse than useless here.
"""
import json
import logging
import os
import threading
import time

import utils

log = logging.getLogger("hyperfetch.audit")

# Rotated rather than trimmed, so the recent past is never lost to make room.
MAX_BYTES = 2 * 1024 * 1024
KEEP_ROTATIONS = 1

# The actions worth a line. Anything not listed is a bug rather than a silent
# omission, so `record` complains instead of writing a mystery.
ACTIONS = {
    "signin",         # a session began
    "signin-failed",  # a password was wrong
    "signup",         # an account was created
    "add",            # a download was queued
    "remove",         # a download and its files were deleted by its owner
    "download",       # a file was handed over
    "expire",         # retention removed something
}

_lock = threading.Lock()


def path():
    return os.path.join(utils.app_data_dir(), "site_audit.jsonl")


def record(action, user="", detail=None, addr=""):
    """Append one line. Never raises — an audit failure must not break a
    download, and the app has a log of its own to complain in."""
    if action not in ACTIONS:
        log.error("refusing to record an unknown audit action: %r", action)
        return False
    row = {
        "t": round(time.time(), 3),
        "action": action,
        "user": user or "",
        "addr": addr or "",
    }
    if detail:
        row["detail"] = detail
    line = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
    try:
        with _lock:
            _rotate_if_needed()
            with open(path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except OSError as e:
        log.warning("could not write the audit log (%s)", e)
        return False


def _rotate_if_needed():
    p = path()
    try:
        if os.path.getsize(p) < MAX_BYTES:
            return
    except OSError:
        return                      # missing is fine; it is about to be created
    for i in range(KEEP_ROTATIONS, 0, -1):
        older, newer = "%s.%d" % (p, i + 1), "%s.%d" % (p, i)
        if i == KEEP_ROTATIONS:
            try:
                os.remove(newer)
            except OSError:
                pass
        else:
            try:
                os.replace(newer, older)
            except OSError:
                pass
    try:
        os.replace(p, p + ".1")
    except OSError as e:
        log.warning("could not rotate the audit log (%s)", e)


def tail(limit=200, user=None):
    """The most recent entries, newest first.

    A damaged line is skipped rather than failing the read: a log you cannot
    open because one row is malformed is a log that does not do its job.
    """
    try:
        with open(path(), "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if user and row.get("user") != user:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def summary(days=30):
    """Per-account totals over a window, for the admin panel."""
    cutoff = time.time() - days * 86400
    by_user = {}
    for row in tail(limit=100000):
        if row.get("t", 0) < cutoff:
            break                   # newest first, so everything after is older
        u = row.get("user") or "-"
        rec = by_user.setdefault(u, {"added": 0, "downloaded": 0, "bytes": 0})
        if row["action"] == "add":
            rec["added"] += 1
        elif row["action"] == "download":
            rec["downloaded"] += 1
            rec["bytes"] += int((row.get("detail") or {}).get("size", 0) or 0)
    return by_user
