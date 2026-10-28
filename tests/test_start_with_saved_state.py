"""The window starts with what a real user's settings hold.

2.8.2 died on start for everybody who had used it before: a saved filter
("last_filter") makes __init__ call _set_filter, which calls refresh(), and
refresh() read self._moves two lines before __init__ made it -
"'DownloadAppV2' object has no attribute '_moves'". The suite and CI's
--selftest start from fresh settings, which never take that path, so both
passed.

The real window, in a process of its own (as tests/test_tray_quit.py does),
with the settings a user who has closed the app once has, the local server
stubbed out.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = textwrap.dedent('''
    import os, sys
    root = sys.argv[1]
    sys.path.insert(0, root)
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    import gui2.app as appmod
    appmod.run_server = lambda *a, **k: None          # no local server
    win = appmod.DownloadAppV2()
    win.show(); app.processEvents(); win.refresh()
    print("STARTED", win._filter, flush=True)
    win.queue.shutdown()
    os._exit(0)
''')


@pytest.mark.parametrize("saved", [
    {"last_filter": "Downloading"},
    {"last_filter": "Completed", "sidebar_collapsed": True, "font_size": "Large",
     "keep_awake": True, "hls_to_mp4": True},
])
def test_the_window_starts_with_a_used_settings_file(tmp_path, saved):
    appdata = tmp_path / "appdata"
    (appdata / "HyperFetch").mkdir(parents=True)
    (appdata / "HyperFetch" / "settings.json").write_text(
        json.dumps(dict(saved, welcomed=True)), encoding="utf-8")
    script = tmp_path / "start.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(appdata),
               HOME=str(appdata))
    r = subprocess.run([sys.executable, str(script), ROOT], env=env, cwd=ROOT,
                       capture_output=True, text=True, timeout=90)
    assert "STARTED " + saved["last_filter"] in r.stdout, r.stderr[-2000:]
