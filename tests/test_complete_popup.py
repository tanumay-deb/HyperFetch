"""The completion popup.

It appears unprompted, over whatever the user was doing, so its size must be
bounded on both axes and it must be dismissible for good.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget, QPushButton

import task as T
from gui2.dialogs.complete import CompleteDialog

LONG = "Show.S01.COMPLETE.1080p.WEB-DL.DDP5.1.H.264-RELEASEGROUP[rartv]" * 3


def _app():
    return QApplication.instance() or QApplication([])


def _dlg(name="file.zip", url="https://x/file.zip", segments=0):
    _app()
    host = QWidget()
    t = T.DownloadTask(url, "E:/dl/" + name, filename=name, total_size=8_000_000_000)
    t.downloaded = 8_000_000_000
    t.added = time.time() - 3600
    t.status = T.COMPLETED
    if segments:
        t.segments = [object()] * segments
    d = CompleteDialog(host, t)
    d._host_ref = host
    d.adjustSize()
    return d


def test_the_popup_is_bounded_on_both_axes():
    """Unprompted windows must not grow to fit their content."""
    d = _dlg()
    assert d.width() <= 400
    assert d.maximumHeight() <= 460


def test_a_very_long_name_does_not_make_it_grow():
    """The name used to wrap and push the whole popup taller."""
    short = _dlg("a.zip")
    long_ = _dlg(LONG)
    assert long_.width() == short.width()
    assert long_.height() <= short.height() + 4


def test_the_buttons_fit_inside_the_popup():
    """They used to overflow into 'Open F / Folde / iew in Lis'."""
    d = _dlg()
    d.show()
    _app().processEvents()
    for b in d.findChildren(QPushButton):
        assert b.sizeHint().width() <= b.width() + 1, f"{b.text()!r} is clipped"
    d.hide()


def test_connections_are_hidden_for_a_torrent():
    """A torrent has no HTTP segments, so the row would always read 0."""
    from PySide6.QtWidgets import QLabel
    tor = _dlg("Movie", url="magnet:?xt=urn:btih:abc")
    labels = [l.text() for l in tor.findChildren(QLabel)]
    assert "Connections" not in labels

    http = _dlg("a.zip", segments=8)
    labels = [l.text() for l in http.findChildren(QLabel)]
    assert "Connections" in labels


def test_dont_show_again_defaults_off_and_is_readable():
    d = _dlg()
    assert d.skip_next.isChecked() is False
    d.skip_next.setChecked(True)
    assert d.skip_next.isChecked() is True


def test_the_preference_is_recorded_however_the_popup_closed():
    """The checkbox is a preference, not a submission — closing with the X must
    honour it just as Close does."""
    import types
    from gui2.app import DownloadAppV2
    d = _dlg()
    d.skip_next.setChecked(True)
    saved = {}
    stub = types.SimpleNamespace(_extras={}, _save_settings=lambda: saved.setdefault("n", 1))
    DownloadAppV2._remember_skip_complete(stub, d)
    assert stub._extras["skip_complete_popup"] is True
    assert saved.get("n") == 1


def test_an_unticked_box_records_nothing():
    import types
    from gui2.app import DownloadAppV2
    d = _dlg()
    stub = types.SimpleNamespace(_extras={}, _save_settings=lambda: None)
    DownloadAppV2._remember_skip_complete(stub, d)
    assert "skip_complete_popup" not in stub._extras
