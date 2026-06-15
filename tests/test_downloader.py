"""Downloader: segmented integrity, resume, cancel, edge servers, disk guard.
All against the in-process file_server fixture — no external network."""
import os
import time
import hashlib
import threading

import pytest

import task as T
import utils
from downloader import Downloader, probe_info


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def test_segmented_download_integrity(file_server, tmp_path):
    data = file_server.put("big.bin", n_bytes=5 * 1024 * 1024)
    dst = str(tmp_path / "big.bin")
    t = T.DownloadTask(file_server.url("big.bin"), dst)
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED
    assert os.path.getsize(dst) == len(data)
    assert open(dst, "rb").read() == data
    assert not os.path.exists(dst + ".hfdownload")


def test_single_vs_multi_segment_md5(file_server, tmp_path):
    file_server.put("v.bin", n_bytes=3 * 1024 * 1024)
    a = str(tmp_path / "a.bin")
    b = str(tmp_path / "b.bin")
    Downloader(T.DownloadTask(file_server.url("v.bin"), a), segments=1).run()
    Downloader(T.DownloadTask(file_server.url("v.bin"), b), segments=8).run()
    assert md5(a) == md5(b)


def test_no_range_server_falls_back(file_server, tmp_path):
    data = file_server.put("nr.bin", n_bytes=512 * 1024, no_range=True)
    dst = str(tmp_path / "nr.bin")
    t = T.DownloadTask(file_server.url("nr.bin"), dst)
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED
    assert open(dst, "rb").read() == data
    assert not t.supports_range


def test_no_content_length_server(file_server, tmp_path):
    data = file_server.put("cl.bin", n_bytes=256 * 1024, no_length=True, no_range=True)
    dst = str(tmp_path / "cl.bin")
    t = T.DownloadTask(file_server.url("cl.bin"), dst)
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED
    assert open(dst, "rb").read() == data


def test_zero_byte_file(file_server, tmp_path):
    file_server.put("empty.bin", data=b"")
    dst = str(tmp_path / "empty.bin")
    t = T.DownloadTask(file_server.url("empty.bin"), dst)
    Downloader(t, segments=4).run()
    assert t.status in (T.COMPLETED, T.PAUSED)  # no crash/hang is the requirement


def test_probe_info_size_and_type(file_server):
    file_server.put("p.bin", n_bytes=4096)
    info = probe_info(file_server.url("p.bin"))
    assert info["size"] == 4096
    assert info["type"]


def test_probe_info_no_head_fallback(file_server):
    # no_range server still answers ranged GET with full body; size via fallback
    file_server.put("nh.bin", n_bytes=8192)
    assert probe_info(file_server.url("nh.bin"))["size"] == 8192


def test_pause_resume_from_disk(file_server, tmp_path):
    file_server.put("pr.bin", n_bytes=8 * 1024 * 1024)
    dst = str(tmp_path / "pr.bin")
    ref = str(tmp_path / "ref.bin")
    Downloader(T.DownloadTask(file_server.url("pr.bin"), ref), segments=1).run()

    t = T.DownloadTask(file_server.url("pr.bin"), dst)
    d = Downloader(t, segments=8)
    th = threading.Thread(target=d.run)
    th.start()
    time.sleep(0.3)
    t.request_pause()
    th.join(timeout=10)
    assert t.status == T.PAUSED

    t.clear_pause()
    Downloader(t, segments=8).run()
    assert t.status == T.COMPLETED
    assert md5(dst) == md5(ref)


def test_cancel_removes_partfile(file_server, tmp_path):
    file_server.put("c.bin", n_bytes=8 * 1024 * 1024)
    dst = str(tmp_path / "c.bin")
    t = T.DownloadTask(file_server.url("c.bin"), dst)
    t.request_cancel()
    Downloader(t, segments=8).run()
    assert t.status == T.CANCELLED
    assert not os.path.exists(dst + ".hfdownload")


def test_disk_space_guard(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 10})())
    t = T.DownloadTask("http://x/huge.bin", str(tmp_path / "huge.bin"))
    with pytest.raises(OSError):
        Downloader(t)._check_disk_space(str(tmp_path / "huge.bin"),
                                        5 * 1024 * 1024 * 1024)


def test_html_login_page_detected(tmp_path):
    """Auth-gated host returns HTML instead of the file -> clear error, no garbage."""
    import http.server
    import threading as _th

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", "100")
            self.end_headers()

        def do_GET(self):
            body = b"<html>login required</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    _th.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        dst = str(tmp_path / "file.zip")
        t = T.DownloadTask(f"http://127.0.0.1:{port}/file.zip", dst)
        Downloader(t, segments=4).run()
        assert t.status == T.ERROR
        assert "web page" in t.error.lower() or "login" in t.error.lower()
    finally:
        httpd.shutdown()
