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
    """Stands in for yt_dlp.YoutubeDL. Records the headers of every attempt.

    It edits the options it is given the way the real one does: yt-dlp keeps
    that very dict as its params and moves a Cookie header out of it into its
    cookie jar (YoutubeDL.__init__, 2026.08.19). A fake that left its options
    alone hid that the fallback read the cookie back from there - and never
    retried."""

    def __init__(self, opts, attempts, fail_when):
        self.opts = opts
        self._attempts = attempts
        self._fail_when = fail_when
        self._hdrs = dict(opts.get("http_headers") or {})
        opts["http_headers"] = {k: v for k, v in self._hdrs.items()
                                if k.lower() != "cookie"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        hdrs = self._hdrs
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


def test_the_retry_happens_with_the_real_yt_dlp(tmp_path):
    """The real constructor, not a fake: it takes the Cookie header out of the
    options it is handed. The fallback found no cookie there after the first
    try and raised instead of retrying, so a YouTube video sent from the
    browser ended on "no downloadable video stream" (user's log, 2026-11-12:
    no "retrying without them" line)."""
    import yt_dlp
    had_cookie = []

    def attempt(o):
        had_cookie.append(_has_cookie(o.get("http_headers") or {}))
        with yt_dlp.YoutubeDL(o):
            pass
        if len(had_cookie) == 1:
            raise yt_dlp.utils.DownloadError(
                "ERROR: [youtube] abc: Requested format is not available. "
                "Use --list-formats for a list of available formats")
        return "video.mkv"

    opts = {"http_headers": {"Cookie": "SID=x", "User-Agent": "UA"},
            "logger": yt_dl._YtLog(), "quiet": True, "no_warnings": True}
    assert yt_dl._cookies_last(attempt, opts) == "video.mkv"
    assert had_cookie == [True, False]


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


# ---- a YouTube 403 points at the stale bundle, not at the network ----
class _403YDL(_FakeYDL):
    """How YouTube fails when the bundled yt-dlp has aged out: the signing of
    the media URLs changed and the download 403s."""

    def extract_info(self, url, download=True):
        raise RuntimeError("ERROR: unable to download video data: "
                           "HTTP Error 403: Forbidden")


@pytest.fixture
def stale_ytdlp(monkeypatch):
    attempts = []
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts: _403YDL(opts, attempts, lambda h: False)
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return attempts


def test_a_youtube_403_blames_the_bundled_version(tmp_path, stale_ytdlp):
    """Measured: the URL that 403'd on a three-month-old yt-dlp downloaded on a
    current one, with and without a JS runtime. So a 403 here is an age
    problem, and the user cannot fix it — the version ships inside the build.
    """
    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()

    assert t.status == T.ERROR
    assert "403" in t.error
    assert "yt-dlp" in t.error and "stale" in t.error, t.error
    assert "JavaScript" not in t.error, "the JS runtime was a red herring"


def test_the_message_names_the_version_it_is_running(tmp_path, stale_ytdlp):
    """"Out of date" is not actionable without saying which one."""
    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()
    assert yt_dl._ytdlp_version() in t.error, t.error


def test_a_403_from_somewhere_else_is_not_blamed_on_yt_dlp(tmp_path, stale_ytdlp):
    """Plenty of sites answer 403 for their own reasons."""
    t = T.DownloadTask("https://example.test/video", str(tmp_path / "v.mkv"),
                       filename="v.mkv")
    yt_dl.YtDlpDownloader(t).run()
    assert t.status == T.ERROR
    assert "stale" not in t.error, t.error


def test_the_failure_reaches_the_drawer(tmp_path, stale_ytdlp):
    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()
    assert any(e["level"] == "ERROR" and "403" in e["message"] for e in t.events)


# ---- YouTube's challenge left unsolved: say Deno, not "private" ----
UNSOLVED = ("[youtube] abc: n challenge solving failed: Some formats may be missing. "
            "Ensure you have a supported JavaScript runtime and challenge solver "
            "script distribution installed.")


class _UnsolvedYDL(_FakeYDL):
    """How a signed-in YouTube request fails with no runtime to solve its
    challenge: yt-dlp warns, every format drops out (user's log, 2026-11-12)."""

    def extract_info(self, url, download=True):
        self._attempts.append(self._hdrs)
        self.opts["logger"].warning(UNSOLVED)
        raise RuntimeError("ERROR: [youtube] abc: Requested format is not available. "
                           "Use --list-formats for a list of available formats")


@pytest.fixture
def unsolved_ytdlp(monkeypatch):
    attempts = []
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts: _UnsolvedYDL(opts, attempts, lambda h: False)
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return attempts


def _deno(monkeypatch, there):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda n: (
        ("/usr/bin/" + n) if (n != "deno" or there) else None))


def test_no_formats_for_want_of_deno_says_install_deno(tmp_path, unsolved_ytdlp,
                                                       monkeypatch):
    """It said "private, region-locked, or need a sign-in" - none of it so."""
    _deno(monkeypatch, there=False)
    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()
    assert t.status == T.ERROR
    assert "Deno" in t.error and "winget install DenoLand.Deno" in t.error, t.error
    assert "private" not in t.error


def test_with_deno_there_it_is_not_blamed(tmp_path, unsolved_ytdlp, monkeypatch):
    """deno present and the challenge still unsolved: something else broke."""
    _deno(monkeypatch, there=True)
    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()
    assert t.status == T.ERROR
    assert "Deno" not in t.error, t.error


def test_another_site_with_no_formats_is_not_told_about_deno(tmp_path, unsolved_ytdlp,
                                                             monkeypatch):
    _deno(monkeypatch, there=False)
    t = T.DownloadTask("https://example.test/video", str(tmp_path / "v.mkv"),
                       filename="v.mkv")
    yt_dl.YtDlpDownloader(t).run()
    assert "Deno" not in t.error, t.error
