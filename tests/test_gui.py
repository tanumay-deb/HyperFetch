"""GUI smoke tests (offscreen Qt). Skips cleanly if PySide6 can't init."""
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication   # noqa: E402
from PySide6.QtCore import Qt                 # noqa: E402

import task as T   # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp, monkeypatch):
    from gui.main_window import DownloadApp
    w = DownloadApp()
    yield w
    try:
        w.queue.shutdown()
    except Exception:
        pass


def _fake_task(fid, status, total=1000, done=0, fname=None):
    t = T.DownloadTask("https://x/" + (fname or f"f{fid}.zip"),
                       f"C:/t/{fname or f'f{fid}.zip'}",
                       filename=fname or f"f{fid}.zip")
    t.total_size = total
    t.downloaded = done
    t.status = status
    return t


def test_constructs_and_empty_state(win):
    win.refresh()
    assert win.model.rowCount() == 0
    # window isn't shown in tests, so isVisible() is False up the hierarchy;
    # isHidden() reflects the explicit show/hide intent set by refresh()
    assert not win.empty.isHidden()


def test_filters(win):
    states = [T.DOWNLOADING, T.COMPLETED, T.PAUSED, T.QUEUED, T.ERROR]
    for i, s in enumerate(states):
        win.queue.tasks.append(_fake_task(i, s))
    win.refresh()
    assert win.model.rowCount() == 5
    win._set_filter("Active")              # _set_filter refreshes the model
    assert win.model.rowCount() == 2       # downloading + queued
    win._set_filter("Paused")
    assert win.model.rowCount() == 1       # paused (no longer hidden under Active)
    win._set_filter("Done")
    assert win.model.rowCount() == 2       # completed + error
    win._set_filter("All")


def test_multiselect_pause(win):
    for i in range(3):
        win.queue.tasks.append(_fake_task(i, T.QUEUED))
    win.refresh()
    win.table.selectAll()
    win.on_pause()
    assert sum(1 for t in win.queue.tasks if t.status == T.PAUSED) == 3


def test_model_exposes_task_and_get_task(win):
    from gui.models import TaskTableModel
    t = _fake_task(1, T.DOWNLOADING)
    win.queue.tasks.append(t)
    win.refresh()
    idx = win.model.index(0, 0)
    assert win.model.data(idx, TaskTableModel.TASK_ROLE) is t
    assert win.queue.get_task(t.id) is t


def test_speed_cell_populates(win):
    t = _fake_task(1, T.DOWNLOADING, total=10_000_000, done=0)
    win.queue.tasks.append(t)
    win.refresh()                          # seed baseline
    t.downloaded = 2_000_000
    time.sleep(0.6)
    win.refresh()                          # compute delta
    speed_idx = win.model.index(0, 3)      # Speed column
    assert win.model.data(speed_idx) != ""


def test_settings_dialog_roundtrip(qapp):
    from gui.dialogs import SettingsDialog
    dlg = SettingsDialog(None, "C:/dl", 3, 8, verify_tls=False, pair_token="TKN")
    d, conc, segs, verify, theme, s_en, s_st, s_sp = dlg.values()
    assert d == "C:/dl"
    assert conc == 3
    assert segs == 8
    assert verify is False
    assert theme == "dark"
    assert dlg.token_edit.text() == "TKN"


def test_light_theme_builds(qapp):
    from gui import theme
    from gui.delegates import SpeedGraphWidget
    theme.apply_theme("light")
    try:
        qss = theme.build_qss()
        assert isinstance(qss, str) and theme.BG == theme.LIGHT["bg"]
        g = SpeedGraphWidget(max_points=20)
        g.add_value(5)
        g.grab()                       # paintEvent under light palette must not raise
    finally:
        theme.apply_theme("dark")       # restore default for other tests


def test_hover_color_defined():
    from gui import theme
    assert hasattr(theme, "HOVER") and theme.HOVER.startswith("#")


def test_speed_graph_capped(qapp):
    from gui.delegates import SpeedGraphWidget
    g = SpeedGraphWidget(max_points=50)
    for i in range(200):
        g.add_value(i)
    assert len(g.data) == 50
    g.grab()   # paintEvent must not raise


class _FakeTray:
    """Stand-in for QSystemTrayIcon so closeEvent's tray branch fires in tests
    (no real tray on a headless CI box)."""
    def __init__(self): self.messages = []
    def isVisible(self): return True
    def showMessage(self, *a): self.messages.append(a)


def _close_with_choice(win, button_text, monkeypatch):
    """Drive closeEvent with the tray dialog answering the button labelled
    `button_text`. QMessageBox.buttons() is role-sorted (not add-order), so we
    match by text, not index. Returns the QCloseEvent."""
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtGui import QCloseEvent
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(
        QMessageBox, "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text() == button_text))
    ev = QCloseEvent()
    win.closeEvent(ev)
    return ev


def test_close_event_minimize_to_tray(win, monkeypatch):
    win.tray = _FakeTray()
    win._quit_requested = False
    ev = _close_with_choice(win, "Minimize to Tray", monkeypatch)
    assert not ev.isAccepted()          # close vetoed -> stays running
    assert not win._quit_requested
    assert win.tray.messages             # showed the "minimized" toast once


def test_close_event_cancel_keeps_app(win, monkeypatch):
    win.tray = _FakeTray()
    win._quit_requested = False
    ev = _close_with_choice(win, "Cancel", monkeypatch)
    assert not ev.isAccepted()
    assert not win._quit_requested


def test_close_event_close_app_proceeds_to_shutdown(win, monkeypatch):
    win.tray = _FakeTray()
    win._quit_requested = False
    # no active tasks -> shutdown path runs cleanly to super().closeEvent
    ev = _close_with_choice(win, "Close App", monkeypatch)
    assert win._quit_requested
    assert ev.isAccepted()


def test_sweep_orphan_temps_removes_only_unknown_hfdownload(qapp, tmp_path, monkeypatch):
    """Startup sweep deletes leftover .hfdownload files in the save dir, but
    NOT ones that belong to a persisted task or files unrelated to HyperFetch."""
    import os
    import tempfile
    from gui.main_window import DownloadApp
    save_dir = tmp_path
    (save_dir / "ghost.zip.hfdownload").write_bytes(b"orphan")
    (save_dir / "Programs").mkdir()
    (save_dir / "Programs" / "ghost.exe.hfdownload").write_bytes(b"orphan2")
    (save_dir / "Programs" / "known.exe.hfdownload").write_bytes(b"keep")
    (save_dir / "unrelated.txt").write_bytes(b"leave me alone")

    w = DownloadApp()
    try:
        w.save_dir = str(save_dir)
        # one task already persisted whose temp file exists on disk
        t = T.DownloadTask("u", str(save_dir / "Programs" / "known.exe"),
                           filename="known.exe")
        w.queue.tasks.append(t)
        
        temp_dir = tempfile.gettempdir()
        known_temp = os.path.join(temp_dir, f"{t.id}.hfdownload")
        ghost_temp = os.path.join(temp_dir, "ghost.hfdownload")
        
        with open(known_temp, "wb") as f: f.write(b"keep")
        with open(ghost_temp, "wb") as f: f.write(b"orphan")
        
        w._sweep_orphan_temps()
        
        assert not (save_dir / "ghost.zip.hfdownload").exists()
        assert not (save_dir / "Programs" / "ghost.exe.hfdownload").exists()
        # legacy known is also deleted because we only keep from %TEMP%
        assert not (save_dir / "Programs" / "known.exe.hfdownload").exists()
        assert (save_dir / "unrelated.txt").exists()
        
        assert os.path.exists(known_temp)
        assert not os.path.exists(ghost_temp)
    finally:
        try: w.queue.shutdown()
        except Exception: pass
        try: os.remove(known_temp)
        except OSError: pass


def test_theme_switch_propagates_to_painter_modules(qapp):
    """apply_theme must update the color globals that delegates/dialogs read,
    not just gui.theme's own namespace. ACCENT is identical across palettes so
    we assert on MUTED/SEL which actually differ."""
    from gui import theme, delegates, dialogs
    try:
        theme.apply_theme("light")
        assert theme.MUTED == theme.LIGHT["muted"]
        assert delegates.MUTED == theme.LIGHT["muted"], "delegate color stale after switch"
        assert delegates.SEL == theme.LIGHT["sel"]
        assert dialogs.MUTED == theme.LIGHT["muted"], "dialog color stale after switch"
    finally:
        theme.apply_theme("dark")
        assert delegates.MUTED == theme.DARK["muted"], "switch back failed"


def test_queue_filter_shows_only_that_queue(win):
    main_q = _fake_task(1, T.QUEUED, fname="a.zip"); main_q.queue_name = "Main"
    other = _fake_task(2, T.QUEUED, fname="b.zip"); other.queue_name = "Side"
    win.queue.tasks.extend([main_q, other])
    win._set_filter("Queue:Main")
    vis = win._visible_tasks()
    assert main_q in vis and other not in vis
    win._set_filter("Queue:Side")
    vis = win._visible_tasks()
    assert other in vis and main_q not in vis


def test_theme_toggle_round_trips(win):
    """dark -> light -> dark must actually switch back; self.theme must track
    the applied palette (not a stale module global)."""
    from gui import theme
    win._apply_theme("light")
    assert win.theme == "light"
    assert theme.THEME == "light"
    win._apply_theme("dark")
    assert win.theme == "dark", "theme did not switch back to dark"
    assert theme.THEME == "dark"
