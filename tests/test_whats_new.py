"""The first start of a new version after an update shows that version's notes.

notes/<version>.md is shipped in the app (approach A of
specs/2026-11-14-whats-new-window-design.md) and is also the GitHub release's
text. A fresh install gets the Welcome window instead; --selftest, which runs
on the real app-data, must not use up the window.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

import utils
from updater import _parse_semver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("extras, shown", [
    ({}, False),                                          # fresh install: Welcome
    ({"welcomed": True}, True),                           # used 2.8.8, before this
    ({"welcomed": True, "last_version": "2.8.8"}, True),
    ({"welcomed": True, "last_version": "2.8.9"}, False),  # seen
    ({"welcomed": True, "last_version": "2.9.0"}, False),  # a downgrade
])
def test_due(extras, shown):
    from gui2.dialogs.whats_new import due
    assert due(extras, "2.8.9") is shown


def test_notes_for_a_version_with_none_is_none():
    from gui2.dialogs.whats_new import notes_for
    assert notes_for("0.0.1") is None


def test_2_8_9_has_notes():
    from gui2.dialogs.whats_new import notes_for
    text = notes_for("2.8.9")
    assert text and "1.9.6" in text and not text.lstrip().startswith("#")


def test_this_version_has_notes():
    """A release cannot go out without its notes: bumping APP_VERSION needs
    notes/<version>.md. 2.8.8 and before were released without."""
    if _parse_semver(utils.APP_VERSION) <= (2, 8, 8):
        pytest.skip("released before notes existed")
    from gui2.dialogs.whats_new import notes_for
    assert notes_for(utils.APP_VERSION), "notes/%s.md is missing" % utils.APP_VERSION


def test_notes_ship_in_both_builds():
    with open(os.path.join(ROOT, "HyperFetch.spec"), encoding="utf-8") as f:
        assert "('notes', 'notes')" in f.read()
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        assert '"share/hyperfetch/notes" = ["notes/*.md"]' in f.read()


# --- the real window, in a process of its own, no local server --------------

START = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, sys.argv[1])
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    import utils
    utils.APP_VERSION = sys.argv[3]
    import gui2.app as appmod
    from gui2.dialogs.whats_new import WhatsNewDialog
    from PySide6.QtCore import Qt
    appmod.run_server = lambda *a, **k: None
    if sys.argv[2] == "selftest":
        appmod._self_test_v2()
        sys.stdout.flush()
        os._exit(0)
    # Welcome is exec()'d: it would wait for a click forever
    appmod.DownloadAppV2._show_welcome = lambda self: print("WELCOME", flush=True)
    import time
    win = appmod.DownloadAppV2()
    win.show()
    end = time.time() + 2.5
    while time.time() < end:
        app.processEvents(); time.sleep(0.05)
    shown = [w for w in app.topLevelWidgets() if isinstance(w, WhatsNewDialog) and w.isVisible()]
    for w in shown:
        print("SHOWN", repr(w.windowTitle()), w.windowModality() == Qt.NonModal, flush=True)
    print("COUNT", len(shown), flush=True)
    win.queue.shutdown()
    os._exit(0)
''')


def _start(tmp_path, settings, mode="window", version="2.8.9"):
    appdata = tmp_path / "appdata"
    (appdata / "HyperFetch").mkdir(parents=True, exist_ok=True)
    path = appdata / "HyperFetch" / "settings.json"
    if settings is not None:
        path.write_text(json.dumps(settings), encoding="utf-8")
    script = tmp_path / "start.py"
    script.write_text(START, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(appdata), HOME=str(appdata))
    r = subprocess.run([sys.executable, str(script), ROOT, mode, version], env=env, cwd=ROOT,
                       capture_output=True, text=True, timeout=90)
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return r, saved


def test_an_update_shows_its_notes_once(tmp_path):
    r, saved = _start(tmp_path, {"welcomed": True})
    assert "SHOWN \"What's new in 2.8.9\" True" in r.stdout, r.stdout + r.stderr[-2000:]
    assert saved.get("last_version") == "2.8.9"
    r, _ = _start(tmp_path, None)                  # the same settings, started again
    assert "COUNT 0" in r.stdout, r.stdout + r.stderr[-2000:]


def test_a_fresh_install_gets_welcome_only(tmp_path):
    r, saved = _start(tmp_path, None)
    assert "WELCOME" in r.stdout and "COUNT 0" in r.stdout, r.stdout + r.stderr[-2000:]
    assert saved.get("last_version") == "2.8.9"


def test_the_selftest_uses_nothing_up(tmp_path):
    r, saved = _start(tmp_path, {"welcomed": True, "last_version": "2.8.7"}, mode="selftest")
    assert "selftest OK" in r.stdout, r.stdout + r.stderr[-2000:]
    assert saved.get("last_version") == "2.8.7"


def test_the_selftest_fails_a_build_without_this_versions_notes(tmp_path):
    """CI runs the BUILT app's --selftest: a build that lost notes/ (a spec
    change) would never show the window, and nothing else would fail."""
    r, _ = _start(tmp_path, {"welcomed": True}, mode="selftest", version="9.9.9")
    assert "notes/9.9.9.md is missing" in r.stdout, r.stdout + r.stderr[-2000:]
    assert "selftest OK" not in r.stdout
