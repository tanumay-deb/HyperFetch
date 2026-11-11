"""Settings -> Browser holds no switches that do nothing (2026-11-11, list #9).

A switch each for Google Chrome and Microsoft Edge, labelled "Detected":
nothing detected a browser, the saved value was read nowhere, and turning
one off still let that browser send downloads.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel                # noqa: E402

COMMON = dict(save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
              theme="Dark", accent="purple", sched_en=False, sched_start="00:00",
              sched_stop="06:00")


def test_the_browser_page_has_no_switch_that_does_nothing():
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    dlg = SettingsDialogV2(None, extras={"browsers": {"Google Chrome": False}}, **COMMON)
    try:
        labels = [w.text() for w in dlg.findChildren(QLabel)]
        assert "Detected" not in labels
        assert "Google Chrome" not in labels and "Microsoft Edge" not in labels
        assert "browsers" not in dlg.values()
        assert "Download button position" in labels, "the rest of the card went too"
    finally:
        dlg.deleteLater()
