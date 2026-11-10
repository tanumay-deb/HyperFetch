"""Settings -> Downloads -> Default Queue offers the queues there are
(2026-11-04, list #6). It listed "Main" alone, so a queue made in Manage
Queues could not be picked, and new downloads always landed in Main."""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

COMMON = dict(save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
              theme="Dark", accent="purple", sched_en=False, sched_start="00:00",
              sched_stop="06:00")


def test_a_queue_of_the_users_can_be_the_default():
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    dlg = SettingsDialogV2(None, extras={"default_queue": "Night"},
                           queues=["Main", "Night"], **COMMON)
    try:
        box = dlg.def_queue
        assert [box.itemText(i) for i in range(box.count())] == ["Main", "Night"]
        assert dlg.values()["default_queue"] == "Night"
    finally:
        dlg.deleteLater()


def test_one_that_is_gone_falls_back_to_the_first():
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    dlg = SettingsDialogV2(None, extras={"default_queue": "Deleted"},
                           queues=["Main", "Night"], **COMMON)
    try:
        assert dlg.values()["default_queue"] == "Main"
    finally:
        dlg.deleteLater()


def test_the_window_hands_settings_its_queues(monkeypatch):
    import gui2.app as app_mod
    got = {}

    class Dlg:
        def __init__(self, parent, **kw):
            got.update(kw)

        def exec(self):
            return 0                            # closed without saving

    monkeypatch.setattr(app_mod, "SettingsDialogV2", Dlg)
    win = types.SimpleNamespace(
        save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
        theme="Dark", scheduler_enabled=False, scheduler_start="00:00",
        scheduler_stop="06:00", _extras={}, pair_requests=None,
        queue=types.SimpleNamespace(queues={"Main": None, "Night": None}))
    app_mod.DownloadAppV2._open_settings(win)
    assert got["queues"] == ["Main", "Night"]
