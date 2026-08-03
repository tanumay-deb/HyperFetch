"""New Download dialog after the trim.

Every row on this screen sits between the user and the thing they asked for, so
these assert that the removed ones really are gone and that the remaining ones
only appear where they change the outcome.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from gui2.dialogs.new_download import NewDownloadDialog, URL_TAB, MAGNET_TAB, TORRENT_TAB

MAGNET = "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel"


def _app():
    return QApplication.instance() or QApplication([])


def _dlg(url="", suggested="", **kw):
    _app()
    host = QWidget()
    d = NewDownloadDialog(host, "E:/HyperFetch", ["Main", "Night"], 8,
                          url=url, suggested=suggested, **kw)
    d._host_ref = host
    return d


def test_there_is_no_duplicate_title_in_the_body():
    """The title bar already says New Download; a second copy was the tallest
    thing on the dialog."""
    from PySide6.QtWidgets import QLabel
    d = _dlg()
    texts = [l.text() for l in d.findChildren(QLabel)]
    assert texts.count("New Download") == 0
    assert d.windowTitle() == "New Download"


def test_priority_is_gone_but_tasks_still_get_one():
    d = _dlg(url="https://x/a.zip")
    assert not hasattr(d, "prio")
    assert d.values()["priority"] == 0        # the queue still needs the field


def test_the_dialog_is_narrower_than_it_was():
    d = _dlg()
    assert d.minimumWidth() <= 460            # was 560


def test_a_magnet_hides_the_fields_that_decide_nothing():
    """A torrent names itself and is not categorised, so offering those fields
    invites a value that is then ignored."""
    d = _dlg(url=MAGNET, suggested="magnet:")
    assert d.tabs.currentIndex() == MAGNET_TAB
    assert d.name_edit.isHidden()
    assert d.cat.isHidden()


def test_the_url_tab_keeps_them():
    d = _dlg(url="https://x/a.zip")
    assert d.tabs.currentIndex() == URL_TAB
    assert not d.name_edit.isHidden()
    assert not d.cat.isHidden()


def test_a_useless_magnet_filename_is_never_carried_over():
    """Callers derive a name from the URL and hand us literally "magnet:".
    Left in the box, switching to the URL tab would submit that as the name."""
    d = _dlg(url=MAGNET, suggested="magnet:")
    assert d.name_edit.text() == ""
    d.tabs.setCurrentIndex(URL_TAB)
    d.url_edit.setText("https://x/real.zip")
    assert d.values()["filename"] == ""


def test_a_torrent_tab_never_forces_a_filename():
    d = _dlg(url=MAGNET)
    d.name_edit.setText("something the user typed earlier")
    assert d.values()["filename"] == ""


def test_the_destination_hint_only_appears_when_it_says_something_new():
    """Repeating the folder shown directly above it is a line of noise."""
    d = _dlg(url="https://x/notes.txt")
    d.cat.setCurrentText("Auto")
    d.url_edit.setText("https://x/notes.xyz")     # uncategorised -> no subfolder
    assert d.dest_hint.isHidden()
    d.cat.setCurrentText("Video")                 # explicit subfolder
    assert not d.dest_hint.isHidden()
    assert "Video" in d.dest_hint.text()


def test_dont_show_again_is_reported_and_defaults_off():
    d = _dlg(url="https://x/a.zip")
    assert d.values()["skip_dialog"] is False
    d.skip_next.setChecked(True)
    assert d.values()["skip_dialog"] is True


# --------------------------------------------------------- skipping the dialog
def _stub_app(extras=None):
    import types
    from gui2.app import DownloadAppV2
    stub = types.SimpleNamespace(
        save_dir="E:/HyperFetch",
        segments=8,
        _extras=dict(extras or {}),
        queue=types.SimpleNamespace(queues={"Main": None, "Night": None}),
    )
    return stub, DownloadAppV2


def test_skipping_the_dialog_matches_what_the_dialog_would_have_returned():
    """Turning the prompt off must change HOW a download is added, never where
    it lands — otherwise the setting quietly relocates people's files."""
    stub, cls = _stub_app({"default_queue": "Night", "auto_start": True})
    quick = cls._quick_values(stub, "https://x/a.zip", "a.zip", {})

    d = _dlg(url="https://x/a.zip", suggested="a.zip")
    d.q.setCurrentText("Night")
    shown = d.values()

    for key in ("url", "save_dir", "filename", "category", "queue",
                "priority", "connections", "start_now", "yt_format"):
        assert quick[key] == shown[key], f"{key} differs when the dialog is skipped"


def test_the_quick_path_does_not_invent_a_torrent_filename():
    stub, cls = _stub_app()
    v = cls._quick_values(stub, MAGNET, "magnet:", {})
    assert v["filename"] == ""


def test_the_quick_path_falls_back_to_a_real_queue():
    """A default_queue naming a deleted queue must not produce a task in a
    queue that does not exist."""
    stub, cls = _stub_app({"default_queue": "Deleted"})
    assert cls._quick_values(stub, "https://x/a.zip", "", {})["queue"] == "Main"
