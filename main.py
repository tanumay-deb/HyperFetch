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


def _handoff(target):
    """Hand the target to an already-running instance via its localhost server so
    we don't open a second window. Returns True if a running app accepted it."""
    try:
        import json, urllib.request, utils
        tok = utils.get_or_create_token()
        req = urllib.request.Request(
            "http://127.0.0.1:5000/open",
            data=json.dumps({"target": target, "token": tok}).encode(),
            headers={"Content-Type": "application/json", "X-HyperFetch-Token": tok})
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


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

    # install BEFORE the GUI so a Qt construction crash is captured too
    crash_reporter.install(APP_VERSION)

    from gui2.app import run_v2
    return run_v2(open_target=target)


if __name__ == "__main__":
    sys.exit(main())
