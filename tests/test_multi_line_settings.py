"""Settings -> Network picks the internet connections a download is spread
over; the window puts them in utils.LINES; the drawer shows connections per
line (specs/2026-11-13-multi-line-downloads-design.md)."""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

import lines
import task as T
import utils

COMMON = dict(save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
              theme="Dark", accent="purple", sched_en=False, sched_start="00:00",
              sched_stop="06:00")
WIFI = ("Wi-Fi", "192.168.1.5", "192.168.1.1")
PHONE = ("Phone", "172.20.10.2", "172.20.10.1")
CABLE = ("Ethernet", "192.168.1.9", "192.168.1.1")


def _dialog(monkeypatch, found, extras):
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(lines, "available", lambda: list(found))
    return SettingsDialogV2(None, extras=extras, queues=["Main"], **COMMON)


def test_switch_and_ticks_reach_values(monkeypatch):
    dlg = _dialog(monkeypatch, [WIFI, PHONE], {})
    try:
        dlg.multi_line.setChecked(True)
        dlg._line_ticks["Phone"].setChecked(False)
        v = dlg.values()
        assert v["multi_line"] is True and v["lines"] == ["Wi-Fi"]
    finally:
        dlg.deleteLater()


def test_first_switch_on_ticks_every_line(monkeypatch):
    dlg = _dialog(monkeypatch, [WIFI, PHONE], {})
    try:
        assert dlg.values()["multi_line"] is False          # off by default
        assert dlg.values()["lines"] == ["Wi-Fi", "Phone"]
    finally:
        dlg.deleteLater()


def test_a_saved_line_not_found_now_is_kept(monkeypatch):
    dlg = _dialog(monkeypatch, [WIFI], {"multi_line": True, "lines": ["Phone"]})
    try:
        v = dlg.values()
        assert "Phone" in v["lines"] and "Wi-Fi" not in v["lines"]
    finally:
        dlg.deleteLater()


def test_same_router_is_said(monkeypatch):
    dlg = _dialog(monkeypatch, [WIFI, CABLE], {})
    try:
        assert "same router as Wi-Fi" in dlg._line_ticks["Ethernet"].text()
        assert "same router" not in dlg._line_ticks["Wi-Fi"].text()
    finally:
        dlg.deleteLater()


@pytest.mark.parametrize("on, want", [(True, ["Wi-Fi"]), (False, [])])
def test_apply_sets_utils_lines(monkeypatch, on, want):
    from gui2.app_settings import SettingsMixin
    monkeypatch.setattr(utils, "LINES", ["stale"])
    win = types.SimpleNamespace(_extras={"multi_line": on, "lines": ["Wi-Fi"]})
    SettingsMixin._apply_network_settings(win)
    assert utils.LINES == want


def test_drawer_shows_connections_per_line():
    from gui2.details_drawer import DetailsDrawer
    app = QApplication.instance() or QApplication([])
    host = QWidget()                            # kept: the drawer is its child
    drawer = DetailsDrawer(host)
    t = T.DownloadTask("https://example.test/big.iso", "C:/Downloads/big.iso")
    t.status = T.DOWNLOADING
    t.http_conns, t.line_conns = 10, {"Wi-Fi": 6, "Phone": 4}
    drawer.open_for(t)
    drawer.update_live(t, 0)
    app.processEvents()
    assert drawer.ov_conns.text() == "Wi-Fi 6 · Phone 4"
    t.line_conns = {}
    drawer.update_live(t, 0)
    assert drawer.ov_conns.text() == "10"
