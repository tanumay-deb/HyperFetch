"""Quitting ends the process - from the tray too.

Qt ends an application by itself only when its last *visible* window closes.
From the tray the main window is already hidden, so closing it ended nothing:
the window and the tray icon went, and HyperFetch.exe stayed in Task Manager.

Each case runs the real window in a process of its own. In-process, the app's
background threads outlive the test - its local server retries its port for
20 s, rewriting web_auth.json in whichever test's app-data folder is current -
and that raced a later test into failing. A separate process is also the
honest check: the complaint was about a process that would not end.
"""
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = textwrap.dedent('''
    import json, os, sys
    mode, appdata = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(appdata, "HyperFetch"), exist_ok=True)
    with open(os.path.join(appdata, "HyperFetch", "settings.json"), "w") as f:
        json.dump({"welcomed": True}, f)          # no first-run dialog in the way
    sys.path.insert(0, sys.argv[3])
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from gui2.app import DownloadAppV2
    win = DownloadAppV2()
    win.show()
    if mode == "tray":
        win.hide()                                # where "Minimize to tray" leaves it
    QTimer.singleShot(0, win._real_quit)
    QTimer.singleShot(8000, lambda: os._exit(3))  # never ended by itself
    sys.exit(app.exec())
''')


@pytest.mark.parametrize("mode", ["tray", "window"])
def test_quit_ends_the_process(tmp_path, mode):
    script = tmp_path / "quit_case.py"
    script.write_text(SCRIPT, encoding="utf-8")
    appdata = tmp_path / ("appdata-%s" % mode)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(appdata),
               HOME=str(appdata))
    r = subprocess.run([sys.executable, str(script), mode, str(appdata), ROOT],
                       env=env, cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert r.returncode != 3, "HyperFetch kept running after Quit (%s)" % mode
    assert r.returncode == 0, r.stderr[-2000:]
