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
