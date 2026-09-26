"""fMP4 HLS (CMAF): #EXT-X-MAP names an init segment that must lead the file.

Sites such as xhamster serve HLS this way: .m4s fragments that each start with
a moof box, and one init segment (ftyp + moov) named by #EXT-X-MAP. Joining the
fragments without the init gives a file no player can open - which is what the
downloader used to save, and under a .ts name besides."""
import http.server
import os
import threading

import pytest

import task as T
import utils
from downloader import Downloader

INIT = b"\x00\x00\x00\x18ftypiso6" + b"\x00" * 8 + b"\x00\x00\x00\x08moov"
SEGS = [b"\x00\x00\x00\x10moof" + bytes([i]) * 2048 for i in range(1, 6)]


@pytest.fixture
def fmp4_server():
    master = ("#EXTM3U\n"
              "#EXT-X-STREAM-INF:BANDWIDTH=900000\n720p.mp4/index.m3u8\n"
              "#EXT-X-STREAM-INF:BANDWIDTH=300000\n240p.mp4/index.m3u8\n")
    media = ("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n"
             "#EXT-X-MEDIA-SEQUENCE:1\n#EXT-X-PLAYLIST-TYPE:VOD\n"
             '#EXT-X-MAP:URI="init-v1-a1.mp4"\n'
             + "".join("#EXTINF:4.0,\nseg-%d-v1-a1.m4s\n" % (i + 1) for i in range(5))
             + "#EXT-X-ENDLIST\n")
    routes = {"master.m3u8": master.encode(),
              "720p.mp4/index.m3u8": media.encode(),
              "240p.mp4/index.m3u8": media.encode(),
              "720p.mp4/init-v1-a1.mp4": INIT}
    for i, seg in enumerate(SEGS):
        routes["720p.mp4/seg-%d-v1-a1.m4s" % (i + 1)] = seg

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = routes.get(self.path.split("?")[0].lstrip("/"))
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()


def test_the_init_segment_leads_the_file_and_it_is_an_mp4(fmp4_server, tmp_path):
    t = T.DownloadTask(fmp4_server + "/master.m3u8", str(tmp_path / "clip.m3u8"),
                       filename="clip.m3u8")
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert os.path.basename(t.save_path) == "clip.mp4"
    assert t.filename == "clip.mp4"
    assert open(t.save_path, "rb").read() == INIT + b"".join(SEGS)


def test_a_resumed_download_keeps_its_init_exactly_once(fmp4_server, tmp_path):
    t = T.DownloadTask(fmp4_server + "/master.m3u8", str(tmp_path / "clip.m3u8"),
                       filename="clip.m3u8")
    already = INIT + SEGS[0] + SEGS[1]
    with open(utils.temp_download_path(t.id), "wb") as f:
        f.write(already)
    t.seg_total, t.seg_done, t.downloaded = 5, 2, len(already)
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert t.seg_done == 5
    assert open(t.save_path, "rb").read() == INIT + b"".join(SEGS)
