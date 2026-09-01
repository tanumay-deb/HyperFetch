"""HyperFetch — multi-segment download accelerator.

Entry point: launches the desktop GUI and the localhost server the browser
extension talks to. Flags: ``--version``, ``--selftest`` (headless smoke check).
Headless queueing without a GUI lives in ``api_server.py``.
"""
import sys

import crash_reporter
from gui.theme import APP_VERSION


def _open_target(argv):
    """A .torrent path or magnet: link passed by Windows (file association)."""
    for a in argv[1:]:
        if a.startswith("-"):
            continue
        if a.lower().startswith("magnet:") or a.lower().endswith(".torrent"):
            return a
    return None


def _post_running(path, payload):
    """POST to an already-running instance's localhost server. Returns the
    decoded JSON reply, or None if nothing answered / the reply wasn't ours."""
    try:
        import json, urllib.request, utils
        from api_server import PORT          # not hardcoded: the two must agree
        tok = utils.get_or_create_token()
        payload = {**payload, "token": tok}
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-HyperFetch-Token": tok})
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode() or "{}")
    except Exception:
        return None


def _handoff(target):
    """Hand a .torrent/magnet target to a running instance so we don't open a
    second window. Returns True if a running app accepted it."""
    return _post_running("/open", {"target": target}) is not None


_MUTEX_NAME = "Local\\HyperFetch.SingleInstance"
_mutex_handle = None


def _claim_single_instance(name=None):
    """Take a process-wide lock, or report that another instance holds it.

    A Windows named mutex, deliberately NOT an HTTP check. The previous guard
    asked the running instance over its localhost server — but that server is
    exactly what fails when the port is already taken, and the app swallowed
    that failure. A crippled instance therefore looked like "nothing running",
    so a second window opened, and the two then fought over downloads.json.

    The kernel releases a mutex automatically when the owning process dies, so
    unlike a lock file this cannot go stale after a crash.

    Returns True if we own the app (caller proceeds), False if another live
    instance already does.
    """
    global _mutex_handle
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        handle = k32.CreateMutexW(None, True, name or _MUTEX_NAME)
        err = ctypes.get_last_error()
        ERROR_ALREADY_EXISTS = 183
        if not handle:
            return True                     # cannot lock -> fail open, never block startup
        if err == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False
        _mutex_handle = handle              # held for the life of the process
        _claim_installer_mutex(k32)
        return True
    except Exception:
        return True                         # non-Windows / unavailable -> fail open


_installer_mutex = None


def _claim_installer_mutex(k32):
    """Hold Global\\HyperFetch.Running purely so the INSTALLER can see us.

    Installing over a running app silently did nothing: Windows locks
    HyperFetch.exe, so Setup skipped it but still wrote the uninstall entry —
    the app reported the new version while the old binary kept running, and
    shipped fixes never arrived. installer.iss names this mutex in AppMutex, so
    Setup now detects the running app and closes it first.

    Global (not Local) because Setup runs elevated, potentially in another
    session. This is separate from the single-instance lock, which stays Local
    so instances are counted per user.
    """
    global _installer_mutex
    if _installer_mutex is not None:
        return
    try:
        h = k32.CreateMutexW(None, False, "Global\\HyperFetch.Running")
        if h:
            _installer_mutex = h
    except Exception:
        pass                                # never let this block startup


def _wait_for_exit(timeout=15.0):
    """Block until the running instance's server stops answering (it is going
    away), or `timeout` passes. Returns True if it went. Used by the restart
    path so the replacement does not race its own predecessor."""
    import time, urllib.request
    from api_server import PORT              # not hardcoded: the two must agree
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/ping", timeout=1):
                pass
        except Exception:
            return True                      # nothing answering -> it is gone
        time.sleep(0.25)
    return False


def _focus_running():
    """Single-instance guard: if a HyperFetch GUI is already running, ask it to
    come to the front and return True (this launch then exits instead of adding
    a duplicate window + tray icon). False when nothing is running, the port is
    held by something else, or a headless server answers (no window to raise —
    'no-gui' — so the GUI launch proceeds)."""
    reply = _post_running("/focus", {})
    return bool(reply) and reply.get("status") == "focused"


def main():
    """Console entry point (``hyperfetch``) and ``python main.py``."""
    if "--version" in sys.argv:
        print(f"HyperFetch {APP_VERSION}")
        return 0

    if "--selftest" in sys.argv:
        from gui2.app import _self_test_v2
        return _self_test_v2()

    # Windows opened us with a .torrent / magnet: — hand it to a running instance
    # if there is one; otherwise launch and add it after startup.
    target = _open_target(sys.argv)
    if target and _handoff(target):
        return 0

    # A second instance must NEVER be created while one is already running: the
    # two share downloads.json, so whichever saves last silently wipes the
    # other's downloads, and only the first owns port 5000 (so the browser
    # extension talks to a window the user may not even be looking at).
    #
    # This runs even when a target was given: the handoff above may have failed
    # (busy instance, transient error), and falling through to a full second
    # window was exactly how duplicates appeared — opening a .torrent by
    # double-click is the common path.
    restarted = "--restarted" in sys.argv
    # The authoritative check: a kernel mutex, which is true even when the
    # running instance's localhost server never came up. Ask it to surface (and
    # to take the file we were opened with) on a best-effort basis, but exit
    # either way — a duplicate window is what corrupts the shared state.
    if not restarted and not _claim_single_instance():
        _focus_running()
        if target:
            _handoff(target)
        return 0
    if restarted:
        # A theme-change restart legitimately expects the outgoing instance to
        # disappear. Wait for it rather than skipping the check outright — if it
        # never goes (a failed quit), focusing it beats duplicating it. The
        # mutex is not consulted here: the predecessor still holds it while it
        # shuts down, and this launch is its intended replacement.
        _wait_for_exit(timeout=15.0)
    # Whatever the launch reason, an instance that is still answering wins: a
    # duplicate window is worse than a restart that did not take effect, because
    # the two overwrite each other's downloads.json.
    if _focus_running():
        if target:
            # the earlier handoff failed but the instance is demonstrably alive
            # (it just answered /focus), so retry rather than silently dropping
            # the file the user double-clicked
            _handoff(target)
        return 0
    if restarted:
        _claim_single_instance()          # predecessor has gone; take ownership

    # install BEFORE the GUI so a Qt construction crash is captured too
    crash_reporter.install(APP_VERSION)

    from gui2.app import run_v2
    return run_v2(open_target=target, restarted=restarted)


if __name__ == "__main__":
    sys.exit(main())
