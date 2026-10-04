"""/probe says how long a playlist is and whether it is a live stream.

The extension's badge on a blob/MSE player has to tell the player's own stream
from the others the page loads. Seen 2026-10-03: an ad network's live stream,
which started after the film, was sent in the film's place - twice. The page
knows how long its player's video is; what it lacked was how long each sniffed
stream is, and whether it has an end at all. The app reads playlists the
browser cannot (it has the capture's cookies and Referer and no CORS), so its
/probe now says both, from the same fetches it already made for the quality
picker.

"Live" here is what ONE fetch shows: no end tag, no #EXT-X-PLAYLIST-TYPE, and
a first segment numbered above 0, so segments have already dropped off the
front. The download engine starts from the same signs and looks a second time
before it refuses a stream (test_hls_live.py); /probe only helps a page choose,
and must stay one fetch per playlist.
"""
import http.server
import threading
from collections import deque

import pytest

import hls
from api_server import create_app


def media(first=0, count=5, seconds=4.0, endlist=True, ptype=None, seq_tag=True):
    out = ["#EXTM3U", "#EXT-X-TARGETDURATION:%d" % round(seconds)]
    if ptype:
        out.append("#EXT-X-PLAYLIST-TYPE:" + ptype)
    if seq_tag:
        out.append("#EXT-X-MEDIA-SEQUENCE:%d" % first)
    for i in range(first, first + count):
        out += ["#EXTINF:%.3f," % seconds, "seg%d.ts" % i]
    if endlist:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"


MASTER = ("#EXTM3U\n"
          "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480\nlow.m3u8\n"
          "#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720\nhigh.m3u8\n")


@pytest.fixture
def playlists():
    """A server of playlists; .hits lists the paths asked for, in order."""
    routes = {
        "film.m3u8": media(count=5, seconds=4.0),                       # 20 s, finished
        "ad.m3u8": media(first=9131, count=3, seconds=2.0, endlist=False),
        "sloppy.m3u8": media(count=5, endlist=False),                   # VOD, no end tag
        "untagged.m3u8": media(count=5, endlist=False, seq_tag=False),
        "event.m3u8": media(first=40, count=6, endlist=False, ptype="EVENT"),
        "master.m3u8": MASTER,
        "high.m3u8": media(count=6, seconds=5.0),                       # 30 s
        "low.m3u8": media(count=6, seconds=5.0),
        "livemaster.m3u8": MASTER.replace("high.m3u8", "ad.m3u8"),
    }
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = self.path.split("?")[0].lstrip("/")
            hits.append(p)
            if p not in routes:
                self.send_response(404)
                self.end_headers()
                return
            body = routes[p].encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.url = lambda p: "http://127.0.0.1:%d/%s" % (httpd.server_address[1], p)
    httpd.hits = hits
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_a_finished_playlist_has_a_length_and_is_not_live(playlists):
    got = hls.probe(playlists.url("film.m3u8"))
    assert got == {"variants": [], "duration": 20.0, "live": False}
    assert playlists.hits == ["film.m3u8"], "one fetch"


def test_a_sliding_window_is_live(playlists):
    """The ad network's stream: three segments on offer, 9131 already gone."""
    got = hls.probe(playlists.url("ad.m3u8"))
    assert got["live"] is True
    assert got["duration"] == 6.0
    assert got["variants"] == []
    assert playlists.hits == ["ad.m3u8"], "/probe looks once; the engine is what looks twice"


@pytest.mark.parametrize("name", ["sloppy.m3u8", "untagged.m3u8", "event.m3u8"])
def test_what_keeps_everything_from_its_start_is_not_live(playlists, name):
    """No end tag, but numbered from 0 (or not numbered), or marked EVENT:
    nothing has dropped off the front."""
    assert hls.probe(playlists.url(name))["live"] is False


def test_a_master_has_the_length_of_its_best_rendition(playlists):
    got = hls.probe(playlists.url("master.m3u8"))
    assert [v["height"] for v in got["variants"]] == [720, 480]
    assert got["duration"] == 30.0
    assert got["live"] is False
    assert got["variants"][0]["size"] == int(2400000 / 8 * 30.0)
    assert playlists.hits == ["master.m3u8", "high.m3u8"], "no fetch more than before"


def test_a_master_of_a_live_stream_is_live(playlists):
    got = hls.probe(playlists.url("livemaster.m3u8"))
    assert got["live"] is True
    assert len(got["variants"]) == 2


def test_a_playlist_that_cannot_be_read_says_nothing(playlists):
    assert hls.probe(playlists.url("missing.m3u8")) == {"variants": [], "duration": 0.0,
                                                        "live": False}


def test_the_variant_list_alone_is_what_it_was(playlists):
    """probe_variants is the list the quality picker has always been given."""
    assert hls.probe_variants(playlists.url("film.m3u8")) == []
    assert [v["label"] for v in hls.probe_variants(playlists.url("master.m3u8"))] == ["720p", "480p"]


# ---- the endpoint -------------------------------------------------------------
class _Queue:
    tasks = []
    queues = {"Main": None}


def test_the_endpoint_passes_length_and_live_on(playlists, tmp_path):
    c = create_app(_Queue(), str(tmp_path), pending=deque()).test_client()
    r = c.post("/probe", json={"url": playlists.url("ad.m3u8")})
    assert r.status_code == 200
    assert r.get_json() == {"variants": [], "duration": 6.0, "live": True}

    r = c.post("/probe", json={"url": playlists.url("master.m3u8")})
    body = r.get_json()
    assert body["duration"] == 30.0 and body["live"] is False
    assert [v["label"] for v in body["variants"]] == ["720p", "480p"]
