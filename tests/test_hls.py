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

    # a live playlist that LOOKS like VOD (has segments) but lacks ENDLIST,
    # used by the sliding-window resume-refusal test
    live_with_segs = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n"
    for i in range(5):
        live_with_segs += f"#EXTINF:4.0,\nseg{i}.ts\n"
    # NOTE: no #EXT-X-ENDLIST -> sliding window

    # masters naming the sound: a playlist of its own (URI), or in the
    # picture's stream (no URI)
    def with_sound(uri):
        return ("#EXTM3U\n"
                '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES'
                + (f',URI="{uri}"' if uri else "") + "\n"
                '#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720,AUDIO="aud"\n'
                "high.m3u8\n").encode()

    # its own sound in the picture's stream (the default), and a dub beside it
    dubbed = ("#EXTM3U\n"
              '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES\n'
              '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="French",DEFAULT=NO,'
              'URI="fr.m3u8"\n'
              '#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720,AUDIO="aud"\n'
              "high.m3u8\n").encode()

    # its sound in two languages, a playlist each: English the default, Hindi
    # saying nothing - not the default to RFC 8216, as good a choice to yt-dlp
    # (2026-11-11). CODECS: without it yt-dlp joins no sound to the picture.
    def langs(english, hindi_first=False):
        en = ('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="%s",LANGUAGE="en",'
              'DEFAULT=YES,AUTOSELECT=YES,URI="en.m3u8"\n' % english)
        hi = ('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Hindi",LANGUAGE="hi",'
              'AUTOSELECT=YES,URI="hi.m3u8"\n')
        return ("#EXTM3U\n" + (hi + en if hindi_first else en + hi)
                + '#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720,'
                'CODECS="avc1.64001f,mp4a.40.2",AUDIO="aud"\n'
                "high.m3u8\n").encode()

    routes = {
        "langs.m3u8": (langs("English"), "application/vnd.apple.mpegurl"),
        # as text with no charset, the default second and named in Devanagari:
        # its UTF-8 holds the byte 0x85, a line break read as ISO-8859-1
        "langs-text.m3u8": (langs("अंग्रेज़ी", hindi_first=True), "text/plain"),
        "en.m3u8": (media("seg").encode(), "application/vnd.apple.mpegurl"),
        "hi.m3u8": (media("seg").encode(), "application/vnd.apple.mpegurl"),
        # playlists that moved: old/ answers with where they are now (new/),
        # whose links are relative to new/ (2026-11-12)
        "old/master.m3u8": ("/new/master.m3u8", "redirect"),
        "old/high.m3u8": ("/new/high.m3u8", "redirect"),
        "new/master.m3u8": (with_sound("audio.m3u8"), "application/vnd.apple.mpegurl"),
        "new/high.m3u8": (media("seg").encode(), "application/vnd.apple.mpegurl"),
        "sep.m3u8": (with_sound("audio.m3u8"), "application/vnd.apple.mpegurl"),
        "muxed.m3u8": (with_sound(""), "application/vnd.apple.mpegurl"),
        "dubbed.m3u8": (dubbed, "application/vnd.apple.mpegurl"),
        "master.m3u8": (master.encode(), "application/vnd.apple.mpegurl"),
        "high.m3u8": (media("seg").encode(), "application/vnd.apple.mpegurl"),
        "low.m3u8": (media("seg").encode(), "application/vnd.apple.mpegurl"),
        "aes.m3u8": (media("enc", True).encode(), "application/vnd.apple.mpegurl"),
        "live.m3u8": (b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n", "application/vnd.apple.mpegurl"),
        "sliding.m3u8": (live_with_segs.encode(), "application/vnd.apple.mpegurl"),
        "key.bin": (key, "application/octet-stream"),
        # an expired key link: a web page where the key was
        "badkey.m3u8": (media("enc", True).replace("key.bin", "expired").encode(),
                        "application/vnd.apple.mpegurl"),
        "expired": (b"<html><body>Session expired</body></html>", "text/html"),
        # the 4th segment is gone (404)
        "gap.m3u8": (("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n"
                      + "".join(f"#EXTINF:4.0,\n{n}\n" for n in
                                ("seg0.ts", "seg1.ts", "seg2.ts", "gone.ts", "seg4.ts"))
                      + "#EXT-X-ENDLIST\n").encode(), "application/vnd.apple.mpegurl"),
    }
    for i in range(5):
        routes[f"seg{i}.ts"] = (segs[i], "video/mp2t")
        routes[f"enc{i}.ts"] = (enc_segs[i], "video/mp2t")
        routes[f"new/seg{i}.ts"] = (segs[i], "video/mp2t")

    # one file holding the init section and every segment, a byte range of it
    # each (#EXT-X-BYTERANGE; no "@" = on from the last range). plain.mp4 is
    # the same file from a server that ignores Range.
    single = b"I" * 100 + plain_total

    def ranged(name):
        return ("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n"
                f'#EXT-X-MAP:URI="{name}",BYTERANGE="100@0"\n'
                f"#EXTINF:4.0,\n#EXT-X-BYTERANGE:4096@100\n{name}\n"
                + f"#EXTINF:4.0,\n#EXT-X-BYTERANGE:4096\n{name}\n" * 4
                + "#EXT-X-ENDLIST\n").encode()
    # capped.mp4 answers at most 1000 bytes a range (HTTP lets it), shifted.mp4
    # a part one byte on from the one asked for
    routes.update({"single.mp4": (single, "video/mp4"), "plain.mp4": (single, "video/mp4"),
                   "capped.mp4": (single, "video/mp4"), "shifted.mp4": (single, "video/mp4"),
                   "ranges.m3u8": (ranged("single.mp4"), "application/vnd.apple.mpegurl"),
                   "plainranges.m3u8": (ranged("plain.mp4"), "application/vnd.apple.mpegurl"),
                   "cappedranges.m3u8": (ranged("capped.mp4"), "application/vnd.apple.mpegurl"),
                   "shiftedranges.m3u8": (ranged("shifted.mp4"),
                                          "application/vnd.apple.mpegurl")})

    asked = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = self.path.split("?")[0].lstrip("/")
            asked.append(p)
            rng = self.headers.get("Range")
            if p in routes and routes[p][1] == "redirect":
                self.send_response(302)
                self.send_header("Location", routes[p][0])
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif p in routes and rng and p != "plain.mp4":
                body, ct = routes[p]
                a, _, b = rng[len("bytes="):].partition("-")
                a, b = int(a), min(int(b), len(body) - 1)
                if p == "capped.mp4":
                    b = min(b, a + 999)
                elif p == "shifted.mp4":
                    a += 1
                self.send_response(206)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(body)}")
                self.send_header("Content-Length", str(b - a + 1))
                self.end_headers()
                self.wfile.write(body[a:b + 1])
            elif p in routes:
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
    srv.single = single
    srv.asked = asked
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


def test_hls_resume_skips_already_fetched(hls_server, tmp_path):
    """Restart-from-disk path: 3 of 5 segments are 'already on disk' and
    HlsDownloader appends only segments 3+4 to reach the full file."""
    dst = str(tmp_path / "resume.ts")
    t = T.DownloadTask(f"{hls_server.base}/high.m3u8", dst, filename="resume.m3u8")
    # the grabber appends '.hfdownload' to save_path; we must seed THAT name,
    # but save_path is rewritten from .m3u8 -> .ts before the temp path is built
    temp = dst[:-3] + ".ts.hfdownload"
    seg_size = len(hls_server.plain_total) // 5     # equal-sized fake segments
    already = hls_server.plain_total[: seg_size * 3]
    with open(temp, "wb") as f:
        f.write(already)
    t.seg_total = 5
    t.seg_done = 3
    t.downloaded = len(already)
    Downloader(t).run()
    assert t.status == T.COMPLETED
    assert open(t.save_path, "rb").read() == hls_server.plain_total
    assert t.seg_done == 5


def test_hls_resume_refuses_live_playlist(hls_server, tmp_path):
    """A sliding-window playlist (no #EXT-X-ENDLIST) must NOT resume — appending
    new bytes onto an older window snapshot would silently corrupt the .ts."""
    dst = str(tmp_path / "live.ts")
    t = T.DownloadTask(f"{hls_server.base}/sliding.m3u8", dst, filename="live.m3u8")
    temp = dst[:-3] + ".ts.hfdownload"
    seg_size = len(hls_server.plain_total) // 5
    # pretend we resumed mid-stream
    with open(temp, "wb") as f:
        f.write(hls_server.plain_total[: seg_size * 3])
    t.seg_total = 5
    t.seg_done = 3
    t.downloaded = seg_size * 3
    Downloader(t).run()
    # full download (5 segments * one byte-value each), NOT 3+5=8
    assert t.status == T.COMPLETED
    assert t.seg_done == 5
    assert open(t.save_path, "rb").read() == hls_server.plain_total


def test_hls_resume_restarts_if_temp_corrupt(hls_server, tmp_path):
    """If the temp file size doesn't match the saved byte count, resume gives
    up and re-downloads from segment 0 — the .ts concat has no integrity check."""
    dst = str(tmp_path / "corrupt.ts")
    t = T.DownloadTask(f"{hls_server.base}/high.m3u8", dst, filename="corrupt.m3u8")
    temp = dst[:-3] + ".ts.hfdownload"
    with open(temp, "wb") as f:
        f.write(b"\x00" * 999)             # wrong size on purpose
    t.seg_total = 5
    t.seg_done = 3
    t.downloaded = 4096 * 3                 # mismatches the on-disk size
    Downloader(t).run()
    assert t.status == T.COMPLETED
    assert open(t.save_path, "rb").read() == hls_server.plain_total


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


def test_probe_variants_lists_master(hls_server):
    import hls
    v = hls.probe_variants(f"{hls_server.base}/master.m3u8")
    assert len(v) == 2
    assert v[0]["bandwidth"] == 2400000 and v[1]["bandwidth"] == 800000  # best first
    assert all(x.get("url") and x.get("label") for x in v)


def test_probe_variants_empty_for_media_playlist(hls_server):
    import hls
    # high.m3u8 is a media playlist (no #EXT-X-STREAM-INF) -> nothing to choose
    assert hls.probe_variants(f"{hls_server.base}/high.m3u8") == []


def test_a_failed_segment_is_named_by_its_place(hls_server, tmp_path):
    """The 4th of 5 failed and the message said "segment 7/5": the count
    of the batch's earlier segments was added twice."""
    t = T.DownloadTask(f"{hls_server.base}/gap.m3u8", str(tmp_path / "v.m3u8"))
    hls.HlsDownloader(t).run()
    assert t.status == T.ERROR and t.error.startswith("segment 4/5 failed"), t.error


def test_a_web_page_where_the_key_was_says_so(hls_server, tmp_path):
    """AES raised ValueError on it, which ended as "Download ended unexpectedly"."""
    t = T.DownloadTask(f"{hls_server.base}/badkey.m3u8", str(tmp_path / "v.m3u8"))
    hls.HlsDownloader(t).run()
    assert t.status == T.ERROR and "key link sent no key" in t.error, t.error


def test_a_playlist_of_byte_ranges_saves_each_part_once(hls_server, tmp_path):
    """Every line names one file, a byte range of it each (#EXT-X-BYTERANGE),
    and each was fetched whole: the video saved once a segment, "Completed"
    (2026-11-01)."""
    t = T.DownloadTask(f"{hls_server.base}/ranges.m3u8", str(tmp_path / "r.m3u8"))
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == hls_server.single


def test_a_part_answered_short_is_asked_for_again(hls_server, tmp_path):
    """HTTP lets a server send less of a range than asked. The short part was
    saved as it came, and the video read "Completed", broken (2026-11-04)."""
    t = T.DownloadTask(f"{hls_server.base}/cappedranges.m3u8", str(tmp_path / "c.m3u8"))
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == hls_server.single


def test_a_part_from_elsewhere_in_the_file_is_refused(hls_server, tmp_path):
    t = T.DownloadTask(f"{hls_server.base}/shiftedranges.m3u8", str(tmp_path / "s.m3u8"))
    Downloader(t).run()
    assert t.status == T.ERROR and "another part" in t.error, t.error


def test_a_server_that_ignores_the_ranges_is_refused(hls_server, tmp_path):
    """It would send the whole file for every part: an error, not that file."""
    t = T.DownloadTask(f"{hls_server.base}/plainranges.m3u8", str(tmp_path / "p.m3u8"))
    Downloader(t).run()
    assert t.status == T.ERROR and "whole file" in t.error, t.error


# ---- a stream whose sound is a playlist of its own ---------------------------------
@pytest.fixture
def to_ytdlp(monkeypatch):
    """yt-dlp, as what it was handed: the task's state when it started."""
    import yt_dl
    handed = []

    class Fake:
        def __init__(self, t, segments=None):
            self.t = t

        def run(self):
            handed.append({k: getattr(self.t, k) for k in
                           ("url", "use_ytdlp", "yt_format", "name_chosen", "seg_total")})
            self.t.status = T.COMPLETED

    monkeypatch.setattr(yt_dl, "YtDlpDownloader", Fake)
    return handed


def test_picked_from_a_master_whose_sound_is_apart_it_goes_to_ytdlp(hls_server, tmp_path,
                                                                    to_ytdlp):
    """The badge sends the quality's own playlist, which holds picture only
    when the master gives the sound one of its own: saved silent, and
    "Completed" (2026-11-03). yt-dlp fetches both and joins them."""
    t = T.DownloadTask(f"{hls_server.base}/high.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    t.hls_master = f"{hls_server.base}/sep.m3u8"
    Downloader(t).run()
    assert to_ytdlp == [{"url": f"{hls_server.base}/sep.m3u8", "use_ytdlp": True,
                         "yt_format": 'bv*[height<=720]+ba[format_note~="^English(?:, (?:low|high))?$"]'
                                      "/bv*[height<=720]+ba/b[height<=720]",
                         "name_chosen": "Clip", "seg_total": 0}]
    assert not any(p.startswith("seg") for p in hls_server.asked), "the picture was fetched alone"


@pytest.mark.parametrize("master", ["langs.m3u8", "langs-text.m3u8"])
def test_the_sound_a_player_plays_is_the_one_ytdlp_takes(hls_server, tmp_path, monkeypatch,
                                                         master):
    """English is the default, Hindi says nothing: yt-dlp's "best audio" took
    a rendition with no DEFAULT for one as good as the default, broke the tie
    by name, and the video came with the Hindi sound (2026-11-11). The real
    yt-dlp's choice, from the options the download uses; nothing is fetched.
    langs-text.m3u8 comes as text with no charset, the default named in
    Devanagari: read as ISO-8859-1, its tag was cut in two."""
    yt_dlp = pytest.importorskip("yt_dlp")
    import yt_dl
    monkeypatch.setattr(yt_dl, "_ffmpeg_dir", lambda: str(tmp_path))     # a join, as with ffmpeg
    taken = []

    class Choice(yt_dl.YtDlpDownloader):
        def run(self):
            opts, _ = yt_dl._choosing_opts(self.t, str(tmp_path))
            with yt_dlp.YoutubeDL({**opts, "cachedir": False}) as ydl:
                info = ydl.extract_info(self.t.url, download=False)
            taken.extend(f["url"] for f in info.get("requested_formats") or ()
                         if f.get("vcodec") == "none")
            self.t.status = T.COMPLETED

    monkeypatch.setattr(yt_dl, "YtDlpDownloader", Choice)
    t = T.DownloadTask(f"{hls_server.base}/high.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    t.hls_master = f"{hls_server.base}/{master}"
    Downloader(t).run()
    assert taken == [f"{hls_server.base}/en.m3u8"]


@pytest.mark.parametrize("name", ['Eng "dub"', "back\\slash", "a]b,c", "अंग्रेज़ी",
                                  "Stereo (height<=144 devices)", "has\0nul"])
def test_any_name_a_master_gives_is_asked_for_as_it_is(name, tmp_path, monkeypatch, to_ytdlp):
    """What a master names its sound goes into yt-dlp's format string: quoted,
    it must be read back as that name - and a "height<=144" in it is no cap."""
    yt_dlp = pytest.importorskip("yt_dlp")
    import hls
    import yt_dl
    monkeypatch.setattr(yt_dl, "_ffmpeg_dir", lambda: str(tmp_path))
    t = T.DownloadTask("https://e.test/high.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    hls.HlsDownloader(t)._to_ytdlp("https://e.test/m.m3u8", 0, {"NAME": name, "LANGUAGE": "en"})
    fmt = yt_dl._choosing_opts(t, str(tmp_path))[0]["format"]
    assert "height" not in fmt.split('"', 1)[0], fmt
    formats = [{"format_id": "v", "url": "v", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
                "height": 720},
               {"format_id": "a1", "url": "a1", "ext": "mp4", "vcodec": "none", "acodec": "mp4a",
                "format_note": "Other", "language": "hi"},
               {"format_id": "a2", "url": "a2", "ext": "mp4", "vcodec": "none", "acodec": "mp4a",
                "format_note": name + ", high", "language": "en"}]
    with yt_dlp.YoutubeDL({"quiet": True, "cachedir": False}) as ydl:
        chosen = list(ydl.build_format_selector(fmt)(
            {"formats": formats, "has_merged_format": False, "incomplete_formats": False}))
    assert [f["format_id"] for f in chosen[0]["requested_formats"]] == ["v", "a2"], fmt


def test_quality_strings_from_before_read_as_they_did(tmp_path, monkeypatch):
    import yt_dl
    t = T.DownloadTask("https://e.test/v", str(tmp_path / "v.mp4"))
    for ffmpeg, given, wanted in (
            (True, "", "bv*+ba/b"),
            (True, "b[height<=720]", "bv*[height<=720]+ba/b[height<=720]/b"),
            (True, "bv*[height<=720]+ba/b[height<=720]", "bv*[height<=720]+ba/b[height<=720]/b"),
            (True, "ba[ext=m4a]/bestaudio/best", "ba[ext=m4a]/ba/b"),
            (False, 'bv*[height<=720]+ba[language="en"]/bv*[height<=720]+ba/b[height<=720]',
             "b[height<=720]/b")):
        monkeypatch.setattr(yt_dl, "_ffmpeg_dir", lambda: str(tmp_path) if ffmpeg else None)
        t.yt_format = given
        assert yt_dl._choosing_opts(t, str(tmp_path))[0]["format"] == wanted, given


def test_a_master_that_moved_still_hands_its_stream_over(hls_server, tmp_path, to_ytdlp):
    """The browser read the master where it was sent (new/) and picked
    new/high.m3u8; the engine joined the master's links against the address
    it asked (old/), found no such quality, and saved the picture alone,
    "Completed" (2026-11-12). It is handed over by the address it was given."""
    t = T.DownloadTask(f"{hls_server.base}/new/high.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    t.hls_master = f"{hls_server.base}/old/master.m3u8"
    Downloader(t).run()
    assert [h["url"] for h in to_ytdlp] == [f"{hls_server.base}/old/master.m3u8"]


def test_a_playlist_that_moved_is_read_where_it_went(hls_server, tmp_path, to_ytdlp):
    """Its segments are named relative to where it was sent, as a player
    reads them: joined against the address asked, every one was a 404."""
    t = T.DownloadTask(f"{hls_server.base}/old/high.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    Downloader(t).run()
    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == hls_server.plain_total
    assert "new/seg0.ts" in hls_server.asked


def test_a_master_given_as_it_is_goes_there_too(hls_server, tmp_path, to_ytdlp):
    t = T.DownloadTask(f"{hls_server.base}/sep.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    Downloader(t).run()
    assert [h["url"] for h in to_ytdlp] == [f"{hls_server.base}/sep.m3u8"]


def test_sound_in_the_picture_s_own_stream_stays_here(hls_server, tmp_path, to_ytdlp):
    t = T.DownloadTask(f"{hls_server.base}/high.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    t.hls_master = f"{hls_server.base}/muxed.m3u8"
    Downloader(t).run()
    assert to_ytdlp == [] and t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == hls_server.plain_total


def test_a_dub_beside_the_picture_s_own_sound_is_not_missed_sound(hls_server, tmp_path,
                                                                  to_ytdlp):
    """The default sound is in the picture's stream; a French dub has a
    playlist of its own. Handed to yt-dlp, its "best audio" could be the dub."""
    t = T.DownloadTask(f"{hls_server.base}/high.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    t.hls_master = f"{hls_server.base}/dubbed.m3u8"
    Downloader(t).run()
    assert to_ytdlp == [] and t.status == T.COMPLETED, t.error


def test_a_master_the_stream_is_not_in_changes_nothing(hls_server, tmp_path, to_ytdlp):
    t = T.DownloadTask(f"{hls_server.base}/low.m3u8", str(tmp_path / "Clip.m3u8"),
                       filename="Clip.m3u8")
    t.hls_master = f"{hls_server.base}/sep.m3u8"           # names high.m3u8 only
    Downloader(t).run()
    assert to_ytdlp == [] and t.status == T.COMPLETED, t.error


def test_the_master_is_kept_with_the_download():
    t = T.DownloadTask("https://e.test/high.m3u8", "C:/dl/v.m3u8")
    t.hls_master = "https://e.test/master.m3u8"
    assert T.DownloadTask.from_dict(t.to_dict()).hls_master == "https://e.test/master.m3u8"
