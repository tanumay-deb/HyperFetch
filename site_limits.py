"""What a site account is allowed to consume.

Three separate limits, deliberately not one:

- **Free space** protects the machine. It applies to everybody, admin included,
  because a full disk breaks the desktop app just as surely as it breaks the
  site.
- **Quota** protects the other accounts from any one of them.
- **Retention** stops storage growing without bound when nobody is watching.

They are pure functions over paths and task lists so the rules can be tested
without a queue, a server or a disk full of real downloads.
"""
import logging
import os
import shutil
import time

import task as T

log = logging.getLogger("hyperfetch.limits")

# Downloads queued through the site land here rather than in Main, so site
# traffic can never take every slot from the desktop app. QueueManager creates
# a queue on first use, so nothing has to exist up front.
WEB_QUEUE = "Web"
WEB_QUEUE_CONCURRENT = 3

DEFAULT_QUOTA = 2 * 1024 ** 3          # 2 GB, editable per account
MAX_ACTIVE_PER_USER = 3

# Refuse new work below this much free space, for everyone. Not a quota — a
# safety valve, so no combination of accounts can fill the disk out from under
# the desktop app.
MIN_FREE = 20 * 1024 ** 3

RETENTION_DAYS = 30

# A torrent with more files than this is refused once its metadata arrives.
# At a 2 GB quota it was never going to fit anyway, and the listing has to stop
# somewhere.
MAX_TORRENT_FILES = 1000


def usage_bytes(base_dir, username):
    """Bytes on disk in one account's folder.

    Measured from the filesystem rather than summed from task records: the
    files are the thing that fills the disk, and a task list can disagree with
    them after a crash, a manual delete, or a download that wrote more than it
    reported.
    """
    import utils
    try:
        d = utils.user_download_dir(base_dir, username)
    except (ValueError, OSError):
        return 0
    if d == base_dir:
        return 0                        # admin is not metered
    total = 0
    for root, _dirs, names in os.walk(d):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass                    # vanished mid-walk; not worth failing over
    return total


def free_bytes(path):
    """Free space on the volume holding `path`, or 0 if it cannot be read."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def active_count(tasks, owner):
    """How many of this account's downloads are running or waiting to run."""
    live = (T.DOWNLOADING, T.QUEUED)
    return sum(1 for t in tasks
               if (getattr(t, "owner", "") or "") == owner and t.status in live)


def human(n):
    """Sizes for messages people read, not for logs."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024


def refusal(base_dir, owner, quota=DEFAULT_QUOTA, want_bytes=0):
    """Why this account cannot start another download, or "" if it can.

    A sentence meant for the person who hit it, following the same shape as
    `web_auth.lan_refusal` &mdash; a limit that silently does nothing is the
    worst version of a limit.

    Checked in this order on purpose: the machine first, then the account. If
    the disk is nearly full, saying "you are over quota" would send someone off
    to delete their own files when the real problem is elsewhere.
    """
    free = free_bytes(base_dir)
    if free and free < MIN_FREE:
        return ("This machine is low on disk space, so new downloads are "
                "paused for everyone. Only %s free." % human(free))

    if not owner:
        return ""                       # admin is not metered

    used = usage_bytes(base_dir, owner)
    if used >= quota:
        return ("You have used all %s of your space. Remove something to make "
                "room." % human(quota))
    if want_bytes and used + want_bytes > quota:
        return ("That is %s and you have %s left of your %s."
                % (human(want_bytes), human(quota - used), human(quota)))
    return ""


def over_quota(base_dir, owner, quota=DEFAULT_QUOTA):
    """True when an account has already exceeded its allowance.

    Used to stop *new* work, never to kill a running download: cancelling a
    transfer partway for crossing a line wastes everything it already fetched,
    and the overshoot is bounded by one file.
    """
    if not owner:
        return False
    return usage_bytes(base_dir, owner) >= quota


def expired(tasks, now=None, days=RETENTION_DAYS):
    """Site downloads that finished more than `days` ago.

    Admin's downloads are never returned. Deleting from the desktop app a month
    on would be a genuinely bad surprise, and retention exists to bound what the
    site accumulates, not to tidy up after the person who owns the machine.
    """
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    out = []
    for t in tasks:
        if not (getattr(t, "owner", "") or ""):
            continue
        if t.status != T.COMPLETED:
            continue
        done = float(getattr(t, "completed_at", 0) or 0) or float(getattr(t, "added", 0) or 0)
        if done and done < cutoff:
            out.append(t)
    return out


def days_left(t, now=None, days=RETENTION_DAYS):
    """Whole days before a download is removed, or None when it never is.

    The page shows this so files count down in view rather than disappearing
    without explanation.
    """
    if not (getattr(t, "owner", "") or "") or t.status != T.COMPLETED:
        return None
    now = time.time() if now is None else now
    done = float(getattr(t, "completed_at", 0) or 0) or float(getattr(t, "added", 0) or 0)
    if not done:
        return None
    return max(0, int((done + days * 86400 - now) // 86400))


def sweep(queue, save_dir, now=None, days=RETENTION_DAYS):
    """Delete site downloads past the retention window. Returns what went.

    Files first, then the task, so a crash between the two leaves a record
    pointing at nothing — which the UI already handles as "no longer here" —
    rather than a file nobody can reach through any record.

    Every path is checked to be inside the owner's folder before anything is
    removed. Retention is the one thing here that deletes on a timer with
    nobody watching, so it gets the same containment check as a request.
    """
    import site_audit
    from site_server import _delete_files

    removed = []
    for t in expired(list(queue.tasks), now=now, days=days):
        owner = getattr(t, "owner", "") or ""
        try:
            gone = _delete_files(t, save_dir, owner)
        except Exception:
            log.exception("could not clear %s for %s", t.filename, owner)
            continue
        try:
            queue.remove_task(t)
        except Exception:
            log.exception("could not drop %s from the queue", t.filename)
            continue
        removed.append((owner, t.filename, gone))
        site_audit.record("expire", owner,
                          {"name": t.filename, "files": gone, "days": days})
    if removed:
        log.info("retention removed %d download(s)", len(removed))
    return removed
