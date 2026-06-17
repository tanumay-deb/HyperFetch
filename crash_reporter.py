"""Local-only crash reporter.

Catches uncaught exceptions on the main thread AND worker threads, writes a
minimal JSON report (traceback + version + platform + cwd — NO user files,
URLs, headers, or paths beyond cwd) to ``%APPDATA%\\HyperFetch\\crashes\\``.
The GUI surfaces a one-line notice when unsent reports exist so the user can
open the folder and share them manually.

No network. No PII beyond the traceback the user's code already produced.
"""
import json
import os
import platform
import sys
import threading
import time
import traceback

import utils


def crashes_dir():
    d = os.path.join(utils.app_data_dir(), "crashes")
    os.makedirs(d, exist_ok=True)
    return d


def _write_report(exc_type, exc, tb, source, app_version):
    payload = {
        "ts": time.time(),
        "source": source,                       # "main" | "thread:<name>"
        "version": app_version,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "exc_type": exc_type.__name__,
        "exc_msg": str(exc)[:500],
        "traceback": "".join(traceback.format_exception(exc_type, exc, tb))[-8000:],
    }
    name = time.strftime("%Y%m%dT%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}.json"
    path = os.path.join(crashes_dir(), name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass  # the crash reporter must NEVER itself raise


def install(app_version):
    """Hook sys.excepthook and threading.excepthook to capture every crash.
    Safe to call once at app startup, before the GUI exists."""
    prev_sys = sys.excepthook
    prev_thr = getattr(threading, "excepthook", None)

    def sys_hook(exc_type, exc, tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            _write_report(exc_type, exc, tb, "main", app_version)
        if prev_sys:
            prev_sys(exc_type, exc, tb)

    def thr_hook(args):
        _write_report(args.exc_type, args.exc_value, args.exc_traceback,
                      f"thread:{args.thread.name if args.thread else '?'}",
                      app_version)
        if prev_thr:
            prev_thr(args)

    sys.excepthook = sys_hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = thr_hook


def unsent_reports():
    """Return the list of crash report file paths still on disk."""
    d = crashes_dir()
    try:
        return sorted(os.path.join(d, n) for n in os.listdir(d)
                      if n.endswith(".json"))
    except OSError:
        return []
