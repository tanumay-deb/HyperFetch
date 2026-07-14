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
        tok = utils.get_or_create_token()
        payload = {**payload, "token": tok}
        req = urllib.request.Request(
            f"http://127.0.0.1:5000{path}",
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

    # Plain launch with an instance already running: pop its window instead of
    # opening a duplicate (which would also lose the port-5000 server to the
    # first instance and confuse the browser extension).
    if not target and _focus_running():
        return 0

    # install BEFORE the GUI so a Qt construction crash is captured too
    crash_reporter.install(APP_VERSION)

    from gui2.app import run_v2
    return run_v2(open_target=target)


if __name__ == "__main__":
    sys.exit(main())
