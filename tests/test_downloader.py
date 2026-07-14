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


def test_format_disk_error_includes_free_space_for_enospc(tmp_path):
    """ENOSPC errors get a 'disk full -- X MiB free' message instead of the
    bare OSError dump, so the user knows to pick a different folder."""
    import errno
    from downloader import Downloader
    e = OSError(errno.ENOSPC, "no space")
    msg = Downloader._format_disk_error(e, str(tmp_path / "x.hfdownload"))
    assert "disk full" in msg.lower()
    assert "miB" in msg or "Settings" in msg


def test_format_disk_error_generic_passthrough(tmp_path):
    """Non-ENOSPC OSErrors fall through to the original message."""
    from downloader import Downloader
    e = OSError(13, "Permission denied")
    msg = Downloader._format_disk_error(e, str(tmp_path / "x"))
    assert "permission" in msg.lower()


def test_auto_segments_zero_completes(file_server, tmp_path):
    """Auto mode (segments=0) must NOT deadlock — the connection cap is derived
    from the actual built segment count, not the requested 0."""
    data = file_server.put("auto.bin", n_bytes=2 * 1024 * 1024)
    dst = str(tmp_path / "auto.bin")
    t = T.DownloadTask(file_server.url("auto.bin"), dst)
    dl = Downloader(t, segments=0)
    done = threading.Event()
    th = threading.Thread(target=lambda: (dl.run(), done.set()), daemon=True)
    th.start()
    assert done.wait(timeout=30), "Auto-segment download hung (connection gate deadlock)"
    assert t.status == T.COMPLETED
    assert open(dst, "rb").read() == data


def test_finalize_failure_marks_error_not_completed(file_server, tmp_path, monkeypatch):
    """If the final move fails, the task must end ERROR (with the temp kept for
    retry), NOT COMPLETED with no file at the destination."""
    import shutil
    file_server.put("f.bin", n_bytes=1 * 1024 * 1024)
    dst = str(tmp_path / "f.bin")
    t = T.DownloadTask(file_server.url("f.bin"), dst)

    real_move = shutil.move
    def boom(src, dstp, *a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(shutil, "move", boom)

    Downloader(t, segments=4).run()
    assert t.status == T.ERROR
    assert "disk" in t.error.lower() or "space" in t.error.lower() or "folder" in t.error.lower()
    assert not os.path.exists(dst)                 # no half-written destination
    # the staged sibling must be cleaned up, not left behind
    assert not os.path.exists(dst + ".hfmove")


# ---- _verify_hash: digest stored for the drawer's Integrity section ----
def _mk_verify_task(tmp_path, data=b"hello world"):
    import task as T
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    t = T.DownloadTask("https://x/f.bin", str(p), filename="f.bin",
                       total_size=len(data))
    return t


def test_verify_hash_match_stores_digest(tmp_path, monkeypatch):
    import hashlib
    from downloader import Downloader
    t = _mk_verify_task(tmp_path)
    expected = hashlib.sha256(b"hello world").hexdigest()
    dl = Downloader(t)
    monkeypatch.setattr(dl, "_fetch_expected_hash", lambda: expected.upper())
    assert dl._verify_hash() is True
    assert t.hash_status == "ok"
    assert t.sha256 == expected


def test_verify_hash_mismatch(tmp_path, monkeypatch):
    from downloader import Downloader
    t = _mk_verify_task(tmp_path)
    dl = Downloader(t)
    monkeypatch.setattr(dl, "_fetch_expected_hash", lambda: "0" * 64)
    assert dl._verify_hash() is False
    assert t.hash_status == "fail"
    assert len(t.sha256) == 64


def test_verify_hash_no_sidecar_skips_unless_forced(tmp_path, monkeypatch):
    import hashlib
    from downloader import Downloader
    t = _mk_verify_task(tmp_path)
    dl = Downloader(t)
    monkeypatch.setattr(dl, "_fetch_expected_hash", lambda: None)
    # normal completion path: no sidecar -> no wasted hashing
    assert dl._verify_hash() is None
    assert t.hash_status == "nohash" and t.sha256 == ""
    # Force Recheck: digest computed anyway
    assert dl._verify_hash(always_digest=True) is None
    assert t.hash_status == "nohash"
    assert t.sha256 == hashlib.sha256(b"hello world").hexdigest()
