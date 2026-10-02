"""What aria2 is told when a torrent starts, and what happens the moment a
magnet's metadata arrives.

A torrent is filed, and its trackers settled, before any of it is downloaded.
A .torrent says what it is when it is added. A magnet does not: its add carries
pause-metadata, so aria2 holds the payload it makes once the metadata is in,
and the app reads that metadata first. With nothing to change the held payload
is let go. Otherwise it is dropped and added again from the saved metadata, in
the right folder with the right trackers - aria2 can change neither on a
download that already exists (measured on 1.37.0).
"""
import os

import aria2d
import task as T
import torrent
import utils
from test_sorting import _magnet_task, make_torrent

IH = "a" * 40
# aria2 otherwise drops a copy of every .torrent it is handed into the download
# folder, as <sha1>.torrent, next to the payload
NO_COPY = {"rpc-save-upload-metadata": "false"}
META_STAGE = {"status": "active", "files": [{"path": "[METADATA]abc"}]}


class _PolledForever(BaseException):
    """The engine kept asking about a download that will never change. Not an
    Exception, so the engine's own retry loop cannot swallow it and hang."""


class _Daemon:
    """aria2 as the engine sees it: adds get gids in order, tellStatus replays a
    script whose entries may be functions (so a file can appear on disk at the
    moment aria2 would have written it)."""

    def __init__(self, script, entries=None):
        self.script = list(script)
        self.entries = dict(entries or {})      # gid -> status, for torrents already held
        self.calls = []
        self.added = []                         # (kind, options) per add, in order
        self.polls = 0

    def ensure(self):
        return True

    def call(self, method, *params, **kw):
        self.calls.append((method, params))
        if method == "aria2.tellStatus":
            self.polls += 1
            if self.polls > 200:
                raise _PolledForever(params[0])
            if params[0] in self.entries:
                return self.entries[params[0]]
            step = self.script.pop(0) if self.script else {"status": "complete"}
            return step() if callable(step) else step
        if method in ("aria2.tellActive", "aria2.tellWaiting"):
            return [dict(st, gid=g) for g, st in self.entries.items()
                    if (st.get("status") == "active") == (method == "aria2.tellActive")]
        if method in ("aria2.forceRemove", "aria2.remove"):
            self.entries.pop(params[0], None)
        if method == "aria2.addUri":
            self.added.append(("magnet", params[1]))
            return "gid%d" % len(self.added)
        if method == "aria2.addTorrent":
            self.added.append(("torrent", params[2]))
            return "gid%d" % len(self.added)
        return {}

    def did(self, method, gid):
        return (method, (gid,)) in self.calls


def _start(task, daemon, tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "appdata"))
    monkeypatch.setattr(torrent, "POLL", 0)
    monkeypatch.setattr(torrent, "STATUS_POLL", 0)
    monkeypatch.setattr(aria2d, "DAEMON", daemon)
    torrent.TorrentDownloader(task)._run_rpc()
    return task


def _done(path, size=500):
    """A status reply for a finished payload, which is then really on disk."""
    def reply():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x")
        return {"status": "complete", "completedLength": str(size), "totalLength": str(size),
                "files": [{"path": path}]}
    return reply


def _metadata_arrives(folder, held="held", **torrent_kw):
    """The reply in which aria2 names the payload it made - having just saved
    the metadata beside it, as --bt-save-metadata does."""
    def reply():
        make_torrent(os.path.join(str(folder), IH + ".torrent"), **torrent_kw)
        return {"status": "complete", "followedBy": [held],
                "files": [{"path": "[METADATA]abc"}]}
    return reply


# ---- a .torrent: everything is known at the add -------------------------------

def test_a_private_torrent_file_is_added_with_only_its_own_trackers(tmp_path, monkeypatch):
    src = make_torrent(tmp_path / "p.torrent", "film.mkv", length=500, private=True,
                       trackers=["https://private.example/pk/announce"])
    t = T.DownloadTask(src, str(tmp_path / "torrent"), filename="p.torrent")
    d = _Daemon([_done(str(tmp_path / "film.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    kind, opts = d.added[0]
    assert kind == "torrent"
    assert opts["bt-tracker"] == ""
    assert opts["bt-exclude-tracker"].split(",") == torrent.PUBLIC_TRACKERS
    assert t.status == T.COMPLETED


def test_a_public_torrent_file_is_added_as_before(tmp_path, monkeypatch):
    src = make_torrent(tmp_path / "q.torrent", "film.mkv", length=500)
    t = T.DownloadTask(src, str(tmp_path / "torrent"), filename="q.torrent")
    d = _Daemon([_done(str(tmp_path / "film.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert d.added == [("torrent", dict(NO_COPY, dir=str(tmp_path)))]


def test_a_torrent_file_the_app_sorts_starts_in_its_category_folder(tmp_path, monkeypatch):
    base = tmp_path / "dl"
    base.mkdir()
    src = make_torrent(tmp_path / "s.torrent", "Show.S01", files=[("ep1.mkv", 500)])
    t = T.DownloadTask(src, str(base / "torrent"), filename="s.torrent")
    t.sort_base = str(base)
    d = _Daemon([_done(str(base / "Video" / "Show.S01" / "ep1.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert d.added[0][1]["dir"] == str(base / "Video")
    assert t.save_path == str(base / "Video" / "Show.S01")


# ---- a magnet: the payload is held until its metadata has been read -----------

def test_a_magnet_asks_aria2_to_hold_the_payload(tmp_path, monkeypatch):
    t = _magnet_task(tmp_path)
    d = _Daemon([_done(str(tmp_path / "x.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert d.added[0] == ("magnet", {"dir": str(tmp_path), "pause-metadata": "true"})


def test_a_magnet_is_filed_when_its_metadata_arrives_and_only_then_started(tmp_path, monkeypatch):
    base = tmp_path / "dl"
    base.mkdir()
    t = _magnet_task(base)
    d = _Daemon([META_STAGE,
                 _metadata_arrives(base, name="Show.S01", files=[("ep1.mkv", 500)]),
                 _done(str(base / "Video" / "Show.S01" / "ep1.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert [k for k, _ in d.added] == ["magnet", "torrent"]
    assert d.added[1][1] == dict(NO_COPY, dir=str(base / "Video")), \
        "the payload should be added again in its category folder, as an ordinary torrent"
    assert d.did("aria2.forceRemove", "held"), "the held payload was left in the daemon"
    assert not d.did("aria2.unpause", "held"), "it was started in the wrong folder first"
    assert t.status == T.COMPLETED
    assert t.save_path == str(base / "Video" / "Show.S01")
    assert not (base / (IH + ".torrent")).exists(), "the metadata was left in the download folder"
    assert os.path.isfile(os.path.join(torrent.metadata_dir(), IH + ".torrent"))


def test_with_nothing_to_change_the_held_payload_is_simply_let_go(tmp_path, monkeypatch):
    """The user chose this folder: a public torrent stays where it was added."""
    t = _magnet_task(tmp_path)
    t.sort_base = ""
    d = _Daemon([_metadata_arrives(tmp_path, name="Show.S01", files=[("ep1.mkv", 500)]),
                 _done(str(tmp_path / "Show.S01" / "ep1.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert [k for k, _ in d.added] == ["magnet"]
    assert d.did("aria2.unpause", "held")
    assert not d.did("aria2.forceRemove", "held")
    assert t.status == T.COMPLETED and t.save_path == str(tmp_path / "Show.S01")


def test_a_payload_whose_metadata_was_not_saved_is_let_go_too(tmp_path, monkeypatch):
    """No file to read means nothing is known; holding it for ever would be worse."""
    t = _magnet_task(tmp_path)
    d = _Daemon([{"status": "complete", "followedBy": ["held"],
                  "files": [{"path": "[METADATA]abc"}]},
                 _done(str(tmp_path / "x.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert d.did("aria2.unpause", "held")
    assert t.status == T.COMPLETED


def test_a_magnet_found_to_be_private_restarts_without_the_public_trackers(tmp_path, monkeypatch):
    t = _magnet_task(tmp_path, trackers=["https://private.example/pk/announce"])
    t.sort_base = ""                                   # the folder is not what changes
    d = _Daemon([_metadata_arrives(tmp_path, name="film.mkv", length=500, private=True),
                 _done(str(tmp_path / "film.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert [k for k, _ in d.added] == ["magnet", "torrent"]
    opts = d.added[1][1]
    assert opts["dir"] == str(tmp_path)
    assert opts["bt-tracker"] == "" and opts["bt-exclude-tracker"].split(",") == torrent.PUBLIC_TRACKERS
    assert opts["rpc-save-upload-metadata"] == "false"
    assert d.did("aria2.forceRemove", "held") and not d.did("aria2.unpause", "held")
    assert t.status == T.COMPLETED


def test_a_magnet_known_to_be_private_never_gets_them_at_all(tmp_path, monkeypatch):
    """Its metadata is on disk from an earlier run, so the very first add says so."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "appdata"))
    make_torrent(os.path.join(torrent.metadata_dir(), IH + ".torrent"),
                 "film.mkv", length=500, private=True)
    t = _magnet_task(tmp_path)
    t.sort_base = ""
    d = _Daemon([{"status": "complete", "followedBy": ["held"],
                  "files": [{"path": "[METADATA]abc"}]},
                 _done(str(tmp_path / "film.mkv"))])
    _start(t, d, tmp_path, monkeypatch)
    assert [k for k, _ in d.added] == ["magnet"]
    opts = d.added[0][1]
    assert opts["bt-tracker"] == "" and "bt-exclude-tracker" in opts
    assert d.did("aria2.unpause", "held"), "already added right: nothing to add again"


def test_a_magnet_whose_metadata_is_known_starts_in_its_category_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path / "appdata"))
    base = tmp_path / "dl"
    base.mkdir()
    make_torrent(os.path.join(torrent.metadata_dir(), IH + ".torrent"),
                 "Album", files=[("01.flac", 500)])
    t = _magnet_task(base)
    d = _Daemon([_done(str(base / "Music" / "Album" / "01.flac"))])
    _start(t, d, tmp_path, monkeypatch)
    assert d.added[0][1]["dir"] == str(base / "Music")
    assert t.save_path == str(base / "Music" / "Album")


# ---- a private torrent the daemon already holds --------------------------------

def _private_file_task(tmp_path):
    src = make_torrent(tmp_path / "p.torrent", "film.mkv", length=500, private=True,
                       trackers=["https://private.example/pk/announce"])
    t = T.DownloadTask(src, str(tmp_path / "torrent"), filename="p.torrent")
    return t, torrent.torrent_infohash(src)


def test_an_entry_still_carrying_public_trackers_is_not_reattached(tmp_path, monkeypatch):
    """Added by a version that did not know: its list cannot be mended, only
    replaced."""
    t, ih = _private_file_task(tmp_path)
    old = {"status": "paused", "infoHash": ih, "dir": str(tmp_path),
           "bittorrent": {"announceList": [["https://private.example/pk/announce"],
                                           [torrent.PUBLIC_TRACKERS[0].upper()]]}}
    d = _Daemon([_done(str(tmp_path / "film.mkv"))], entries={"old": old})
    _start(t, d, tmp_path, monkeypatch)
    assert d.did("aria2.forceRemove", "old")
    assert not d.did("aria2.unpause", "old")
    assert d.added and d.added[0][1]["bt-tracker"] == ""


def test_a_clean_entry_is_reattached(tmp_path, monkeypatch):
    t, ih = _private_file_task(tmp_path)
    clean = {"status": "paused", "infoHash": ih, "dir": str(tmp_path),
             "bittorrent": {"announceList": [["https://private.example/pk/announce"]]},
             "completedLength": "500", "totalLength": "500",
             "files": [{"path": str(tmp_path / "film.mkv")}]}
    d = _Daemon([], entries={"mine": clean})
    (tmp_path / "film.mkv").write_bytes(b"x")
    clean["status"] = "paused"

    def finish(method, *params, **kw):
        if method == "aria2.unpause":
            clean["status"] = "complete"
        return _Daemon.call(d, method, *params, **kw)
    d_call, d.call = d.call, finish
    _start(t, d, tmp_path, monkeypatch)
    assert d.did("aria2.unpause", "mine")
    assert d.added == [], "a healthy registration was thrown away"
    assert t.status == T.COMPLETED
