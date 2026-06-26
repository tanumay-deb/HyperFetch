"""HyperFetch — multi-segment download accelerator.

Entry point: launches the desktop GUI and the localhost server the browser
extension talks to. Flags: ``--version``, ``--selftest`` (headless smoke check).
Headless queueing without a GUI lives in ``api_server.py``.
"""
import sys

import crash_reporter
from gui.theme import APP_VERSION

if __name__ == "__main__":
    if "--version" in sys.argv:
        print(f"HyperFetch {APP_VERSION}")
        sys.exit(0)

    if "--selftest" in sys.argv:
        from gui2.app import _self_test_v2
        sys.exit(_self_test_v2())

    # install BEFORE the GUI so a Qt construction crash is captured too
    crash_reporter.install(APP_VERSION)

    from gui2.app import run_v2
    sys.exit(run_v2())
