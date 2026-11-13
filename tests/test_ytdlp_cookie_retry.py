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
    """deno there or not, whatever this machine has; ffmpeg there."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: "/usr/bin/" + n)
    monkeypatch.setattr(yt_dl, "_deno", lambda: "/usr/bin/deno" if there else "")


def test_no_formats_for_want_of_deno_says_install_deno(tmp_path, unsolved_ytdlp,
                                                       monkeypatch):
    """It said "private, region-locked, or need a sign-in" - none of it so."""
    _deno(monkeypatch, there=False)
    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()
    assert t.status == T.ERROR
    assert "Deno" in t.error and yt_dl._get_deno() in t.error, t.error
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


# ---- YouTube turning this connection away: plain words, not yt-dlp's flags ----
BOT = ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use "
       "--cookies-from-browser or --cookies for the authentication. See  "
       "https://github.com/yt-dlp/yt-dlp/wiki/FAQ  for how to manually pass cookies.")
RATE = ("ERROR: [youtube] abc: Unable to download webpage: HTTP Error 429: "
        "Too Many Requests (caused by <HTTPError 429: Too Many Requests>)")


class _RefusedYDL(_FakeYDL):
    """The user's 2026-11-12 log: YouTube answered 429 and asked for a sign-in.
    A signed-in try with nothing to solve the challenge got no formats, and
    the retry without cookies met the bot check."""

    def extract_info(self, url, download=True):
        self._attempts.append(self._hdrs)
        return self._fail_when(self._hdrs, self.opts["logger"])


def _refusing(monkeypatch, behave):
    attempts = []
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts: _RefusedYDL(opts, attempts, behave)
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return attempts


def _bot(hdrs, logger):
    raise RuntimeError(BOT)


def _unsolved_then_bot(hdrs, logger):
    if _has_cookie(hdrs):
        logger.warning(UNSOLVED)
        raise RuntimeError("ERROR: [youtube] abc: Requested format is not available.")
    raise RuntimeError(BOT)


def test_the_bot_check_without_a_sign_in_says_send_it_from_the_browser(tmp_path,
                                                                        monkeypatch):
    _refusing(monkeypatch, _bot)
    t = _task(tmp_path, {"User-Agent": "UA"})
    yt_dl.YtDlpDownloader(t).run()
    assert t.status == T.ERROR
    assert "browser" in t.error and "try again later" in t.error, t.error
    assert "--cookies" not in t.error, "a flag the app's user cannot pass"


def test_429_reads_the_same(tmp_path, monkeypatch):
    def rate(hdrs, logger):
        raise RuntimeError(RATE)
    _refusing(monkeypatch, rate)
    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()
    assert "refusing this connection" in t.error, t.error


def test_signed_in_and_still_refused_says_try_later(tmp_path, monkeypatch):
    _deno(monkeypatch, there=True)
    _refusing(monkeypatch, _bot)
    t = _task(tmp_path, {"Cookie": SIGNED_IN})
    yt_dl.YtDlpDownloader(t).run()
    assert "even with your sign-in" in t.error and "try again later" in t.error.lower()
    assert "browser" not in t.error, "it came from the browser already"


# what a browser signed in to YouTube sends - yt-dlp's own test of a sign-in
# (_has_auth_cookies): LOGIN_INFO and one of the SAPISID cookies
SIGNED_IN = "LOGIN_INFO=a; SAPISID=b; VISITOR_INFO1_LIVE=c; YSC=d"
VISITOR = "VISITOR_INFO1_LIVE=c; YSC=d; PREF=f6=40000000"


def test_a_browser_not_signed_in_to_youtube_is_told_to_sign_in(tmp_path, monkeypatch):
    """Cookies came, but a visitor's: "even with your sign-in" was not so."""
    _deno(monkeypatch, there=True)
    _refusing(monkeypatch, _bot)
    t = _task(tmp_path, {"Cookie": VISITOR})
    yt_dl.YtDlpDownloader(t).run()
    assert "even with your sign-in" not in t.error, t.error
    assert "Sign in to YouTube in your browser" in t.error, t.error


@pytest.mark.parametrize("cookie,signed_in", [
    (SIGNED_IN, True),
    ("LOGIN_INFO=a; __Secure-3PAPISID=b", True),
    ("LOGIN_INFO=a; __Secure-1PAPISID=b", True),
    ("SAPISID=b; SID=c", False),           # rotated out: LOGIN_INFO goes first
    (VISITOR, False),
    ("", False),
])
def test_a_sign_in_is_what_yt_dlp_takes_for_one(tmp_path, cookie, signed_in):
    t = _task(tmp_path, {"Cookie": cookie} if cookie else {})
    assert yt_dl._signed_in(t) is signed_in


def test_signed_in_without_deno_says_install_deno(tmp_path, monkeypatch):
    """The real chain: the signed-in try had no formats (nothing to solve the
    challenge), the retry without cookies met the bot check. Deno is the fix."""
    _deno(monkeypatch, there=False)
    attempts = _refusing(monkeypatch, _unsolved_then_bot)
    t = _task(tmp_path, {"Cookie": SIGNED_IN})
    yt_dl.YtDlpDownloader(t).run()
    assert [_has_cookie(a) for a in attempts] == [True, False]
    assert yt_dl._get_deno() in t.error, t.error


def test_another_sites_429_is_left_alone(tmp_path, monkeypatch):
    def rate(hdrs, logger):
        raise RuntimeError(RATE)
    _refusing(monkeypatch, rate)
    t = T.DownloadTask("https://example.test/video", str(tmp_path / "v.mkv"),
                       filename="v.mkv")
    yt_dl.YtDlpDownloader(t).run()
    assert "YouTube" not in t.error, t.error


# ---- a deno installed while HyperFetch runs is used, no restart ----
def _installed_now(monkeypatch, tmp_path):
    """deno put where the system's PATH now points, and not on the PATH this
    process started with - what an install while the app runs leaves."""
    import os
    there = tmp_path / "installed"
    there.mkdir()
    exe = there / ("deno.exe" if os.name == "nt" else "deno")
    exe.write_bytes(b"")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(yt_dl, "_system_path", lambda: str(there), raising=False)
    return str(exe)


def test_a_deno_installed_after_the_app_started_is_handed_to_yt_dlp(tmp_path,
                                                                    monkeypatch):
    """A restart lost the browser's sign-in (cookies live in memory only), and
    a resumed signed-out try met YouTube's bot check."""
    import os
    exe = _installed_now(monkeypatch, tmp_path)
    opts, _ = yt_dl._choosing_opts(_task(tmp_path, {}), str(tmp_path))
    given = (opts.get("js_runtimes") or {}).get("deno", {}).get("path", "")
    assert os.path.normcase(given) == os.path.normcase(exe), opts.get("js_runtimes")
    assert yt_dl.explain(RuntimeError("Requested format is not available"),
                         _task(tmp_path, {}), unsolved=True).find("Deno") < 0


def test_the_advice_keeps_the_sign_in():
    for text in (yt_dl.NEEDS_DENO, yt_dl.SIGNED_IN_NEEDS_DENO):
        assert "restart" not in text.lower() and "Resume" in text, text


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows' registry")
def test_the_system_path_is_read_from_windows():
    assert "system32" in yt_dl._system_path().lower()


def test_the_way_to_get_deno_is_the_systems_own():
    """winget is Windows': from source on Linux or macOS it is no help."""
    assert "winget install DenoLand.Deno" in yt_dl._get_deno(windows=True)
    elsewhere = yt_dl._get_deno(windows=False)
    assert "deno.com" in elsewhere and "winget" not in elsewhere


def test_a_video_id_containing_403_does_not_read_as_stale_ytdlp(tmp_path):
    """'403' inside a YouTube video id must not trigger the HTTP 403 stale
    yt-dlp message."""
    t = _task(tmp_path, {})
    assert yt_dl.explain(RuntimeError("ERROR: [youtube] abc403defgh: Video unavailable"), t) == ""
    assert "stale" in yt_dl.explain(RuntimeError("HTTP Error 403: Forbidden"), t)


def test_installing_deno_and_resuming_within_a_minute_finds_it(tmp_path, monkeypatch):
    """A download fails with 'Install Deno, then Resume' (or NameScout caches a
    miss); the user installs Deno and hits Resume within 60 s. The 1-minute
    NameScout miss cache must not hide the newly installed Deno from Resume."""
    import os
    home = tmp_path / "home"
    deno_bin = home / ".deno" / "bin"
    deno_bin.mkdir(parents=True)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(yt_dl, "_system_path", lambda: "", raising=False)

    seen_runtimes = []

    class _NeedDenoYDL(_FakeYDL):
        def extract_info(self, url, download=True):
            seen_runtimes.append(self.opts.get("js_runtimes"))
            if not (self.opts.get("js_runtimes") or {}).get("deno", {}).get("path"):
                self.opts["logger"].warning(UNSOLVED)
                raise RuntimeError("ERROR: [youtube] abc: Requested format is not available.")
            out = str(tmp_path / "video.mp4")
            open(out, "wb").write(b"ok")
            return {"id": "abc", "title": "video", "ext": "mp4"}

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts: _NeedDenoYDL(opts, [], lambda h: False)
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)

    t = _task(tmp_path, {})
    yt_dl.YtDlpDownloader(t).run()
    assert t.status == T.ERROR and "Deno" in t.error

    # Even if NameScout also ticked while Deno was missing (caching the miss):
    assert yt_dl._deno() == ""

    # User installs Deno within the same minute and clicks Resume:
    exe = deno_bin / ("deno.exe" if os.name == "nt" else "deno")
    exe.write_bytes(b"")
    exe.chmod(0o755)

    yt_dl.YtDlpDownloader(t).run()
    assert t.status == T.COMPLETED, t.error
    given = (seen_runtimes[-1] or {}).get("deno", {}).get("path", "")
    assert os.path.normcase(given) == os.path.normcase(str(exe))


