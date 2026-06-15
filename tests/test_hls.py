"""HLS grabber: detection, master->variant, AES-128 decrypt, concat, cancel.
Local playlist server, no external network."""
import os
import http.server
import threading

import pytest

import task as T
import hls
from downloader import Downloader


@pytest.fixture
def hls_server(aes_tools):
    segs = [bytes([i]) * 4096 for i in range(1, 6)]
    plain_total = b"".join(segs)
    key = bytes(range(16))
    enc_segs = [aes_tools.encrypt(s, key, i) for i, s in enumerate(segs)]

    master = ("#EXTM3U\n"
              "#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow.m3u8\n"
              "#EXT-X-STREAM-INF:BANDWIDTH=2400000\nhigh.m3u8\n")

    def media(prefix, encrypted=False):
        out = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n"
        if encrypted:
            out += '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
        for i in range(5):
            out += f"#EXTINF:4.0,\n{prefix}{i}.ts\n"
        return out + "#EXT-X-ENDLIST\n"

    routes = {
        "master.m3u8": (master.encode(), "application/vnd.apple.mpegurl"),
        "high.m3u8": (media("seg").encode(), "application/vnd.apple.mpegurl"),
        "low.m3u8": (media("seg").encode(), "application/vnd.apple.mpegurl"),
        "aes.m3u8": (media("enc", True).encode(), "application/vnd.apple.mpegurl"),
        "live.m3u8": (b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n", "application/vnd.apple.mpegurl"),
        "key.bin": (key, "application/octet-stream"),
    }
    for i in range(5):
        routes[f"seg{i}.ts"] = (segs[i], "video/mp2t")
        routes[f"enc{i}.ts"] = (enc_segs[i], "video/mp2t")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = self.path.split("?")[0].lstrip("/")
            if p in routes:
                body, ct = routes[p]
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    srv = type("S", (), {})()
    srv.base = f"http://127.0.0.1:{port}"
    srv.plain_total = plain_total
    yield srv
    httpd.shutdown()


@pytest.mark.parametrize("url,fn,ct,exp", [
    ("http://x/v.m3u8", "", "", True),
    ("", "", "application/vnd.apple.mpegurl", True),
    ("http://x/v.m3u8?token=1", "", "", True),
    ("http://x/v.mp4", "v.mp4", "video/mp4", False),
    ("http://x/v.bin", "", "", False),
])
def test_is_hls(url, fn, ct, exp):
    assert hls.is_hls(url, fn, ct) is exp


def test_master_variant_concat(hls_server, tmp_path):
    dst = str(tmp_path / "vid.m3u8")
    t = T.DownloadTask(f"{hls_server.base}/master.m3u8", dst, filename="vid.m3u8")
    Downloader(t).run()
    assert t.status == T.COMPLETED
    assert t.save_path.endswith(".ts")          # rewritten from .m3u8
    assert open(t.save_path, "rb").read() == hls_server.plain_total
    assert t.percent == 100 and t.seg_total == 5


def test_aes128_decrypt(hls_server, tmp_path):
    dst = str(tmp_path / "enc.m3u8")
    t = T.DownloadTask(f"{hls_server.base}/aes.m3u8", dst, filename="enc.m3u8")
    Downloader(t).run()
    assert t.status == T.COMPLETED
    assert open(t.save_path, "rb").read() == hls_server.plain_total


def test_cancel_midstream(hls_server, tmp_path):
    dst = str(tmp_path / "c.m3u8")
    t = T.DownloadTask(f"{hls_server.base}/high.m3u8", dst, filename="c.m3u8")
    t.request_cancel()
    Downloader(t).run()
    assert t.status == T.CANCELLED
    assert not os.path.exists(t.save_path + ".hfdownload")


def test_live_stream_errors_clearly(hls_server, tmp_path):
    dst = str(tmp_path / "live.m3u8")
    t = T.DownloadTask(f"{hls_server.base}/live.m3u8", dst, filename="live.m3u8")
    Downloader(t).run()
    assert t.status == T.ERROR
    assert "segment" in t.error.lower() or "live" in t.error.lower()


def test_title_filename_becomes_ts(hls_server, tmp_path):
    """A title-based .m3u8 name (from the grabber) is saved as <title>.ts."""
    import os
    dst = str(tmp_path / "My Holiday Clip.m3u8")
    t = T.DownloadTask(f"{hls_server.base}/master.m3u8", dst,
                       filename="My Holiday Clip.m3u8")
    Downloader(t).run()
    assert t.status == T.COMPLETED
    assert os.path.basename(t.save_path) == "My Holiday Clip.ts"
    assert t.filename == "My Holiday Clip.ts"
    assert open(t.save_path, "rb").read() == hls_server.plain_total
