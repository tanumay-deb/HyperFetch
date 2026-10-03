"""A live stream has no end, so there is no finished video to download.

Seen 2026-10-03: the browser extension sent two .m3u8 links that were an ad
network's live streams, three segments on offer and no #EXT-X-ENDLIST. The
engine fetched those three and called the task Completed at 0.4 MB and 0.9 MB:
a second or two of the wrong thing, with nothing to say why.

A live playlist is a window that slides. As each segment is added at the end
the oldest drops off the front, and #EXT-X-MEDIA-SEQUENCE - the number of the
first segment - goes up by one for every segment dropped (Apple, "Live Playlist
(Sliding Window) Construction"). Looking again one target duration later and
seeing that number move on is the direct test.

Anything not shown to be live that way must download exactly as it did before:
a finished playlist, one marked #EXT-X-PLAYLIST-TYPE VOD or EVENT (an event
grows, but keeps everything so far), and VOD from servers that leave the end
tag out. Every playlist here comes from a local server.
"""
import http.server
import os
import re
import threading
import time

import pytest

import hls
import task as T
from downloader import Downloader


def seg(n):
    """Segment n's bytes - different for each n, so order and gaps show."""
    return bytes([n % 256]) * 512


def playlist(first, count, target=1, ptype=None, endlist=False, seq_tag=True,
             query=""):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:%d" % target]
    if ptype:
        lines.append("#EXT-X-PLAYLIST-TYPE:" + ptype)
    if seq_tag:
        lines.append("#EXT-X-MEDIA-SEQUENCE:%d" % first)
    for n in range(first, first + count):
        lines += ["#EXTINF:1.0,", "seg%d.ts%s" % (n, query)]
    if endlist:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def live(first, target=1):
    """A three-segment window that has moved on by one segment every time it
    is fetched - what the ad network served."""
    return lambda n: playlist(first + n, 3, target=target)


MASTER = ("#EXTM3U\n"
          "#EXT-X-STREAM-INF:BANDWIDTH=900000\nhi.m3u8\n"
          "#EXT-X-STREAM-INF:BANDWIDTH=300000\nlo.m3u8\n")


@pytest.fixture
def server():
    """Serves `routes` - path -> function of how many times that path has been
    asked for, returning the playlist - and any segN.ts. Logs every request."""
    routes, hits = {}, []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0].lstrip("/")
            hits.append((path, time.monotonic()))
            m = re.fullmatch(r"seg(\d+)\.ts", path)
            if path in routes:
                body = routes[path](sum(1 for p, _ in hits if p == path)).encode()
            elif m:
                body = seg(int(m.group(1)))
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    srv = type("S", (), {})()
    srv.base = "http://127.0.0.1:%d" % httpd.server_address[1]
    srv.routes = routes
    srv.fetches = lambda path: [at for p, at in hits if p == path]
    srv.segments_fetched = lambda: sum(1 for p, _ in hits if p.startswith("seg"))
    yield srv
    httpd.shutdown()
    httpd.server_close()


def task(srv, path, tmp_path):
    return T.DownloadTask(srv.base + "/" + path, str(tmp_path / "clip.m3u8"),
                          filename="clip.m3u8")


def grab(srv, path, tmp_path):
    t = task(srv, path, tmp_path)
    Downloader(t).run()
    return t


# ---------------------------------------------------------------- live
def test_a_live_stream_ends_with_a_clear_message_not_a_few_seconds(server, tmp_path):
    server.routes["live.m3u8"] = live(4820)
    t = grab(server, "live.m3u8", tmp_path)
    assert t.status == T.ERROR, (
        "a live window finished as %s with %d bytes" % (t.status, t.downloaded))
    assert t.error == hls.LIVE_STREAM
    assert server.segments_fetched() == 0
    assert not os.path.exists(t.save_path)


def test_a_live_stream_behind_a_master_playlist_is_caught_too(server, tmp_path):
    """The extension usually sends the master; the window is in the variant."""
    server.routes["master.m3u8"] = lambda n: MASTER
    server.routes["hi.m3u8"] = live(77)
    server.routes["lo.m3u8"] = live(77)
    t = grab(server, "master.m3u8", tmp_path)
    assert t.status == T.ERROR, t.status
    assert t.error == hls.LIVE_STREAM
    assert server.segments_fetched() == 0


def test_the_message_is_plain_and_whole_in_the_failure_toast():
    # the toast shows the first 60 characters of an error (gui2/app.py)
    assert "live stream" in hls.LIVE_STREAM.lower()
    assert len(hls.LIVE_STREAM) <= 60


def test_it_looks_again_no_sooner_than_one_target_duration(server, tmp_path):
    """RFC 8216 6.3.4: a client waits at least the target duration before it
    reloads a playlist. Sooner would find the same window and prove nothing."""
    server.routes["live.m3u8"] = live(500, target=2)
    t = grab(server, "live.m3u8", tmp_path)
    first, again = server.fetches("live.m3u8")[:2]
    assert again - first >= 1.95
    assert t.status == T.ERROR


def test_a_huge_target_duration_cannot_stall_the_start(server, tmp_path, monkeypatch):
    monkeypatch.setattr(hls, "LIVE_WAIT_MAX", 0.5)
    server.routes["live.m3u8"] = live(40, target=3600)
    began = time.monotonic()
    t = grab(server, "live.m3u8", tmp_path)
    assert time.monotonic() - began < 5
    assert t.status == T.ERROR and t.error == hls.LIVE_STREAM


@pytest.mark.parametrize("stop,status", [("request_cancel", T.CANCELLED),
                                         ("request_pause", T.PAUSED)])
def test_pause_and_cancel_do_not_wait_out_the_second_look(server, tmp_path,
                                                         stop, status):
    server.routes["live.m3u8"] = live(900, target=8)
    t = task(server, "live.m3u8", tmp_path)
    threading.Timer(0.3, getattr(t, stop)).start()
    began = time.monotonic()
    Downloader(t).run()
    assert t.status == status
    assert time.monotonic() - began < 3


# ---------------------------------------------------------------- not live
@pytest.mark.parametrize("route,first,count", [
    (lambda n: playlist(1, 5, endlist=True), 1, 5),
    (lambda n: playlist(1, 5, ptype="VOD"), 1, 5),
    # an event grows between fetches, but keeps everything so far
    (lambda n: playlist(1, 4 + n, ptype="EVENT"), 1, 5),
    # VOD from servers that leave the end tag out: long, numbered from 0
    (lambda n: playlist(0, 40), 0, 40),
    (lambda n: playlist(0, 40, seq_tag=False), 0, 40),
], ids=["finished", "vod-type-no-end-tag", "event", "no-end-tag-seq-0",
        "no-end-tag-no-seq"])
def test_what_is_not_live_downloads_as_before_with_one_fetch(server, tmp_path,
                                                            route, first, count):
    server.routes["v.m3u8"] = route
    t = grab(server, "v.m3u8", tmp_path)
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == b"".join(
        seg(n) for n in range(first, first + count))
    assert len(server.fetches("v.m3u8")) == 1     # no second look, no wait


@pytest.mark.parametrize("route", [
    lambda n: playlist(1, 5),
    lambda n: playlist(1, 5, query="?token=%d" % n),
], ids=["unchanged", "links-re-signed-each-fetch"])
def test_vod_without_an_end_tag_that_does_not_move_on_still_downloads(
        server, tmp_path, route):
    """Numbered from 1 with no end tag looks like a window on one fetch; the
    second look shows it standing still, so it is downloaded as before."""
    server.routes["v.m3u8"] = route
    t = grab(server, "v.m3u8", tmp_path)
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == b"".join(seg(n) for n in range(1, 6))


# ---------------------------------------------------------------- /probe
def test_probe_answers_as_before_for_a_live_master(server):
    """The extension's quality picker must not wait for a second look."""
    server.routes["master.m3u8"] = lambda n: MASTER
    server.routes["hi.m3u8"] = live(77, target=6)
    server.routes["lo.m3u8"] = live(77, target=6)
    began = time.monotonic()
    v = hls.probe_variants(server.base + "/master.m3u8")
    assert time.monotonic() - began < 3
    assert [x["bandwidth"] for x in v] == [900000, 300000]
    assert v[0]["size"] == int(900000 / 8 * 3.0)    # the 3 x 1 s window on offer
    assert len(server.fetches("hi.m3u8")) == 1
