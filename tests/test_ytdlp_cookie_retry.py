"""yt-dlp retries without cookies when the site serves no usable format.

Real failure this reproduces: the extension's right-click "Download video on
this page" sends the browser's YouTube cookies. YouTube answers a cookie-bearing
request with a player response whose formats need the "n challenge" solved;
with no JS runtime yt-dlp drops every video format ("Only images are available")
and dies with "Requested format is not available". The identical URL pasted into
New Download — which sends no cookies — downloaded fine.
"""
import sys
import types

import pytest

import task as T
import yt_dl


class _FakeYDL:
    """Stands in for yt_dlp.YoutubeDL. Records the headers of every attempt."""

    def __init__(self, opts, attempts, fail_when):
        self.opts = opts
        self._attempts = attempts
        self._fail_when = fail_when

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        hdrs = self.opts.get("http_headers") or {}
        self._attempts.append(hdrs)
        if self._fail_when(hdrs):
            raise RuntimeError(
                "ERROR: [youtube] abc: Requested format is not available. "
                "Use --list-formats for a list of available formats")
        return {"title": "video", "ext": "mkv"}

    def prepare_filename(self, info):
        return ""


@pytest.fixture
def fake_ytdlp(monkeypatch):
    """Install a fake yt_dlp module; return the list attempts are recorded in."""
    attempts = []
    state = {"fail_when": lambda h: False}

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts: _FakeYDL(opts, attempts, state["fail_when"])
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return attempts, state


def _task(tmp_path, headers):
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc",
                       str(tmp_path / "v.mkv"), filename="v.mkv")
    t.headers = headers
    return t


def _has_cookie(h):
    return any(k.lower() == "cookie" for k in h)


def test_retries_without_cookies_when_no_format_is_available(tmp_path, fake_ytdlp):
    attempts, state = fake_ytdlp
    state["fail_when"] = _has_cookie          # cookies -> no usable format

    t = _task(tmp_path, {"Cookie": "SID=x", "User-Agent": "UA", "Referer": "https://y/"})
    yt_dl.YtDlpDownloader(t).run()

    assert len(attempts) == 2, "should have retried once without cookies"
    assert _has_cookie(attempts[0]), "the first try must still use cookies"
    assert not _has_cookie(attempts[1]), "the retry must drop the cookie header"
    # the retry keeps the rest of the browser context — referer-gated CDNs need it
    assert attempts[1].get("User-Agent") == "UA"
    assert attempts[1].get("Referer") == "https://y/"
    assert t.status != T.ERROR, f"retry succeeded but task errored: {t.error}"


def test_no_retry_when_there_were_no_cookies(tmp_path, fake_ytdlp):
    attempts, state = fake_ytdlp
    state["fail_when"] = lambda h: True       # fails regardless

    t = _task(tmp_path, {"User-Agent": "UA"})
    yt_dl.YtDlpDownloader(t).run()

    assert len(attempts) == 1, "nothing to drop, so retrying is just a second failure"
    assert t.status == T.ERROR


def test_cookies_are_kept_when_they_work(tmp_path, fake_ytdlp):
    attempts, state = fake_ytdlp
    state["fail_when"] = lambda h: False      # cookies are fine here

    t = _task(tmp_path, {"Cookie": "SID=x"})
    yt_dl.YtDlpDownloader(t).run()

    assert len(attempts) == 1, "a working first try must not be retried"
    assert _has_cookie(attempts[0]), "private videos need the cookies to survive"


def test_unrelated_failure_is_not_retried(tmp_path, fake_ytdlp):
    attempts, state = fake_ytdlp

    def boom(h):
        raise RuntimeError("ERROR: unable to download video data: HTTP Error 403")

    # raise from extract_info via fail_when's side effect
    state["fail_when"] = boom

    t = _task(tmp_path, {"Cookie": "SID=x"})
    yt_dl.YtDlpDownloader(t).run()

    assert len(attempts) == 1, "only a missing-format failure is worth retrying"
    assert t.status == T.ERROR


@pytest.mark.parametrize("msg,exp", [
    ("Requested format is not available", True),
    ("ERROR: Only images are available for download", True),
    ("no video formats found", True),
    ("HTTP Error 403: Forbidden", False),
    ("Unable to extract player response", False),
])
def test_formats_unavailable_predicate(msg, exp):
    assert yt_dl._formats_unavailable(RuntimeError(msg)) is exp


def test_format_error_does_not_blame_ffmpeg_when_ffmpeg_exists(tmp_path, fake_ytdlp,
                                                               monkeypatch):
    """The old message sent users hunting for a binary that was already there."""
    import shutil
    attempts, state = fake_ytdlp
    state["fail_when"] = lambda h: True
    # yt_dl imports shutil inside run(); patch the module it will look up. This
    # also pins the result on CI, where the bundled bin/ffmpeg.exe is absent.
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/ffmpeg")

    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()

    assert t.status == T.ERROR
    assert "ffmpeg" not in t.error.lower(), t.error
