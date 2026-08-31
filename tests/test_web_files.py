"""Handing a finished download to the phone.

This is the point of the web client: iOS has no torrent client, so the PC
fetches it and the phone collects the file from here. That makes these routes
the only ones that put real file bytes on the network, so the tests care most
about what they refuse.
"""
import os

import pytest

import task as T
import utils
import web_auth
from api_server import create_app, servable_files, MAX_LISTED_FILES


PW = "correct horse battery"
LAN = "192.168.1.50"


class _Queue:
    def __init__(self):
        self.tasks = []

    def get_task(self, tid):
        return next((t for t in self.tasks if t.id == tid), None)

    def add_task(self, t, start=True):
        self.tasks.append(t)
        return t


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    dl = tmp_path / "downloads"
    dl.mkdir()
    q = _Queue()
    app = create_app(q, str(dl), pending=None, token="tok")
    app.config["TESTING"] = True
    c = app.test_client()

    web_auth.set_password(PW, user="admin")
    web_auth.set_enabled(True)
    r = c.post("/api/login", json={"username": "admin", "password": PW},
               environ_overrides={"REMOTE_ADDR": LAN})
    assert r.status_code == 200
    return c, q, dl


def _get(c, path, **kw):
    return c.get(path, environ_overrides={"REMOTE_ADDR": LAN}, **kw)


def _task(q, path, status=T.COMPLETED):
    t = T.DownloadTask("https://example.test/x", str(path),
                       filename=os.path.basename(str(path)))
    t.status = status
    q.tasks.append(t)
    return t


# ---- the happy path --------------------------------------------------------
def test_a_finished_file_can_be_taken(env):
    c, q, dl = env
    f = dl / "movie.mkv"
    f.write_bytes(b"video-bytes-here")
    t = _task(q, f)

    r = _get(c, f"/api/downloads/{t.id}/file")
    assert r.status_code == 200
    assert r.get_data() == b"video-bytes-here"
    assert "attachment" in r.headers["Content-Disposition"]
    assert "movie.mkv" in r.headers["Content-Disposition"]


def test_it_answers_range_requests(env):
    """Without this a phone cannot resume a dropped 4 GB download, and Safari
    cannot stream a video without pulling all of it first."""
    c, q, dl = env
    f = dl / "movie.mkv"
    f.write_bytes(b"0123456789")
    t = _task(q, f)

    full = _get(c, f"/api/downloads/{t.id}/file")
    assert full.headers.get("Accept-Ranges") == "bytes"

    r = _get(c, f"/api/downloads/{t.id}/file", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.get_data() == b"2345"
    assert r.headers["Content-Range"] == "bytes 2-5/10"


def test_inline_is_offered_for_playing_rather_than_saving(env):
    c, q, dl = env
    f = dl / "clip.mp4"
    f.write_bytes(b"x")
    t = _task(q, f)
    r = _get(c, f"/api/downloads/{t.id}/file?inline=1")
    assert "attachment" not in r.headers.get("Content-Disposition", "")


def test_a_multi_file_torrent_lists_its_contents(env):
    c, q, dl = env
    show = dl / "Some.Show.S01"
    (show / "extras").mkdir(parents=True)
    (show / "E01.mkv").write_bytes(b"a" * 5)
    (show / "E02.mkv").write_bytes(b"bb" * 5)
    (show / "extras" / "notes.txt").write_bytes(b"c")
    t = _task(q, show)

    r = _get(c, f"/api/downloads/{t.id}/files")
    body = r.get_json()
    assert r.status_code == 200
    assert body["ready"] is True
    names = [f["name"] for f in body["files"]]
    assert names == ["E01.mkv", "E02.mkv", "notes.txt"]
    assert body["files"][0]["size"] == 5
    assert body["files"][2]["path"] == "extras/notes.txt"

    got = _get(c, f"/api/downloads/{t.id}/file/1")
    assert got.get_data() == b"bb" * 5


def test_the_listing_never_reveals_where_things_are_on_disk(env):
    """The phone needs a name and an index. This PC's directory layout is not
    the browser's business."""
    c, q, dl = env
    d = dl / "pack"
    d.mkdir()
    (d / "a.bin").write_bytes(b"a")
    t = _task(q, d)
    body = _get(c, f"/api/downloads/{t.id}/files").get_data(as_text=True)
    assert str(dl) not in body
    assert "C:\\\\" not in body


# ---- what it refuses -------------------------------------------------------
def test_an_unfinished_download_is_not_served(env):
    """Half a file looks like a whole one once it is on the phone."""
    c, q, dl = env
    f = dl / "part.iso"
    f.write_bytes(b"incomplete")
    t = _task(q, f, status=T.DOWNLOADING)
    r = _get(c, f"/api/downloads/{t.id}/file")
    assert r.status_code == 409
    assert r.get_json()["code"] == "not-ready"


def test_partial_files_are_left_out_of_the_listing(env):
    c, q, dl = env
    d = dl / "pack"
    d.mkdir()
    (d / "done.bin").write_bytes(b"a")
    (d / "busy.bin.hfdownload").write_bytes(b"b")
    (d / "busy.bin.aria2").write_bytes(b"c")
    t = _task(q, d)
    names = [f["name"] for f in _get(c, f"/api/downloads/{t.id}/files").get_json()["files"]]
    assert names == ["done.bin"]


def test_signing_out_stops_the_files_too(env):
    c, q, dl = env
    f = dl / "movie.mkv"
    f.write_bytes(b"x")
    t = _task(q, f)
    assert _get(c, f"/api/downloads/{t.id}/file").status_code == 200
    c.post("/api/logout", environ_overrides={"REMOTE_ADDR": LAN})
    assert _get(c, f"/api/downloads/{t.id}/file").status_code == 401


def test_switching_the_client_off_stops_the_files(env):
    c, q, dl = env
    f = dl / "movie.mkv"
    f.write_bytes(b"x")
    t = _task(q, f)
    web_auth.set_enabled(False)
    r = _get(c, f"/api/downloads/{t.id}/file")
    assert r.status_code == 403
    assert r.get_json()["code"] == "disabled"


@pytest.mark.parametrize("index", ["-1", "999"])
def test_an_index_outside_the_listing_is_a_404(env, index):
    c, q, dl = env
    f = dl / "movie.mkv"
    f.write_bytes(b"x")
    t = _task(q, f)
    r = _get(c, f"/api/downloads/{t.id}/file/{index}")
    assert r.status_code == 404


def test_the_index_is_the_only_input_so_there_is_no_path_to_traverse(env):
    """Callers pick a file by position in a list the server built from the
    task's own save_path. There is no caller-supplied path anywhere in the
    route, which is why traversal has nothing to work with."""
    c, q, dl = env
    f = dl / "movie.mkv"
    f.write_bytes(b"x")
    t = _task(q, f)
    for attempt in ("../../../../etc/passwd", "..%2f..%2fsecret", "0/../../x"):
        r = _get(c, f"/api/downloads/{t.id}/file/{attempt}")
        assert r.status_code in (404, 400, 405), attempt


def test_a_missing_file_is_reported_not_crashed(env):
    c, q, dl = env
    t = _task(q, dl / "deleted.mkv")
    r = _get(c, f"/api/downloads/{t.id}/file")
    assert r.status_code == 404
    assert r.get_json()["code"] == "gone"


def test_an_unknown_download_is_a_404(env):
    c, _, _ = env
    assert _get(c, "/api/downloads/nope/file").status_code == 404
    assert _get(c, "/api/downloads/nope/files").status_code == 404


# ---- servable_files itself -------------------------------------------------
@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks here")
def test_a_symlink_out_of_the_folder_is_dropped(tmp_path):
    """A torrent is untrusted content — it can contain a symlink pointing at
    anything on this disk, and the walk must not follow it out."""
    secret = tmp_path / "secret.txt"
    secret.write_text("password file")
    d = tmp_path / "torrent"
    d.mkdir()
    (d / "real.bin").write_bytes(b"ok")
    try:
        os.symlink(str(secret), str(d / "escape.txt"))
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")

    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(d), filename="torrent")
    names = [f["name"] for f in servable_files(t)]
    assert names == ["real.bin"], "a symlink escaped the download folder"


def test_a_long_listing_stops_rather_than_growing(tmp_path):
    d = tmp_path / "many"
    d.mkdir()
    for i in range(MAX_LISTED_FILES + 25):
        (d / f"f{i:04d}.bin").write_bytes(b"x")
    t = T.DownloadTask("https://e.test/x", str(d), filename="many")
    assert len(servable_files(t)) == MAX_LISTED_FILES


def test_a_task_with_no_path_serves_nothing(tmp_path):
    t = T.DownloadTask("https://e.test/x", "", filename="x")
    assert servable_files(t) == []


def test_the_walk_drops_anything_that_resolves_outside_the_root(tmp_path, monkeypatch):
    """The same guard as the symlink test, reachable without needing symlink
    permission: os.walk is made to report a file from outside the download
    folder, and it must not survive the containment check."""
    import api_server
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"nope")
    d = tmp_path / "torrent"
    d.mkdir()
    (d / "real.bin").write_bytes(b"ok")

    real_walk = os.walk

    def fake_walk(top, *a, **k):
        for base, dirs, names in real_walk(top, *a, **k):
            yield base, dirs, names + ["../elsewhere/secret.txt"]

    monkeypatch.setattr(api_server.os, "walk", fake_walk)
    t = T.DownloadTask("https://e.test/x", str(d), filename="torrent")
    names = [f["name"] for f in servable_files(t)]
    assert names == ["real.bin"], "a path outside the root was served"
