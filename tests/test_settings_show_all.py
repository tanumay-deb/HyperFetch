"""Settings shows the everyday options; "Show all settings" shows the rest.

Asked 2026-11-16: about 53 options in 9 sections were too many for someone
who only wants to download things. Nothing is removed - the rest is tucked
away in place (specs/2026-11-16-settings-show-all-design.md). A search still
finds every option, and switching changes no setting.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFrame, QWidget

COMMON = dict(save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
              theme="Dark", accent="purple", sched_en=False, sched_start="00:00",
              sched_stop="06:00")

EVERYDAY = ["Default Download Folder", "Check for updates automatically", "Close button behavior",
            "Concurrent Downloads", "Download Speed Limit", "When download is complete",
            "Ask before adding a download", "Seed after completing", "Upload speed limit",
            "Browser Extension", "Font Size"]


@pytest.fixture
def dlg():
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    d = SettingsDialogV2(None, extras={}, **COMMON)
    yield d
    d.deleteLater()


def rows(d):
    return [r for r in d.findChildren(QWidget, "settingRow")]


def shown_labels(d):
    return sorted(r.property("label") for r in rows(d) if not r.isHidden())


def hidden_sections(d):
    from gui2.dialogs.settings import _SECTIONS
    return [_SECTIONS[i] for i in range(d.nav.count()) if d.nav.isRowHidden(i)]


def panel_titled(d, words):
    from PySide6.QtWidgets import QLabel
    return next(p for p in d.findChildren(QFrame) if p.objectName() == "panel"
                and any(words in l.text() for l in p.findChildren(QLabel)))


def test_everyday_options_only_by_default(dlg):
    assert dlg.show_all.isChecked() is False
    assert shown_labels(dlg) == sorted(EVERYDAY)
    assert hidden_sections(dlg) == ["Network", "Browser Access", "Advanced"]
    assert not panel_titled(dlg, "Pairing Token").isHidden()
    assert panel_titled(dlg, "Auto Capture").isHidden()


def test_show_all_shows_everything(dlg):
    dlg.show_all.setChecked(True)
    assert all(not r.isHidden() for r in rows(dlg))
    assert len(rows(dlg)) > 40
    assert hidden_sections(dlg) == []
    assert not panel_titled(dlg, "Auto Capture").isHidden()


def test_search_finds_a_tucked_away_option(dlg):
    dlg.search.setText("proxy")
    assert "Proxy Settings" in shown_labels(dlg)
    assert "Network" not in hidden_sections(dlg)
    dlg.search.setText("")
    assert "Proxy Settings" not in shown_labels(dlg)
    assert "Network" in hidden_sections(dlg)


def test_the_open_page_leaving_the_sidebar_moves_to_another(dlg):
    from gui2.dialogs.settings import _SECTIONS
    dlg.show_all.setChecked(True)
    dlg.nav.setCurrentRow(_SECTIONS.index("Network"))
    dlg.show_all.setChecked(False)
    cur = dlg.nav.currentRow()
    assert cur != _SECTIONS.index("Network") and not dlg.nav.isRowHidden(cur)
    assert dlg.stack.currentIndex() == cur


def test_the_switch_is_saved_and_changes_no_value(dlg):
    from gui2.dialogs.settings import SettingsDialogV2
    before = dlg.values()
    assert before["settings_show_all"] is False
    dlg.show_all.setChecked(True)
    after = dlg.values()
    assert after.pop("settings_show_all") is True
    before.pop("settings_show_all")
    assert after == before
    again = SettingsDialogV2(None, extras={"settings_show_all": True}, **COMMON)
    try:
        assert again.show_all.isChecked() and hidden_sections(again) == []
    finally:
        again.deleteLater()


def test_the_pairing_card_stays_whole_in_the_real_window(dlg):
    """The window passes pair_requests, which puts "Paired Firefox browsers"
    in the token's card: tucked away, it took the token with it."""
    from gui2.dialogs.settings import SettingsDialogV2

    class Requests:
        def count(self):
            return 0

        def forget_all(self):
            pass

    d = SettingsDialogV2(None, extras={}, pair_requests=Requests(), **COMMON)
    try:
        card = panel_titled(d, "Pairing Token")
        assert not card.isHidden()
        assert "Paired Firefox browsers" in shown_labels(d)
    finally:
        d.deleteLater()


def test_a_rows_second_line_goes_with_it(dlg):
    """The scheduled limit's From / to / limit line was its own, outside the
    row: tucked away, the row's title went and the line stayed."""
    def row_of(w):
        while w is not None and w.objectName() != "settingRow":
            w = w.parentWidget()
        return w

    for box, label in ((dlg.thr_start, "Scheduled speed limit"), (dlg.t_start, "Scheduler")):
        assert row_of(box) is not None and row_of(box).property("label") == label
        assert row_of(box).isHidden()
    dlg.show_all.setChecked(True)
    assert not row_of(dlg.thr_start).isHidden()


def test_the_switch_is_saved_as_it_is_flipped():
    """Asked 2026-11-16: kept when the switch is flipped, not only by Save
    Changes - closed with Reset, it came back off."""
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    told = []
    d = SettingsDialogV2(None, extras={}, on_show_all=told.append, **COMMON)
    try:
        d.show_all.setChecked(True)
        d.show_all.setChecked(False)
        assert told == [True, False]
    finally:
        d.deleteLater()


def test_the_window_keeps_the_switch_at_once(monkeypatch):
    import types
    import gui2.app as app_mod
    got = {}

    class Dlg:
        def __init__(self, parent, **kw):
            got.update(kw)

        def exec(self):
            return 0                            # closed with Reset

    monkeypatch.setattr(app_mod, "SettingsDialogV2", Dlg)
    saved = []
    win = types.SimpleNamespace(
        save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
        theme="Dark", scheduler_enabled=False, scheduler_start="00:00",
        scheduler_stop="06:00", _extras={}, pair_requests=None,
        queue=types.SimpleNamespace(queues={"Main": None}),
        _save_settings=lambda: saved.append(dict(win._extras)))
    win._remember_show_all = types.MethodType(app_mod.DownloadAppV2._remember_show_all, win)
    app_mod.DownloadAppV2._open_settings(win)
    got["on_show_all"](True)
    assert saved == [{"settings_show_all": True}]
