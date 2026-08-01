"""Upload cap, and the shut-down-when-finished countdown.

The countdown is the safety mechanism, not a decoration: it turns off someone's
computer, so every path out of the dialog that is not an explicit "do it" must
leave the machine running.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

import torrent
import utils
from gui2.app_settings import _parse_rate
from gui2.dialogs.shutdown import ShutdownDialog, power_command


def _app():
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------- upload cap
def test_rate_parsing():
    assert _parse_rate("Unlimited") == 0
    assert _parse_rate("500 KB/s") == 500 * 1024
    assert _parse_rate("2 MB/s") == 2 * 1024 * 1024
    assert _parse_rate(None) == 0
    assert _parse_rate("nonsense") == 0


def test_upload_cap_reaches_aria2(monkeypatch):
    monkeypatch.setattr(utils, "MAX_UPLOAD_BPS", 250 * 1024, raising=False)
    assert f"--max-overall-upload-limit={250 * 1024}" in torrent.preference_opts()


def test_unlimited_passes_no_flag(monkeypatch):
    monkeypatch.setattr(utils, "MAX_UPLOAD_BPS", 0, raising=False)
    assert not any("upload-limit" in o for o in torrent.preference_opts())


def test_the_cap_applies_even_without_seeding(monkeypatch):
    """A torrent uploads to peers while it is still downloading, and that is the
    traffic most likely to choke an asymmetric line."""
    monkeypatch.setattr(utils, "SEED_ENABLED", False, raising=False)
    monkeypatch.setattr(utils, "MAX_UPLOAD_BPS", 100 * 1024, raising=False)
    o = torrent.preference_opts()
    assert "--seed-time=0" in o
    assert f"--max-overall-upload-limit={100 * 1024}" in o


# ---------------------------------------------------------- power commands
@pytest.mark.skipif(sys.platform != "win32", reason="Windows power commands")
def test_the_power_commands_are_what_they_claim():
    assert power_command("Shut down")[:2] == ["shutdown", "/s"]
    assert "SetSuspendState" in " ".join(power_command("Sleep"))
    assert power_command("Do nothing") is None


# -------------------------------------------------------------- countdown
def test_cancel_does_not_run_anything(monkeypatch):
    app = _app()
    host = QWidget()
    fired = []
    monkeypatch.setattr("gui2.dialogs.shutdown.subprocess.Popen",
                        lambda *a, **k: fired.append(a))
    dlg = ShutdownDialog(host, action="Shut down", seconds=5)
    dlg.reject()
    app.processEvents()
    assert fired == [], "cancelling still powered the machine off"


def test_closing_the_window_is_a_cancel(monkeypatch):
    """Closing must not read as consent."""
    app = _app()
    host = QWidget()
    fired = []
    monkeypatch.setattr("gui2.dialogs.shutdown.subprocess.Popen",
                        lambda *a, **k: fired.append(a))
    dlg = ShutdownDialog(host, action="Shut down", seconds=5)
    dlg.close()
    app.processEvents()
    assert fired == []


def test_a_cancelled_countdown_cannot_fire_later(monkeypatch):
    """A timer tick arriving after the cancel must not resurrect it."""
    app = _app()
    host = QWidget()
    fired = []
    monkeypatch.setattr("gui2.dialogs.shutdown.subprocess.Popen",
                        lambda *a, **k: fired.append(a))
    dlg = ShutdownDialog(host, action="Shut down", seconds=1)
    dlg.reject()
    dlg._tick()
    dlg._tick()
    app.processEvents()
    assert fired == []


def test_it_does_fire_when_the_countdown_runs_out(monkeypatch):
    app = _app()
    host = QWidget()
    fired = []
    monkeypatch.setattr("gui2.dialogs.shutdown.subprocess.Popen",
                        lambda *a, **k: fired.append(a))
    dlg = ShutdownDialog(host, action="Shut down", seconds=0)
    dlg._tick()
    app.processEvents()
    assert len(fired) == 1, "the countdown expired without doing anything"


def test_it_fires_only_once(monkeypatch):
    app = _app()
    host = QWidget()
    fired = []
    monkeypatch.setattr("gui2.dialogs.shutdown.subprocess.Popen",
                        lambda *a, **k: fired.append(a))
    dlg = ShutdownDialog(host, action="Shut down", seconds=0)
    dlg._fire()
    dlg._fire()
    dlg._tick()
    assert len(fired) == 1


# ------------------------------------------------------- when it arms at all
def _stub_window(tasks, wc="Shut down PC"):
    """Just enough window for _maybe_power_off, without building the real one."""
    import types
    from gui2.app import DownloadAppV2
    shown = []
    stub = types.SimpleNamespace(
        _power_dlg=None,
        queue=types.SimpleNamespace(tasks=tasks),
        _extras={"when_complete": wc},
        _show_from_tray=lambda: shown.append(True),
        styleSheet=lambda: "",
    )
    return stub, DownloadAppV2, shown


def test_it_waits_for_the_whole_queue_not_the_first_file(monkeypatch):
    """Powering off after the first of forty downloads would be absurd."""
    import task as T
    _app()
    busy = T.DownloadTask("https://x/a.zip", "a.zip", filename="a")
    busy.status = T.DOWNLOADING
    fin = T.DownloadTask("https://x/b.zip", "b.zip", filename="b")
    fin.status = T.COMPLETED
    stub, cls, _ = _stub_window([busy, fin])

    armed = []
    monkeypatch.setattr("gui2.dialogs.shutdown.ShutdownDialog",
                        lambda *a, **k: armed.append(True))
    cls._maybe_power_off(stub, "Shut down PC")
    assert armed == [], "armed while a download was still running"


def test_a_paused_download_does_not_block_it_forever(monkeypatch):
    """One forgotten pause would otherwise disable the feature permanently."""
    import task as T
    app = _app()
    paused = T.DownloadTask("https://x/a.zip", "a.zip", filename="a")
    paused.status = T.PAUSED
    fin = T.DownloadTask("https://x/b.zip", "b.zip", filename="b")
    fin.status = T.COMPLETED
    stub, cls, shown = _stub_window([paused, fin])

    made = []

    class _Dlg:
        def __init__(self, *a, **k):
            made.append(k.get("action"))
            self.finished = types_signal()

        def show(self): pass
        def raise_(self): pass
        def activateWindow(self): pass

    import types as _t

    def types_signal():
        return _t.SimpleNamespace(connect=lambda *a, **k: None)

    monkeypatch.setattr("gui2.dialogs.shutdown.ShutdownDialog", _Dlg)
    cls._maybe_power_off(stub, "Shut down PC")
    app.processEvents()
    assert made == ["Shut down"]
    assert shown, "countdown was armed without bringing the window forward"


def test_it_does_not_stack_two_countdowns(monkeypatch):
    import task as T
    _app()
    fin = T.DownloadTask("https://x/b.zip", "b.zip", filename="b")
    fin.status = T.COMPLETED
    stub, cls, _ = _stub_window([fin])
    stub._power_dlg = object()          # one already running
    armed = []
    monkeypatch.setattr("gui2.dialogs.shutdown.ShutdownDialog",
                        lambda *a, **k: armed.append(True))
    cls._maybe_power_off(stub, "Shut down PC")
    assert armed == []
