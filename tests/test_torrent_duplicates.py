"""A torrent added from a .torrent is known the second time, as a magnet is.

The add-time check compared each listed task's .torrent FILE, so it missed:
  - a torrent whose .torrent had been deleted after it was added (the usual
    thing to do with a file in Downloads) - nothing to read, so no match;
  - a .torrent given as a web link, which is what the extension sends for a
    .torrent link. That one never even started: the engine said "the
    .torrent file is no longer at https://..." and paused it.
And it offered nothing for a .torrent: "Load Trackers" read only a magnet's
tr=, and a .torrent download could not take trackers at all ("Trackers can
only be added to magnet links").

Now a torrent task carries its infohash and the app keeps its own copy of
its .torrent from the moment it is added; a web link is fetched (with the
browser's headers) at add and by the engine; and a .torrent download takes
trackers like a magnet does - first, without changing which torrent it is.
"""
import base64
import os
import shutil
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import aria2d
import task as T
import torrent
import utils

HERE = os.path.dirname(os.path.abspath(__file__))
SINTEL = os.path.join(HERE, "data", "08ada5a7a6183aae1e09d831df6748d566095a10.torrent")
SINTEL_IH = "08ada5a7a6183aae1e09d831df6748d566095a10"
NEW = "udp://new.example:80/announce"


def _bencode(x):
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode()
    if isinstance(x, bytes):
        return b"%d:%s" % (len(x), x)
    if isinstance(x, list):
        return b"l" + b"".join(map(_bencode, x)) + b"e"
    return b"d" + b"".join(_bencode(k) + _bencode(x[k]) for k in sorted(x)) + b"e"


def _torrent_bytes(trackers=("udp://own.example:80/announce",), private=False, name="Movie.mkv"):
    info = {"name": name, "length": 1000, "piece length": 16384, "pieces": b"x" * 20}
    if private:
        info["private"] = 1
    return _bencode({"announce": trackers[0], "announce-list": [[t] for t in trackers],
                     "info": info})


def _file(tmp_path, data, name="Movie.torrent"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ---- the window --------------------------------------------------------------------
class _Box:
    """QMessageBox: records what it says and presses the button named in
    `press` (None: closes it)."""
    Question = Information = AcceptRole = RejectRole = ActionRole = 0
    press = None
    shown = []

    def __init__(self, *a):
        self._buttons, self._said = {}, []
        _Box.shown.append(self._said)

    def setText(self, s):
        self._said.append(s)

    def setInformativeText(self, s):
        self._said.append(s)

    def addButton(self, text, role):
        self._buttons[text] = object()
        self._said.append("[%s]" % text)
        return self._buttons[text]

    def clickedButton(self):
        return self._buttons.get(_Box.press)

    def __getattr__(self, name):                    # setIcon, exec, ...
        return lambda *a, **k: None


@pytest.fixture
def window(monkeypatch):
    """The window's add path, with a list, a queue that records, toasts."""
    import gui2.app as app_mod
    from gui2.app import DownloadAppV2
    monkeypatch.setattr(app_mod, "QMessageBox", _Box)
    monkeypatch.setattr(aria2d, "DAEMON", types.SimpleNamespace(
        call=lambda *a, **k: pytest.fail("the window asked the daemon")))
    _Box.press, _Box.shown = None, []
    added, toasts, shown = [], [], []
    queue = types.SimpleNamespace(queues={"Main": None}, tasks=[], segments=8)

    def add_task(t, start=True):
        added.append(t)
        queue.tasks.append(t)

    queue.add_task = add_task
    win = types.SimpleNamespace(
        save_dir=None, segments=8, queue=queue,
        _extras={"skip_new_download_dialog": True, "auto_start": False},
        _toasts=types.SimpleNamespace(show=lambda *a, **k: toasts.append(a)),
        _save_state=lambda: None, refresh=lambda: None,
        _show_existing=shown.append)
    for name in ("_quick_values", "_torrent_again", "_fetch_torrent"):
        setattr(win, name, (lambda n: lambda *a: getattr(DownloadAppV2, n)(win, *a))(name))
    win.add = lambda url: DownloadAppV2._add_download(win, url, "", None)
    win.added, win.toasts, win.shown = added, toasts, shown
    return win


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_a_torrent_whose_file_was_deleted_is_still_known(window, tmp_path):
    window.save_dir = str(tmp_path)
    first = shutil.copy(SINTEL, tmp_path / "Sintel.torrent")
    window.add(str(first))
    assert len(window.added) == 1 and window.added[0].infohash == SINTEL_IH
    os.remove(first)                                 # as people do, once it is added
    assert os.path.isfile(torrent.kept_torrent(SINTEL_IH)), "the app kept no copy"

    again = shutil.copy(SINTEL, tmp_path / "Sintel (1).torrent")
    _Box.press = "Show Existing"
    window.add(str(again))
    assert len(window.added) == 1, "the same torrent was added twice"
    assert "already in the list" in " ".join(_Box.shown[-1])
    assert window.shown == [window.added[0]]


def test_a_torrent_file_and_its_magnet_are_one_download(window, tmp_path):
    window.save_dir = str(tmp_path)
    window.add("magnet:?xt=urn:btih:%s&dn=Sintel" % SINTEL_IH)
    window.add(str(shutil.copy(SINTEL, tmp_path / "Sintel.torrent")))
    assert len(window.added) == 1


def test_a_web_link_is_fetched_known_and_kept(window, tmp_path, media_server, qapp):
    window.save_dir = str(tmp_path)
    media_server.put("dl/Sintel.torrent", open(SINTEL, "rb").read())
    url = media_server.url("dl/Sintel.torrent")
    window.add(url)
    assert len(window.added) == 1
    t = window.added[0]
    assert t.url == url, "the task should keep the link it came from"
    assert t.infohash == SINTEL_IH
    assert os.path.isfile(torrent.kept_torrent(SINTEL_IH))
    assert torrent.torrent_file_for(t) == torrent.kept_torrent(SINTEL_IH)

    window.add(url)                                  # the same link again
    window.add("magnet:?xt=urn:btih:%s" % SINTEL_IH)  # the same torrent as a magnet
    assert len(window.added) == 1


def test_a_web_link_that_is_a_web_page_says_so_and_adds_nothing(window, tmp_path,
                                                                media_server, qapp):
    window.save_dir = str(tmp_path)
    media_server.put("dl/x.torrent", b"")
    media_server.html.add("dl/x.torrent")
    window.add(media_server.url("dl/x.torrent"))
    assert not window.added
    kind, title, text = window.toasts[-1]
    assert kind == "error" and "web page, not a .torrent" in text


def test_a_web_link_that_is_gone_says_so(window, tmp_path, media_server, qapp):
    window.save_dir = str(tmp_path)
    window.add(media_server.url("dl/missing.torrent"))
    assert not window.added
    assert "not found on the server (404)" in window.toasts[-1][2]


def test_trackers_a_second_torrent_brings_are_offered_and_loaded(window, tmp_path):
    window.save_dir = str(tmp_path)
    window.add(_file(tmp_path, _torrent_bytes(), "a.torrent"))
    t = window.added[0]
    _Box.press = "Load Trackers"
    window.add(_file(tmp_path, _torrent_bytes(("udp://own.example:80/announce", NEW)),
                     "b.torrent"))
    assert len(window.added) == 1
    assert "has 1 tracker the one in the list does not" in " ".join(_Box.shown[-1])
    assert t.extra_trackers == [NEW] and t.trackers_changed is True
    assert window.toasts[-1][1] == "Trackers imported"


def test_a_magnet_brings_its_trackers_to_a_torrent_file(window, tmp_path):
    window.save_dir = str(tmp_path)
    window.add(_file(tmp_path, _torrent_bytes()))
    t = window.added[0]
    _Box.press = "Load Trackers"
    window.add("magnet:?xt=urn:btih:%s&tr=%s" % (t.infohash, NEW))
    assert t.extra_trackers == [NEW]


def test_a_private_torrent_is_offered_no_trackers(window, tmp_path):
    window.save_dir = str(tmp_path)
    window.add(_file(tmp_path, _torrent_bytes(private=True), "a.torrent"))
    t = window.added[0]
    _Box.press = "Load Trackers"                     # not there to press
    window.add(_file(tmp_path, _torrent_bytes(("udp://own.example:80/announce", NEW),
                                              private=True), "b.torrent"))
    said = " ".join(_Box.shown[-1])
    assert "private torrent" in said and "[Load Trackers]" not in said
    assert t.extra_trackers == [] and not t.trackers_changed


def test_nothing_new_says_so_and_offers_the_existing_one(window, tmp_path):
    window.save_dir = str(tmp_path)
    window.add(_file(tmp_path, _torrent_bytes(), "a.torrent"))
    window.add(_file(tmp_path, _torrent_bytes(), "b.torrent"))
    said = " ".join(_Box.shown[-1])
    assert "already up to date" in said and "[Show Existing]" in said
    assert len(window.added) == 1


# ---- the engine ---------------------------------------------------------------------
class _Daemon:
    def __init__(self):
        self.calls = []

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        self.calls.append((method, params))
        if method == "aria2.addTorrent":
            return "gid1"
        if method == "aria2.tellStatus":
            return {"status": "complete", "completedLength": "1000",
                    "totalLength": "1000", "files": []}
        return []

    def handed(self):
        """The .torrent bytes aria2 was given."""
        return base64.b64decode(next(p for m, p in self.calls if m == "aria2.addTorrent")[0])


def _run(tmp_path, monkeypatch, t):
    monkeypatch.setattr(torrent, "aria2c_path", lambda: "aria2c.exe")
    monkeypatch.setattr(torrent, "POLL", 0)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0)
    d = _Daemon()
    monkeypatch.setattr(aria2d, "DAEMON", d)
    torrent.TorrentDownloader(t).run()
    return d


def test_the_engine_fetches_a_web_link_itself(tmp_path, monkeypatch, media_server):
    """What the headless server and the web client add: the link, nothing more."""
    media_server.put("dl/Sintel.torrent", open(SINTEL, "rb").read())
    t = T.DownloadTask(media_server.url("dl/Sintel.torrent"), str(tmp_path / "Sintel.torrent"),
                       headers={"Cookie": "session=1"})
    d = _run(tmp_path, monkeypatch, t)
    assert t.status == T.COMPLETED, t.error
    assert torrent.infohash_of(d.handed()) == SINTEL_IH
    assert t.infohash == SINTEL_IH and os.path.isfile(torrent.kept_torrent(SINTEL_IH))
    asked = [r for r in media_server.log if r["path"] == "dl/Sintel.torrent"]
    assert asked and asked[0]["headers"].get("Cookie") == "session=1", \
        "the site's cookies did not go with it"


def test_a_web_link_that_will_not_give_a_torrent_fails_in_words(tmp_path, monkeypatch,
                                                                media_server):
    media_server.put("dl/x.torrent", b"")
    media_server.html.add("dl/x.torrent")
    t = T.DownloadTask(media_server.url("dl/x.torrent"), str(tmp_path / "x.torrent"))
    d = _run(tmp_path, monkeypatch, t)
    assert t.status == T.ERROR and "web page, not a .torrent" in t.error
    assert not any(m == "aria2.addTorrent" for m, _ in d.calls)
    assert not getattr(t, "_stall_yield", False), "tried again by itself"


def test_trackers_added_to_a_torrent_go_first_and_keep_which_torrent_it_is(tmp_path,
                                                                           monkeypatch):
    data = open(SINTEL, "rb").read()
    t = T.DownloadTask(_file(tmp_path, data, "Sintel.torrent"), str(tmp_path / "out.bin"))
    t.status = T.PAUSED
    added, now = torrent.add_trackers(t, [NEW, "udp://tracker.opentrackr.org:1337/announce"])
    assert added == [NEW] and not now and t.trackers_changed
    assert torrent.trackers_of(t)[0] == NEW
    d = _run(tmp_path, monkeypatch, t)
    handed = d.handed()
    assert torrent.trackers_in(handed) == [NEW, "udp://tracker.opentrackr.org:1337/announce"]
    assert torrent.infohash_of(handed) == SINTEL_IH, "it became another torrent"
    assert open(t.url, "rb").read() == data, "the user's .torrent was changed"


def test_a_private_torrent_takes_no_trackers(tmp_path):
    t = T.DownloadTask(_file(tmp_path, _torrent_bytes(private=True)), str(tmp_path / "o.bin"))
    assert torrent.add_trackers(t, [NEW]) == ([], False)
    assert t.extra_trackers == []


def test_the_metadata_scout_never_hands_a_torrent_link_to_aria2(tmp_path, monkeypatch,
                                                                media_server):
    """aria2's addUri would fetch the .torrent - then the whole payload, into app
    data: bt-metadata-only is for magnets only."""
    media_server.put("dl/Sintel.torrent", open(SINTEL, "rb").read())
    monkeypatch.setattr(aria2d, "DAEMON", types.SimpleNamespace(
        ensure=lambda: True, call=lambda *a, **k: pytest.fail("handed to aria2")))
    t = T.DownloadTask(media_server.url("dl/Sintel.torrent"), str(tmp_path / "Sintel.torrent"))
    t.status = T.QUEUED
    torrent.MetadataPrefetcher(lambda: [t])._begin(t)
    assert t.infohash == SINTEL_IH and t.total_size, "nothing learned from the .torrent"


# ---- reading .torrent bytes from anywhere ----------------------------------------------
@pytest.mark.parametrize("junk", [b"", b"<html>", b"d4:infoi1e", b"l" * 5000 + b"e" * 5000,
                                  b"d8:announce" + b"9" * 30])
def test_bytes_that_are_not_a_torrent_are_nobodys(junk):
    assert torrent.infohash_of(junk) == ""
    assert torrent.trackers_in(junk) == []
    assert torrent.with_trackers_first(junk, [NEW]) == junk
    assert torrent.keep_torrent(junk) == ("", "")
