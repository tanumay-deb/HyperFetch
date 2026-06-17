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
    import main
    w = main.DownloadApp()
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
    import main
    t = _fake_task(1, T.DOWNLOADING)
    win.queue.tasks.append(t)
    win.refresh()
    idx = win.model.index(0, 0)
    assert win.model.data(idx, main.TaskTableModel.TASK_ROLE) is t
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
    import main
    dlg = main.SettingsDialog(None, "C:/dl", 3, 8, verify_tls=False, pair_token="TKN")
    d, conc, segs, verify, theme = dlg.values()
    assert conc == 3 and segs == 8 and verify is False
    assert theme == "dark"
    assert dlg.token_edit.text() == "TKN"


def test_light_theme_builds(qapp):
    import main
    main.apply_theme("light")
    try:
        qss = main.build_qss()
        assert isinstance(qss, str) and main.BG == main.LIGHT["bg"]
        g = main.SpeedGraphWidget(max_points=20)
        g.add_value(5)
        g.grab()                       # paintEvent under light palette must not raise
    finally:
        main.apply_theme("dark")       # restore default for other tests


def test_hover_color_defined():
    import main
    assert hasattr(main, "HOVER") and main.HOVER.startswith("#")


def test_speed_graph_capped(qapp):
    import main
    g = main.SpeedGraphWidget(max_points=50)
    for i in range(200):
        g.add_value(i)
    assert len(g.data) == 50
    g.grab()   # paintEvent must not raise


def test_sweep_orphan_temps_removes_only_unknown_hfdownload(qapp, tmp_path, monkeypatch):
    """Startup sweep deletes leftover .hfdownload files in the save dir, but
    NOT ones that belong to a persisted task or files unrelated to HyperFetch."""
    import os
    import main
    save_dir = tmp_path
    (save_dir / "ghost.zip.hfdownload").write_bytes(b"orphan")
    (save_dir / "Programs").mkdir()
    (save_dir / "Programs" / "ghost.exe.hfdownload").write_bytes(b"orphan2")
    (save_dir / "Programs" / "known.exe.hfdownload").write_bytes(b"keep")
    (save_dir / "unrelated.txt").write_bytes(b"leave me alone")

    w = main.DownloadApp()
    try:
        w.save_dir = str(save_dir)
        # one task already persisted whose temp file exists on disk
        t = T.DownloadTask("u", str(save_dir / "Programs" / "known.exe"),
                           filename="known.exe")
        w.queue.tasks.append(t)
        w._sweep_orphan_temps()
        assert not (save_dir / "ghost.zip.hfdownload").exists()
        assert not (save_dir / "Programs" / "ghost.exe.hfdownload").exists()
        assert (save_dir / "Programs" / "known.exe.hfdownload").exists()
        assert (save_dir / "unrelated.txt").exists()
    finally:
        try: w.queue.shutdown()
        except Exception: pass
