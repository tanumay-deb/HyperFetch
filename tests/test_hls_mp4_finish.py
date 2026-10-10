"""An HLS download is finished as an ordinary MP4, so a player can seek in it.

The HLS engine joined the segments and saved that: a transport stream, or - for
a playlist of fMP4 fragments - a fragmented MP4 with no index. Both play. But
in VLC a skip of five seconds froze the picture for a second or two, every
time, while an ordinary MP4 carried on at once (reported 2026-10-10 with two
files side by side).

Measured in VLC 3.0.24 on its real Direct3D output, frames shown in the first,
second and third second after each +5 s skip:
  ordinary MP4 (H.264)                 45 / 30 / 30
  joined fMP4 fragments (AV1)          25 /  0 /  0-27
  the same video rewritten by ffmpeg   46 / 30 / 30    (1 s for 110 MB, no re-encode)
  joined transport stream (H.264)      18 /  0 /  0-19
So it is the container, not the codec: VLC can only jump to a fragment's start
in the one, and has no index at all in the other. With ffmpeg at hand the
engine now rewrites what it joined as an ordinary MP4 - the same video and
audio, copied - and saves that; without it, or if the rewrite fails or comes
out short, the joined file is saved as before.

The first tests drive the engine with a stand-in for the rewrite; the last ones
use the real ffmpeg on media it makes itself, and are skipped where there is
none. No network.
"""
import http.server
import os
import shutil
import struct
import subprocess
import threading

import pytest

import hls
import task as T
import utils
from downloader import Downloader

TS = [bytes([0x47, i]) * 940 for i in range(1, 6)]            # five "segments"
INIT = b"\x00\x00\x00\x18ftypiso6" + b"\x00" * 8 + b"\x00\x00\x00\x08moov"
M4S = [b"\x00\x00\x00\x10moof" + bytes([i]) * 2048 for i in range(1, 6)]


def _serve(routes):
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0].lstrip("/")
            hits.append(path)
            body = routes.get(path)
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
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1], hits


@pytest.fixture
def stream(request):
    """A five-segment playlist, four seconds a segment: "ts" or "fmp4"."""
    kind = getattr(request, "param", "ts")
    ext, segs = ("ts", TS) if kind == "ts" else ("m4s", M4S)
    media = ("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n#EXT-X-PLAYLIST-TYPE:VOD\n"
             + ('#EXT-X-MAP:URI="init.mp4"\n' if kind == "fmp4" else "")
             + "".join("#EXTINF:4.0,\nseg%d.%s\n" % (i, ext) for i in range(5))
             + "#EXT-X-ENDLIST\n")
    routes = {"clip.m3u8": media.encode(), "init.mp4": INIT}
    routes.update({"seg%d.%s" % (i, ext): s for i, s in enumerate(segs)})
    httpd, base, hits = _serve(routes)
    joined = (INIT if kind == "fmp4" else b"") + b"".join(segs)
    yield type("Stream", (), {"url": base + "/clip.m3u8", "joined": joined, "hits": hits,
                              "kind": kind})
    httpd.shutdown()


class _Rewrite:
    """Stands in for hls.remux: an "MP4" that is the joined bytes with a mark."""

    def __init__(self, monkeypatch, result=True, during=None):
        self.calls, self.result, self.during = [], result, during
        monkeypatch.setattr(hls, "ffmpeg_path", lambda: "ffmpeg")
        monkeypatch.setattr(hls, "remux", self)

    def __call__(self, src, dst, seconds=0.0, should_stop=lambda: False):
        self.calls.append({"src": src, "dst": dst, "seconds": seconds,
                           "stop_asked": should_stop()})
        if self.during:
            self.during()
        if not self.result or should_stop():
            return False
        with open(src, "rb") as f, open(dst, "wb") as out:
            out.write(b"MP4:" + f.read())
        return True


@pytest.fixture
def dl(tmp_path):
    """The folder the download is saved in, and nothing else is."""
    d = tmp_path / "dl"
    d.mkdir()
    return d


def _task(stream, dl, name="clip.m3u8"):
    return T.DownloadTask(stream.url, str(dl / name), filename=name)


# ---- the case ---------------------------------------------------------------------
@pytest.mark.parametrize("stream", ["ts", "fmp4"], indirect=True)
def test_what_was_joined_is_saved_as_an_ordinary_mp4(stream, dl, monkeypatch):
    rewrite = _Rewrite(monkeypatch)
    t = _task(stream, dl)

    Downloader(t).run()

    assert t.status == T.COMPLETED, t.error
    assert os.path.basename(t.save_path) == t.filename == "clip.mp4"
    assert open(t.save_path, "rb").read() == b"MP4:" + stream.joined
    assert os.listdir(dl) == ["clip.mp4"], "something else was left in the folder"
    assert not os.path.exists(utils.temp_download_path(t.id)), "the joined temp file was left"
    assert t.total_size == t.downloaded == os.path.getsize(t.save_path)
    assert len(rewrite.calls) == 1
    assert rewrite.calls[0]["seconds"] == pytest.approx(20.0), "the playlist's length was not passed"


def test_a_name_the_browser_gave_is_kept(stream, dl, monkeypatch):
    _Rewrite(monkeypatch)
    t = _task(stream, dl, name="My clip.mp4")
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == "My clip.mp4"
    assert open(t.save_path, "rb").read() == b"MP4:" + stream.joined


def test_another_file_of_the_new_name_is_not_written_over(stream, dl, monkeypatch):
    """clip.ts becomes clip.mp4 - unless somebody's clip.mp4 is there."""
    _Rewrite(monkeypatch)
    (dl / "clip.mp4").write_bytes(b"somebody's")
    t = _task(stream, dl)
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == "clip (1).mp4"
    assert (dl / "clip.mp4").read_bytes() == b"somebody's"


# ---- when it cannot be done ----------------------------------------------------------
def test_without_ffmpeg_the_joined_file_is_saved_as_before(stream, dl, monkeypatch):
    monkeypatch.setattr(hls, "ffmpeg_path", lambda: None)
    monkeypatch.setattr(hls, "remux", lambda *a, **k: pytest.fail("asked for a rewrite"))
    t = _task(stream, dl)
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == "clip.ts"
    assert open(t.save_path, "rb").read() == stream.joined


def test_a_rewrite_that_fails_keeps_the_joined_file(stream, dl, monkeypatch):
    _Rewrite(monkeypatch, result=False)
    t = _task(stream, dl)
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert t.filename == "clip.ts"
    assert open(t.save_path, "rb").read() == stream.joined
    assert os.listdir(dl) == ["clip.ts"]
    assert any("MP4" in e["message"] for e in t.events), "the log does not say why it is a .ts"


def test_a_pause_during_the_rewrite_keeps_what_was_fetched(stream, dl, monkeypatch):
    t = _task(stream, dl)
    rewrite = _Rewrite(monkeypatch, during=t.request_pause)

    Downloader(t).run()

    assert t.status == T.PAUSED
    assert os.listdir(dl) == [], "half a file was left at its place"
    assert open(utils.temp_download_path(t.id), "rb").read() == stream.joined
    fetched = len(stream.hits)

    t.clear_pause()
    rewrite.during = None
    Downloader(t).run()

    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == b"MP4:" + stream.joined
    assert [h for h in stream.hits[fetched:] if h.startswith("seg")] == [], "fetched again"


def test_a_cancel_during_the_rewrite_leaves_nothing(stream, dl, monkeypatch):
    t = _task(stream, dl)
    _Rewrite(monkeypatch, during=t.request_cancel)
    Downloader(t).run()
    assert t.status == T.CANCELLED
    assert os.listdir(dl) == []
    assert not os.path.exists(utils.temp_download_path(t.id))


def test_the_card_says_what_it_is_doing_meanwhile(stream, dl, monkeypatch):
    """A big file takes a while to rewrite, at 100% with no speed."""
    t = _task(stream, dl)
    seen = []
    _Rewrite(monkeypatch, during=lambda: seen.append(getattr(t, "finishing", False)))
    Downloader(t).run()
    assert seen == [True] and t.finishing is False


def test_the_card_reads_saving_as_mp4_then():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    t = T.DownloadTask("https://example.test/clip.m3u8", "C:/dl/clip.mp4", filename="clip.mp4",
                       status=T.DOWNLOADING)
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    assert "Saving as MP4" not in card.sub.toolTip()
    t.finishing = True
    card.update_task(t, 0.0)
    assert card.sub.toolTip() == "Saving as MP4…"


def test_no_test_uses_the_machines_ffmpeg_unasked():
    """Else the suite would differ from one machine to the next."""
    assert hls.ffmpeg_path() is None


# ---- the real ffmpeg ----------------------------------------------------------------------
REAL = shutil.which("ffmpeg")


def _boxes(path):
    """Top-level MP4 boxes, and the names inside moov's sample tables."""
    top, size = [], os.path.getsize(path)
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    while pos + 8 <= size:
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        if n == 1:
            n = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        if n < 8:
            break
        top.append(typ.decode("latin-1"))
        pos += n
    return top, data


def _make(tmp_path, *args):
    """Six seconds of test video and tone, made by ffmpeg itself. MPEG-2
    video: it repeats its headers in the stream at every group of pictures,
    as the H.264 of a real HLS stream does. MPEG-4 part 2 keeps them outside
    the stream, and a transport stream joined from its segments cannot be
    read back - by ffmpeg, which then leaves the joined file as it is."""
    r = subprocess.run([REAL, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=duration=6:size=160x120:rate=10", "-f", "lavfi", "-i",
                        "sine=duration=6", "-c:v", "mpeg2video", "-g", "10", "-c:a", "aac",
                        *args],
                       cwd=str(tmp_path), capture_output=True, text=True)
    if r.returncode:
        pytest.skip("this ffmpeg cannot make the test media: " + r.stderr[-200:])


def _is_ordinary_mp4(path):
    top, data = _boxes(path)
    return (top.count("moov") == 1 and "mdat" in top and "moof" not in top
            and b"stco" in data and b"stss" in data)


@pytest.mark.skipif(not REAL, reason="no ffmpeg on this machine")
@pytest.mark.parametrize("kind", ["ts", "fmp4"])
def test_with_the_real_ffmpeg_a_joined_file_becomes_an_ordinary_mp4(kind, tmp_path,
                                                                 monkeypatch):
    monkeypatch.setattr(hls, "ffmpeg_path", lambda: REAL)
    if kind == "ts":
        _make(tmp_path, "-f", "mpegts", "joined.bin")
    else:
        _make(tmp_path, "-f", "mp4", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
              "joined.bin")
        assert "moof" in _boxes(str(tmp_path / "joined.bin"))[0], "not the layout under test"
    out = str(tmp_path / "out.hfmove")

    assert hls.remux(str(tmp_path / "joined.bin"), out, seconds=6.0) is True

    assert _is_ordinary_mp4(out)


@pytest.mark.skipif(not REAL, reason="no ffmpeg on this machine")
def test_with_the_real_ffmpeg_a_rewrite_that_did_not_work_leaves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(hls, "ffmpeg_path", lambda: REAL)
    out = str(tmp_path / "out.hfmove")
    (tmp_path / "junk.bin").write_bytes(b"not a video at all" * 500)
    assert hls.remux(str(tmp_path / "junk.bin"), out, seconds=6.0) is False
    assert not os.path.exists(out)

    _make(tmp_path, "-f", "mpegts", "joined.bin")
    # the playlist said ten minutes and six seconds came out: cut short
    assert hls.remux(str(tmp_path / "joined.bin"), out, seconds=600.0) is False
    assert not os.path.exists(out)
    # asked to stop: a pause or a cancel
    assert hls.remux(str(tmp_path / "joined.bin"), out, seconds=6.0,
                     should_stop=lambda: True) is False
    assert not os.path.exists(out)


@pytest.mark.skipif(not REAL, reason="no ffmpeg on this machine")
@pytest.mark.parametrize("kind", ["ts", "fmp4"])
def test_with_the_real_ffmpeg_a_real_stream_comes_out_as_an_ordinary_mp4(kind, tmp_path,
                                                                      monkeypatch):
    """End to end: a playlist ffmpeg made, served here, fetched by the engine."""
    site = tmp_path / "site"
    site.mkdir()
    if kind == "ts":
        _make(site, "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
              "-hls_segment_filename", "seg%d.ts", "clip.m3u8")
    else:
        _make(site, "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
              "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
              "-hls_segment_filename", "seg%d.m4s", "clip.m3u8")
    routes = {n: (site / n).read_bytes() for n in os.listdir(site)}
    httpd, base, _ = _serve(routes)
    monkeypatch.setattr(hls, "ffmpeg_path", lambda: REAL)
    out = tmp_path / "dl"
    out.mkdir()
    t = T.DownloadTask(base + "/clip.m3u8", str(out / "clip.m3u8"), filename="clip.m3u8")
    try:
        Downloader(t).run()
    finally:
        httpd.shutdown()

    assert t.status == T.COMPLETED, t.error
    assert t.filename == "clip.mp4" and os.listdir(out) == ["clip.mp4"]
    assert _is_ordinary_mp4(t.save_path)
    probe = shutil.which("ffprobe")
    if probe:
        secs = float(subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", t.save_path], capture_output=True,
                                    text=True).stdout.strip() or 0)
        assert 5.5 <= secs <= 6.6, secs
