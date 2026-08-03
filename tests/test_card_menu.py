"""The download-card context menu must BUILD for every task state.

A NameError in one branch stops the whole menu opening, and right-click then
does nothing with no error the user can see. Testing the handlers is not
enough — that is exactly how such a bug shipped. This builds the real menu.

The harness is a plain object rather than a QWidget subclass: _build_card_menu
needs no widget, and constructing real widgets here made the run hang.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

import pytest
from PySide6.QtWidgets import QApplication, QMenu

import task as T
from gui2.app_actions import ActionsMixin


def _app():
    return QApplication.instance() or QApplication([])


class _Host(ActionsMixin):
    def __init__(self, tasks):
        self.queue = types.SimpleNamespace(
            queues={"Main": types.SimpleNamespace(name="Main")},
            get_task=lambda i: None, tasks=tasks)
        self.list = types.SimpleNamespace(selected_ids=lambda: set(),
                                          set_selection=lambda x: None)
        self.drawer = types.SimpleNamespace(open_for=lambda t: None)
        self._toasts = types.SimpleNamespace(show=lambda *a: None)

    def _menu(self):
        return QMenu()


def _task(status, url="https://x/a.zip", name="a.zip"):
    t = T.DownloadTask(url, f"C:/dl/{name}", filename=name, total_size=1000)
    t.status = status
    t.downloaded = 400
    return t


@pytest.mark.parametrize("status", [
    T.DOWNLOADING, T.PAUSED, T.QUEUED, T.COMPLETED, T.ERROR,
    T.CANCELLED, T.SCHEDULED,
])
@pytest.mark.parametrize("url,name", [
    ("https://x/a.zip", "a.zip"),
    ("magnet:?xt=urn:btih:abc&dn=Show", "Show"),
    ("C:/t/file.torrent", "file.torrent"),
])
def test_the_menu_builds_for_every_state_and_kind(status, url, name):
    _app()
    m = _Host([]) ._build_card_menu(_task(status, url=url, name=name))
    assert m is not None, "the builder returned nothing to show"
    assert m.actions(), "menu built but empty"


def test_the_builder_returns_the_menu_instead_of_showing_it():
    """It must not exec(): that blocks on real input, and a caller doing
    m.exec() on the None it used to return crashed right-click outright."""
    _app()
    m = _Host([])._build_card_menu(_task(T.DOWNLOADING))
    assert isinstance(m, QMenu)
    assert not m.isVisible()


def test_a_downloading_torrent_offers_play_when_there_is_data(tmp_path):
    _app()
    vid = tmp_path / "Show" / "ep1.mkv"
    vid.parent.mkdir(parents=True)
    vid.write_bytes(b"\0" * 100)
    t = _task(T.DOWNLOADING, url="magnet:?xt=urn:btih:abc&dn=Show", name="Show")
    t.save_path = str(tmp_path / "Show")
    t.file_progress = [{"path": str(vid), "length": 1000, "completed": 100,
                        "selected": True}]
    m = _Host([])._build_card_menu(t)   # hold it: an unparented menu is collected
    labels = [a.text() for a in m.actions()]
    assert "Play preview" in labels


def test_no_play_entry_when_nothing_has_data(tmp_path):
    _app()
    t = _task(T.DOWNLOADING, url="magnet:?xt=urn:btih:abc&dn=Show", name="Show")
    t.save_path = str(tmp_path / "Show")
    t.file_progress = []
    m = _Host([])._build_card_menu(t)   # hold it: an unparented menu is collected
    labels = [a.text() for a in m.actions()]
    assert "Play preview" not in labels
