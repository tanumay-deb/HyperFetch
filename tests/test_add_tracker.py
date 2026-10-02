"""Adding trackers to a torrent that is already in the list.

Two doors lead here: the Trackers tab in the details drawer, and adding a
magnet whose torrent is already listed ("Load Trackers"). Both used to call
aria2's changeOption("bt-tracker") on the running download and report success.
Measured on aria2 1.37.0, that call changes nothing - the tracker list is fixed
when a download is added - and restarts a running download for no gain.

Now both write the trackers into the magnet, ahead of the ones already there
(aria2 stops at the first tracker that answers), and flag the task; the engine
adds the torrent again with them (tests/test_torrent_start.py). What the user
is told matches what will happen.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import aria2d
import task as T
import torrent

IH = "a" * 40
OLD, NEW = "udp://old.example:80", "udp://new.example:80"


def _task(status=T.DOWNLOADING, gid="gid1", seeding=False):
    t = T.DownloadTask("magnet:?xt=urn:btih:%s&dn=Show&tr=%s" % (IH, "udp%3A%2F%2Fold.example%3A80"),
                       "C:/dl/magnet_.bin", filename="Show")
    t.status = status
    t.gid = gid
    t.seeding = seeding
    return t


class _Daemon:
    """Records what it is asked. The window must ask it nothing here: the
    daemon cannot change a download's trackers, and the engine does the rest."""

    def __init__(self):
        self.asked = []

    def call(self, method, *a, **kw):
        self.asked.append(method)
        return {}


@pytest.fixture(autouse=True)
def no_daemon(monkeypatch):
    daemon = _Daemon()
    monkeypatch.setattr(aria2d, "DAEMON", daemon)
    yield
    assert daemon.asked == [], "the window asked the daemon for %s" % daemon.asked


# ---- the Trackers tab ----------------------------------------------------------

def _add_in_drawer(t, typed):
    from gui2.details_drawer import DetailsDrawer
    said, saved = [], []
    win = types.SimpleNamespace(queue=types.SimpleNamespace(tasks=[t]),
                                _save_state=lambda: saved.append(1))
    drawer = types.SimpleNamespace(
        tracker_input=types.SimpleNamespace(text=lambda: typed, clear=lambda: None),
        tracker_msg=types.SimpleNamespace(setText=said.append),
        _window=lambda: win, _tid=t.id, _render_trackers=lambda task: None)
    DetailsDrawer._add_trackers(drawer)
    return said[-1], saved


def test_a_tracker_added_to_a_downloading_torrent_is_put_first_and_applied(tmp_path):
    t = _task()
    said, saved = _add_in_drawer(t, NEW)
    assert torrent.magnet_trackers(t.url) == [NEW, OLD]
    assert t.trackers_changed is True, "nothing tells the engine to add it again"
    assert "Reconnecting" in said
    assert saved, "the changed magnet was not saved"


def test_a_tracker_added_to_a_paused_torrent_says_when_it_will_be_used():
    t = _task(status=T.PAUSED, gid=None)
    said, _ = _add_in_drawer(t, NEW)
    assert torrent.magnet_trackers(t.url) == [NEW, OLD]
    assert t.trackers_changed is True, "its next start would re-attach to the old list"
    assert "next time this torrent starts" in said
    assert "Reconnecting" not in said


def test_a_seeding_torrent_is_not_said_to_reconnect():
    t = _task(status=T.COMPLETED, seeding=True)
    said, _ = _add_in_drawer(t, NEW)
    assert "next time this torrent starts" in said


def test_a_tracker_it_already_has_changes_nothing():
    t = _task()
    said, saved = _add_in_drawer(t, OLD.upper())
    assert t.trackers_changed is False
    assert "Already" in said and not saved


# ---- adding a magnet whose torrent is already listed ----------------------------

class _Box:
    """QMessageBox, with the user pressing the first button (Load Trackers)."""
    Question = AcceptRole = RejectRole = 0
    informed = []

    def __init__(self, *a):
        self._buttons = []

    @staticmethod
    def information(*a):
        _Box.informed.append(a)

    def addButton(self, text, role):
        self._buttons.append(object())
        return self._buttons[-1]

    def clickedButton(self):
        return self._buttons[0]

    def __getattr__(self, name):                    # setIcon, setText, exec, ...
        return lambda *a, **k: None


def _add_magnet_again(existing, monkeypatch):
    import gui2.app as app_mod
    from gui2.app import DownloadAppV2
    monkeypatch.setattr(app_mod, "QMessageBox", _Box)
    toasts, saved = [], []
    queue = types.SimpleNamespace(queues={"Main": None}, tasks=[existing], segments=8,
                                  add_task=lambda *a, **k: pytest.fail("added it twice"))
    win = types.SimpleNamespace(
        save_dir="C:/dl", segments=8, queue=queue,
        _extras={"skip_new_download_dialog": True},
        _toasts=types.SimpleNamespace(show=lambda *a, **k: toasts.append(a)),
        _save_state=lambda: saved.append(1), refresh=lambda: None)
    win._quick_values = lambda *a: DownloadAppV2._quick_values(win, *a)
    incoming = "magnet:?xt=urn:btih:%s&tr=%s" % (IH, "udp%3A%2F%2Fnew.example%3A80")
    DownloadAppV2._add_download(win, incoming, "", None)
    return [t for t in toasts if t[1] != "Added"], saved


def test_trackers_loaded_from_a_second_magnet_are_put_first_and_applied(monkeypatch):
    t = _task()
    toasts, saved = _add_magnet_again(t, monkeypatch)
    assert torrent.magnet_trackers(t.url) == [NEW, OLD]
    assert t.trackers_changed is True
    assert saved
    kind, title, text = toasts[-1]
    assert title == "Trackers imported"
    assert "Reconnecting" in text


def test_trackers_loaded_for_a_paused_torrent_say_when_they_will_be_used(monkeypatch):
    t = _task(status=T.PAUSED, gid=None)
    toasts, _ = _add_magnet_again(t, monkeypatch)
    assert t.trackers_changed is True
    assert "next time it starts" in toasts[-1][2]
