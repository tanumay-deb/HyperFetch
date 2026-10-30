"""Theme, accent and font size change with no restart (asked 2026-10-30).

Widgets fix their colours and sizes as they are built, so a theme change
restarted the app, and every download stopped with it - one from a server
that cannot resume started over from nothing. Now the window builds its
widgets again (DownloadAppV2._reskin) and nothing else stops.

The real window, in a process of its own (as tests/test_tray_quit.py does),
with a download running from a local server, the local server of the app
stubbed out.
"""
import json
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = textwrap.dedent('''
    import json, os, sys, time
    root, url = sys.argv[1], sys.argv[2]
    sys.path.insert(0, root)
    from PySide6.QtWidgets import QApplication, QDialog
    from PySide6.QtGui import QShortcut
    from PySide6.QtCore import QCoreApplication, QEvent, QProcess
    import shiboken6
    app = QApplication([])
    quits, spawned = [], []
    QApplication.quit = staticmethod(lambda: quits.append(1))
    QProcess.startDetached = staticmethod(lambda *a, **k: spawned.append(a))
    import gui2.app as appmod
    import task as T
    from gui2 import palette
    from gui2.dialogs.settings import SettingsDialogV2
    appmod.run_server = lambda *a, **k: None          # no local server
    win = appmod.DownloadAppV2()
    win.show()

    def pump(secs, until=None):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            app.processEvents()
            # what the event loop does with deleteLater
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            if until and until():
                return True
            time.sleep(0.02)
        return bool(until and until())

    win._add_download(url, "big.bin", None, ask=False)
    t = next(x for x in win.queue.tasks if x.url == url)
    pump(15, lambda: t.status == T.DOWNLOADING and t.downloaded > 0)
    # what the window shows
    win.pills["Active"].click()
    win.search.setText("big")
    win.list.set_selection({t.id})
    win.drawer.open_for(t)
    win.drawer.tabs.setCurrentIndex(2)
    win.sidebar.set_update("Update to 9.9.9")
    win._toggle_sidebar()
    pump(0.5)
    old = [win.sidebar, win.list, win.drawer, win.status_bar]
    theme, queue, at = palette.active_theme(), win.queue, t.downloaded

    win.status_bar.theme_btn.click()
    pump(0.5)
    out = {
        "theme": [theme, palette.active_theme()],
        "restarted": bool(quits or spawned),
        "old_gone": not any(shiboken6.isValid(w) for w in old),
        "new_colours": palette.COLORS["surface"] in win.status_bar.styleSheet(),
        "same_queue": win.queue is queue,
        "running": [t.status == T.DOWNLOADING, t.pause_requested],
        "filter": [win._filter, win.pills["Active"].isChecked()],
        "search": win.search.text(),
        "selected": sorted(win.list.selected_ids()) == [t.id],
        "drawer": [win.drawer.isVisible(), win.drawer._tid == t.id,
                   win.drawer.tabs.currentIndex()],
        "update": win.sidebar._update_text,
        "sidebar": win.sidebar.width(),
        "keys": [sum(s.key().toString() == "Ctrl+K" for s in win.findChildren(QShortcut)),
                 len(win.list.findChildren(QShortcut))],
    }

    def answer(self):                                  # Settings: Large, green
        self.font_size.setCurrentText("Large")
        self._accent = "green"
        return QDialog.Accepted

    SettingsDialogV2.exec = answer
    side = win.sidebar
    win._open_settings()
    pump(0.5)
    out["settings"] = [palette.FONT_SCALE, palette.COLORS["accent"],
                       win.sidebar is not side]
    out["finished"] = pump(30, lambda: t.status == T.COMPLETED)
    out["went_on"] = t.downloaded > at
    print("RESULT " + json.dumps(out), flush=True)
    win.queue.shutdown()
    os._exit(0)
''')


def test_theme_accent_and_font_change_while_a_download_runs(tmp_path, file_server):
    file_server.put("big.bin", 4 << 20)
    appdata = tmp_path / "appdata"
    (appdata / "HyperFetch").mkdir(parents=True)
    (appdata / "HyperFetch" / "settings.json").write_text(json.dumps({
        "welcomed": True, "theme": "dark", "save_dir": str(tmp_path / "dl"),
        "global_speed_limit": 1 << 20,                  # 4 s: the switch lands mid-way
        "auto_update_check": False}), encoding="utf-8")
    (tmp_path / "dl").mkdir()
    script = tmp_path / "reskin.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(appdata),
               HOME=str(appdata))
    r = subprocess.run([sys.executable, str(script), ROOT, file_server.url("big.bin")],
                       env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, r.stdout[-2000:] + r.stderr[-3000:]
    out = json.loads(line[len("RESULT "):])
    assert out["theme"] == ["dark", "light"]
    assert not out["restarted"]
    assert out["old_gone"] and out["new_colours"]
    assert out["same_queue"] and out["running"] == [True, False], out
    assert out["filter"] == ["Active", True]
    assert out["search"] == "big" and out["selected"]
    assert out["drawer"] == [True, True, 2]
    assert out["update"] == "Update to 9.9.9"
    assert out["sidebar"] == 72
    assert out["keys"] == [1, 4]
    assert out["settings"] == [1.2, "#22c55e", True]
    assert out["finished"] and out["went_on"], out
