"""One file from several links (asked 2026-10-30, "A"): the parts are spread
over the download's own link and the ones the user added."""
import os

import task as T
from downloader import Downloader, check_mirror


def _data(n=2 * 1024 * 1024):
    return os.urandom(n)


def test_parts_come_from_both_links_and_the_file_is_whole(media_server, tmp_path):
    data = media_server.put("a/file.bin", _data())
    media_server.put("b/file.bin", data)
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"),
                       headers={"Cookie": "session=first-site"})
    t.mirrors = [media_server.url("b/file.bin")]
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert open(t.save_path, "rb").read() == data
    by = {p: [r for r in media_server.log if r["path"] == p and r["range"]]
          for p in ("a/file.bin", "b/file.bin")}
    assert by["a/file.bin"] and by["b/file.bin"], "both links must serve parts"
    # the first site's cookie is not handed to another server
    assert all("Cookie" not in r["headers"] for r in by["b/file.bin"])


def test_a_link_to_another_file_is_dropped_and_the_download_goes_on(media_server, tmp_path):
    data = media_server.put("a/file.bin", _data())
    media_server.put("b/other.bin", _data(len(data) + 1))
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"))
    t.mirrors = [media_server.url("b/other.bin")]
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert open(t.save_path, "rb").read() == data
    assert any("Stopped using the link" in e["message"] for e in t.events)


def test_a_dead_link_is_dropped_and_the_download_goes_on(media_server, tmp_path):
    data = media_server.put("a/file.bin", _data())
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"))
    t.mirrors = [media_server.url("gone/file.bin")]          # 404
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert open(t.save_path, "rb").read() == data


def test_a_link_is_checked_before_it_is_added(media_server, tmp_path):
    data = media_server.put("a/file.bin", _data())
    media_server.put("b/file.bin", data)
    media_server.put("c/file.bin", data + b"x")
    media_server.put("d/file.bin", data)
    media_server.no_range.add("d/file.bin")
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"))
    assert check_mirror(t, media_server.url("b/file.bin")) is None
    assert "not the same" in check_mirror(t, media_server.url("c/file.bin"))
    assert "cannot send parts" in check_mirror(t, media_server.url("d/file.bin"))
    assert "already" in check_mirror(t, t.url)
    assert "http" in check_mirror(t, "ftp://x/file.bin")


def test_the_links_are_saved_with_the_download():
    t = T.DownloadTask("https://a.example/f.bin", "C:/dl/f.bin")
    t.mirrors = ["https://b.example/f.bin"]
    assert T.DownloadTask.from_dict(t.to_dict()).mirrors == ["https://b.example/f.bin"]


def test_the_menu_adds_a_checked_link_and_refuses_a_wrong_one(media_server, tmp_path, monkeypatch):
    """"Add another link…": asked on a thread, taken up on the window's."""
    import collections
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import gui2.app_actions as A

    data = media_server.put("a/file.bin", _data())
    media_server.put("b/file.bin", data)
    media_server.put("c/file.bin", data + b"x")
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"))
    said, saved = [], []

    class Win:
        _mirror_checks = collections.deque()
        _toasts = type("Toasts", (), {"show": lambda self, *a: said.append(a)})()
        _save_state = lambda self: saved.append(1)
        _add_mirror = A.ActionsMixin._add_mirror
        _finish_mirror_checks = A.ActionsMixin._finish_mirror_checks

    w = Win()
    for link in ("b/file.bin", "c/file.bin"):
        monkeypatch.setattr(A.QInputDialog, "getText",
                            staticmethod(lambda *a, u=media_server.url(link): (u, True)))
        w._add_mirror(t)
        end = time.monotonic() + 15
        while not w._mirror_checks and time.monotonic() < end:
            time.sleep(0.02)
        w._finish_mirror_checks()
    assert t.mirrors == [media_server.url("b/file.bin")]
    assert said[-1][0] == "error" and saved == [1]


def test_a_busy_extra_link_hands_that_part_back_and_is_kept(media_server, tmp_path):
    """Mirror sites cap connections a visitor: "too many" is not a dead link."""
    data = media_server.put("a/file.bin", _data(8 * 1024 * 1024))
    media_server.put("b/file.bin", data)
    media_server.limit, media_server.rate = 3, 4 * 1024 * 1024
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"))
    t.mirrors = [media_server.url("b/file.bin")]
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert open(t.save_path, "rb").read() == data
    assert media_server.stats["refused"], "the cap must have been met"
    assert not any("Stopped using" in e["message"] for e in t.events)


def test_when_the_first_link_expires_the_extra_link_serves_the_rest(media_server, tmp_path):
    data = media_server.put("a/file.bin", _data())
    media_server.put("b/file.bin", data)
    media_server.forbid_range.add("a/file.bin")         # every part asked of it: 403
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"))
    t.mirrors = [media_server.url("b/file.bin")]
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert open(t.save_path, "rb").read() == data


def test_a_link_pasted_twice_is_added_once_and_one_connection_is_said(tmp_path):
    import collections
    import gui2.app_actions as A
    said = []

    class Win:
        _toasts = type("Toasts", (), {"show": lambda self, *a: said.append(a)})()
        _save_state = lambda self: None
        _finish_mirror_checks = A.ActionsMixin._finish_mirror_checks

    t = T.DownloadTask("https://a.example/f.bin", str(tmp_path / "f.bin"))
    t.segments = [T.Segment(0, 0, 99)]
    w = Win()
    w._mirror_checks = collections.deque([(t, "https://b.example/f.bin", None)] * 2)
    w._finish_mirror_checks()
    assert t.mirrors == ["https://b.example/f.bin"]
    assert len(said) == 1 and "expires" in said[0][2]
    t.supports_range = False
    w._mirror_checks.append((t, "https://c.example/f.bin", None))
    w._finish_mirror_checks()
    assert t.mirrors == ["https://b.example/f.bin"] and said[-1][0] == "error"


def test_an_extra_links_streams_do_not_make_the_first_links_403_a_busy_server():
    """A 403 from the first link while only an extra link streams is the
    first link's own answer (expired), not its server being full."""
    import time
    t = T.DownloadTask("https://a.example/f.bin", "C:/dl/f.bin")
    t.mirrors = ["https://b.example/f.bin"]
    d = Downloader(t, segments=8)
    d._streams(+1, "https://b.example/f.bin")
    assert not d._pushed_back(time.monotonic(), t.url)
    assert d._pushed_back(time.monotonic(), "https://b.example/f.bin")
    d._streams(+1, t.url)
    assert d._pushed_back(time.monotonic(), t.url)


def test_an_extra_link_that_refuses_every_part_is_dropped(media_server, tmp_path):
    data = media_server.put("a/file.bin", _data(8 * 1024 * 1024))
    media_server.put("b/file.bin", data)
    media_server.forbid_range.add("b/file.bin")
    t = T.DownloadTask(media_server.url("a/file.bin"), str(tmp_path / "file.bin"))
    t.mirrors = [media_server.url("b/file.bin")]
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED, (t.status, t.error)
    assert open(t.save_path, "rb").read() == data
    assert any("Stopped using" in e["message"] for e in t.events)


def test_before_it_starts_the_message_follows_how_the_file_will_split(tmp_path, monkeypatch):
    import collections
    import gui2.app_actions as A
    said = []

    class Win:
        queue = type("Q", (), {"segments": 8})()
        _toasts = type("Toasts", (), {"show": lambda self, *a: said.append(a)})()
        _save_state = lambda self: None
        _finish_mirror_checks = A.ActionsMixin._finish_mirror_checks

    w = Win()
    for size, words in ((500 * 1024, "expires"), (100 * 1024 * 1024, "also come from")):
        t = T.DownloadTask("https://a.example/f.bin", str(tmp_path / "f.bin"))
        t.total_size = size
        w._mirror_checks = collections.deque([(t, "https://b.example/f.bin", None)])
        w._finish_mirror_checks()
        assert words in said[-1][2]
    import utils
    monkeypatch.setattr(utils, "MAX_CONNECTIONS", 1)     # Settings' cap: one connection
    t = T.DownloadTask("https://a.example/f.bin", str(tmp_path / "f.bin"))
    t.total_size = 100 * 1024 * 1024
    w._mirror_checks = collections.deque([(t, "https://b.example/f.bin", None)])
    w._finish_mirror_checks()
    assert "expires" in said[-1][2]
