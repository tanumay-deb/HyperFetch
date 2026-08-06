"""DASH (.mpd) manifests route to yt-dlp, not the byte downloader.

An .mpd is an XML index, not the media. Byte-downloading one writes a few KB of
XML named like a video; the HLS engine can't read it either. yt-dlp's generic
extractor is the engine that understands it.
"""
import pytest

import task as T
import yt_dl
from downloader import Downloader


@pytest.mark.parametrize("url,fn,ct,exp", [
    ("http://x/manifest.mpd", "", "", True),
    ("http://x/manifest.mpd?token=1", "", "", True),      # query string ignored
    ("", "stream.mpd", "", True),                         # named by the sniffer
    ("", "", "application/dash+xml", True),
    ("http://x/v.m3u8", "", "application/x-mpegurl", False),
    ("http://x/v.mp4", "v.mp4", "video/mp4", False),
    ("http://x/report.mpdf", "", "", False),              # not a suffix match
    ("", "", "", False),
])
def test_is_dash(url, fn, ct, exp):
    assert yt_dl.is_dash(url, fn, ct) is exp


def _route(tmp_path, monkeypatch, url, filename):
    """Run the engine-delegation step and report which engine got the task."""
    picked = []

    def engine(name):
        def run(self):
            picked.append(name)
            self.t.status = T.COMPLETED
        return run

    monkeypatch.setattr(yt_dl.YtDlpDownloader, "run", engine("ytdlp"))
    import hls
    import torrent
    monkeypatch.setattr(hls.HlsDownloader, "run", engine("hls"))
    monkeypatch.setattr(torrent.TorrentDownloader, "run", engine("torrent"))
    # anything that reaches the byte path must not touch the network
    monkeypatch.setattr(Downloader, "_probe",
                        lambda self: picked.append("bytes"))

    t = T.DownloadTask(url, str(tmp_path / filename), filename=filename)
    Downloader(t)._run()
    return picked


def test_mpd_goes_to_ytdlp(tmp_path, monkeypatch):
    # the bug: this used to fall through every engine and byte-download the XML
    assert _route(tmp_path, monkeypatch,
                  "http://x/manifest.mpd", "manifest.mpd") == ["ytdlp"]


def test_mpd_with_query_goes_to_ytdlp(tmp_path, monkeypatch):
    assert _route(tmp_path, monkeypatch,
                  "http://x/manifest.mpd?auth=abc", "manifest.mpd") == ["ytdlp"]


def test_m3u8_still_goes_to_hls(tmp_path, monkeypatch):
    assert _route(tmp_path, monkeypatch,
                  "http://x/v.m3u8", "v.m3u8") == ["hls"]


def test_plain_file_still_byte_downloads(tmp_path, monkeypatch):
    assert _route(tmp_path, monkeypatch,
                  "http://x/v.mp4", "v.mp4") == ["bytes"]
