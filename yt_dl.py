"""yt-dlp integration — download from media pages (YouTube, Vimeo, etc.) that a
plain HTTP byte-download can't handle.

A YtDlpDownloader is bound to one DownloadTask and drives the yt-dlp library with
progress hooks that update the task in place and honour pause/cancel (the hook
raises to abort). yt-dlp is imported lazily (heavy dependency) — it's declared to
PyInstaller in HyperFetch.spec for the frozen build.

A video that is only waiting its turn is given its name by NameScout, at the
end of this file: the same name, by the same rules, its download gives it.
That name is the video's title, or the one the user typed for the download
(DownloadTask.name_chosen) with the extension yt-dlp picks.
"""
import os
import time
import logging
import threading

import task as T

log = logging.getLogger("hyperfetch.ytdlp")

# media-page hosts where yt-dlp is the right engine (not a direct file URL)
_SITES = (
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv",
    "tiktok.com", "instagram.com", "facebook.com", "twitter.com", "x.com",
    "soundcloud.com", "reddit.com", "bilibili.com", "rumble.com", "ok.ru",
)

# How long can_extract waits for yt-dlp to find a video on a page before the
# page counts as having none. Extraction is a few requests; this only bounds a
# host that answers slowly or not at all.
EXTRACT_TIMEOUT = 60


def is_ytdlp_url(url):
    u = (url or "").lower()
    if not u.startswith(("http://", "https://")):
        return False
    return any(d in u for d in _SITES)


def is_dash(url="", filename="", ctype=""):
    """A DASH manifest (``.mpd``) — an XML index, not the media.

    Byte-downloading one writes a few KB of XML named like a video, and the HLS
    engine can't read it either (different format). yt-dlp's generic extractor
    parses the manifest, fetches the segments and merges the separate video/audio
    adaptation sets with the bundled ffmpeg."""
    u = (url or "").split("?")[0].lower()
    f = (filename or "").lower()
    c = (ctype or "").lower()
    return u.endswith(".mpd") or f.endswith(".mpd") or "dash+xml" in c


def is_ytdlp_task(t, ctype=""):
    """Whether a download is yt-dlp's to fetch: "Use yt-dlp" is ticked, the
    link is a media site's page or a DASH manifest (`ctype`: what the server
    called it, once it has been asked), or Settings sends its host there. A
    torrent never is.

    One answer for the engine that routes a download (downloader.py) and for
    NameScout, which names the ones that wait."""
    import torrent
    import utils
    if torrent.is_torrent_task(t.url, t.filename):
        return False
    return bool(getattr(t, "use_ytdlp", False) or is_ytdlp_url(t.url)
                or is_dash(t.url, t.filename, ctype)
                or utils.host_rule(t.url).get("ytdlp"))


def _formats_unavailable(err):
    """True when extraction produced no usable video format.

    Distinct from a download that started and then broke: this is the site
    handing back nothing playable, which a retry with different request context
    can genuinely fix.
    """
    low = str(err).lower()
    return ("requested format is not available" in low
            or "only images are available" in low
            or "no video formats found" in low)


def available():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


class _Abort(Exception):
    pass


class _Failed(Exception):
    """The segmented engine broke after it started; its error stands."""


class _YtLog:
    """Routes yt-dlp's own messages (deprecation notices, ERROR echoes) into our
    debug log instead of stdout/stderr, so they don't spam the app console."""
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): log.debug("yt-dlp: %s", m)
    def error(self, m): log.debug("yt-dlp: %s", m)


def _http_headers(t):
    """The browser context a task carries (UA/Referer/Cookie from the extension)."""
    hdrs = getattr(t, "headers", {}) or {}
    return {k: v for k, v in hdrs.items()
            if k.lower() in ("user-agent", "referer", "cookie")}


def _net_opts():
    """yt-dlp options for the global TLS + proxy settings."""
    import utils
    opts = {}
    if not utils.VERIFY_TLS:
        opts["nocheckcertificate"] = True
    if utils.PROXIES:
        opts["proxy"] = utils.PROXIES.get("https") or utils.PROXIES.get("http")
    return opts


def _ffmpeg_dir():
    """The folder ffmpeg is in (bundled with the app, or on PATH), or None.

    With ffmpeg we can merge separate video+audio streams -> real 1080p/4K, and
    videos that only offer DASH (no combined stream) become downloadable.
    Without it we are limited to single muxed streams (<=720p on YouTube)."""
    import shutil
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(base, "bin", "ffmpeg.exe")
    if os.path.exists(bundled):
        return os.path.dirname(bundled)
    which = shutil.which("ffmpeg")
    return os.path.dirname(which) if which else None


# What a video's file is called: its title, or the name the user gave the
# download with the extension yt-dlp picks. That name is a field of the
# video's info (_named), not text in the template: yt-dlp expands environment
# variables in a template's own text ("$HOME", a leading "~"), never in a
# field's value - and a playlist's entries, which are not given it, fall back
# to their titles.
TITLED = "%(title)s.%(ext)s"
CHOSEN = "%(hyperfetch_name,title)s.%(ext)s"


def _named(t, info):
    """`info`, given the name the user chose for the task's file - for
    CHOSEN, wherever yt-dlp names the file from it. Not a playlist, nor an
    entry of one: every entry would get the one name, and all but the first
    would be taken for downloaded already. They keep their titles."""
    name = getattr(t, "name_chosen", "")
    if (name and info and info.get("_type", "video") == "video"
            and info.get("playlist_index") is None):
        info["hyperfetch_name"] = name
    return info


def _choosing_opts(t, out_dir):
    """The yt-dlp options that decide which format is picked and what the file
    is called, and where ffmpeg is: ``(opts, ffdir)``.

    Shared by the download and by the look a waiting download gets (look_up),
    so the name a video is given while it waits is the name its download
    gives it."""
    import re
    opts = {
        "outtmpl": os.path.join(out_dir.replace("%", "%%"),
                                CHOSEN if getattr(t, "name_chosen", "") else TITLED),
        "noplaylist": True,
        "logger": _YtLog(),
        "quiet": True, "no_warnings": True,
    }
    http_headers = _http_headers(t)
    if http_headers:
        opts["http_headers"] = http_headers
    ffdir = _ffmpeg_dir()
    if ffdir:
        opts["ffmpeg_location"] = ffdir

    # No JS runtime is bundled. yt-dlp warns that YouTube extraction without
    # one is deprecated, but measured against real videos it currently changes
    # nothing: the same 33 formats up to 2160p come back with and without a
    # runtime, because YouTube is not demanding the "n challenge" for anonymous
    # requests. Bundling one is not free either — deno is ~110 MB, and quickjs
    # is 2 MB but yt-dlp ships no solver lib for it, so it must fetch one at
    # solve time. Revisit when YouTube actually starts requiring it; the
    # cookie fallback (_cookies_last) is what fixes the failure seen in practice.

    # Build a format string that never hard-fails with "requested format is
    # not available": prefer a height-capped merge when ffmpeg is present,
    # else a single muxed stream — always with a plain "b" fallback.
    req = (getattr(t, "yt_format", "") or "").strip()
    mh = re.search(r"height<=(\d+)", req)
    h = mh.group(1) if mh else None
    if req.startswith("ba"):                        # audio-only intent
        opts["format"] = "ba[ext=m4a]/ba/b"
    elif ffdir:
        opts["format"] = (f"bv*[height<={h}]+ba/b[height<={h}]/b" if h else "bv*+ba/b")
    else:
        opts["format"] = (f"b[height<={h}]/b" if h else "b")

    # respect the global TLS + proxy settings
    opts.update(_net_opts())
    return opts, ffdir


def _cookies_last(attempt, opts):
    """``attempt(opts)`` - and, when the site hands back no usable format to a
    request that carried cookies, once more without them."""
    try:
        return attempt(opts)
    except (_Abort, _Failed):
        raise
    except Exception as e:
        # YouTube answers a cookie-bearing request with a player response
        # whose formats need the "n challenge" solved. Without a JS runtime
        # yt-dlp cannot solve it, every video format drops out ("Only images
        # are available") and extraction dies with "Requested format is not
        # available" — while the very same URL with no cookies serves normal
        # formats. That is why the browser's right-click download failed on a
        # video that New Download handled: only the extension sends cookies.
        # Cookies still matter for private/members-only videos, so try them
        # first and fall back rather than dropping them outright. The retry
        # also covers a plain transient extraction failure.
        headers = opts.get("http_headers") or {}
        if not (_formats_unavailable(e)
                and any(k.lower() == "cookie" for k in headers)):
            raise
        log.info("yt-dlp: no formats with cookies, retrying without them: %s",
                 str(e)[:120])
        retry = dict(opts)
        retry["http_headers"] = {k: v for k, v in headers.items()
                                 if k.lower() != "cookie"}
        return attempt(retry)


def direct_format(info):
    """Whether yt-dlp would fetch the chosen format as one plain http(s) file:
    the case the app's segmented engine can take over.

    Not a merge of separate video and audio, not HLS or DASH fragments, not
    a live stream, not a request that needs a body or a browser's TLS
    fingerprint, and not a format whose extractor asks for it in chunks
    (YouTube's, Google Drive's): yt-dlp documents chunking as a way past a
    server's throttling, and ranges of the engine's own size would not keep
    to it."""
    if not info or info.get("requested_formats"):
        return False
    if info.get("protocol") not in ("http", "https") or not info.get("url"):
        return False
    if info.get("fragments") or info.get("is_live"):
        return False
    if info.get("request_data") or info.get("impersonate"):
        return False
    return not (info.get("downloader_options") or {}).get("http_chunk_size")


def media_headers(info):
    """The headers yt-dlp's own download sends to a format's link: the
    format's http_headers (the browser's User-Agent and Referer reach them
    through the http_headers option) over an uncompressed Accept-Encoding.

    Cookies are not a header here. yt-dlp keeps them in its jar, each scoped
    to its host - the page's own to the page's host - and the engine is given
    the jar, so a CDN gets the cookies yt-dlp would send it and no others."""
    return {"Accept-Encoding": "identity",
            **{k: v for k, v in (info.get("http_headers") or {}).items()
               if k.lower() != "cookie"}}


def _has_media(info):
    """An extraction result with something in it for the engine to download."""
    if not info:
        return False
    if info.get("_type") in ("playlist", "multi_video"):
        return bool(info.get("entries"))
    return bool(info.get("formats") or info.get("url"))


def can_extract(t):
    """Whether yt-dlp finds a video at the task's URL: one extraction, nothing
    downloaded.

    For a link that answered with a web page where a file was expected. A video
    page on a site _SITES does not name looks exactly like that, and yt-dlp
    knows far more sites than the list. False when it finds nothing, is not
    installed or takes longer than EXTRACT_TIMEOUT - and when the task is
    paused or cancelled first, which the caller checks.

    Extraction cannot be interrupted, so it runs on a thread of its own while
    this polls pause/cancel in 0.2 s slices. A look the user walked away from
    finishes in the background and its answer is dropped.
    """
    if t.pause_requested or t.cancel_requested:
        return False
    opts = {
        "logger": _YtLog(),
        "quiet": True, "no_warnings": True,
        "noplaylist": True,
        # a playlist's first entry, unresolved, is enough to know
        "extract_flat": "in_playlist", "playlist_items": "1",
        "socket_timeout": 15,           # the downloader's own CONNECT_TIMEOUT
        **_net_opts(),
    }
    http_headers = _http_headers(t)
    if http_headers:
        opts["http_headers"] = http_headers
    found = []

    def look():
        try:
            import yt_dlp               # a first import is slow too; keep it here
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(t.url, download=False)
        except Exception as e:          # not installed, unsupported page, login wall, no formats
            log.debug("yt-dlp: no video at %s: %s", t.url, str(e)[:200])
            return
        found.append(_has_media(info))

    th = threading.Thread(target=look, daemon=True, name="ytdlp-look")
    th.start()
    deadline = time.monotonic() + EXTRACT_TIMEOUT
    while th.is_alive():
        if t.pause_requested or t.cancel_requested:
            return False
        if time.monotonic() >= deadline:
            log.info("yt-dlp took over %ss on %s - taken as no video",
                     EXTRACT_TIMEOUT, t.url)
            return False
        th.join(0.2)
    return bool(found and found[0])


def _ytdlp_version():
    """The bundled yt-dlp's version, for error messages. It is frozen into the
    build, so naming it is the difference between "something is broken" and
    "this build has aged out"."""
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return "unknown version"


def folder_for(t, name):
    """The folder a video belongs in, when that is not the one `name` -
    yt-dlp's path for it - is in: its category's, under the folder the app
    sorts into (task.sort_base). "" when it stays where it is.

    It stays when the user chose its folder, when it is there already, and
    when anything of the download is on disk: yt-dlp's partial is in the
    folder the download began in, and moved now it would start again from
    nothing beside it. Such a download is filed when it finishes, as before.
    """
    base = getattr(t, "sort_base", None)
    if not base:
        return ""
    import utils
    here = os.path.dirname(name)
    there = os.path.join(base, utils.category_for(os.path.basename(name)))
    if os.path.normcase(os.path.abspath(there)) == os.path.normcase(os.path.abspath(here)):
        return ""
    return "" if _begun(name) else there


def take_name(t, name, size=0):
    """Write a video's name onto its task: the path, the name shown, the size
    when the site said one - and that yt-dlp has been asked (task.yt_named)."""
    t.save_path, t.filename = name, os.path.basename(name)
    if size and not t.total_size:
        t.total_size = int(size)
    t.yt_named = True
    t.meta_failed = False


def _begun(name):
    """Whether anything of this video is on disk already: the file, yt-dlp's
    .part and .ytdl beside it, or the parts of a merge (Title.f137.mp4)."""
    import glob
    return bool(glob.glob(glob.escape(os.path.splitext(name)[0]) + ".*"))


def _leave(folder, base):
    """Remove the folder a download only waited in, if that left it empty
    and it is one of the app's own, directly under the folder it sorts
    into. rmdir takes nothing that holds a file."""
    import utils
    names = {c.lower() for c in utils.CATEGORIES} | {"other"}
    if (os.path.basename(folder).lower() in names
            and os.path.normcase(os.path.abspath(os.path.dirname(folder)))
            == os.path.normcase(os.path.abspath(base))):
        try:
            os.rmdir(folder)
        except OSError:
            pass                    # still holds something


# The extensions of a typed name that are yt-dlp's to choose: video and audio
# containers (yt_dlp.utils.MEDIA_EXTENSIONS - copied, for yt-dlp is slow to
# import and the window asks this), MPEG-TS, and the ".bin" a name with no
# extension is given (utils.filename_from_url).
_CONTAINERS = {"." + e for e in (
    "3g2", "3gp", "f4v", "mk3d", "divx", "mpg", "ogv", "m4v", "wmv", "avi", "flv",
    "mkv", "mov", "mp4", "webm", "ts", "aac", "ape", "asf", "f4a", "f4b", "m4b",
    "m4r", "oga", "ogx", "spx", "vorbis", "wma", "weba", "aiff", "alac", "flac",
    "m4a", "mka", "mp3", "ogg", "opus", "wav", "bin")}


def stem(name):
    """What a video's file keeps of a name the user typed: all of it but an
    extension yt-dlp chooses (_CONTAINERS). "Lecture 1.mp4" and "Lecture
    1.bin" give "Lecture 1"; "Part 1.5" stays "Part 1.5"."""
    base, ext = os.path.splitext(name or "")
    return base if base and ext.lower() in _CONTAINERS else (name or "")


def name_for(t, typed):
    """What a download is called when the user types `typed` for it: that -
    but a video yt-dlp has named keeps the extension yt-dlp gave it, as its
    file will ("Lecture 1.mkv" typed for an mp4 is "Lecture 1.mp4"). A
    playlist's ".bin" was never yt-dlp's."""
    ext = os.path.splitext(t.filename or "")[1]
    if getattr(t, "yt_named", False) and ext and ext.lower() != ".bin":
        return stem(typed) + ext
    return typed


def rename(queue, t, name):
    """Rename a video that has not finished to `name` (name_for's), as the
    user asked: it is called that while it waits, and so is its file
    (DownloadTask.name_chosen). What its download left on disk - yt-dlp's
    .part, the parts of a merge - is renamed with it (_carry), or it would
    start again from nothing beside it.

    Not while it runs: yt-dlp named the file it is writing as it started,
    and the run ends under that name. False then, and nothing is changed.
    A waiting one is changed with the queue's lock held
    (QueueManager.while_waiting), so it cannot start half-way through.

    A name is taken when a file has it, as ever - or another download's
    partial: yt-dlp would carry on from that, into this video.
    """
    import utils

    def change():
        here = os.path.dirname(t.save_path) or "."
        base, ext = os.path.splitext(name)
        new, n = utils.unique_path(here, name), 0
        while _left(new):
            n += 1
            new = utils.unique_path(here, "%s (%d)%s" % (base, n, ext))
        if getattr(t, "yt_named", False):
            _carry(t.save_path, new)
        t.save_path, t.filename = new, os.path.basename(new)
        t.name_chosen = stem(t.filename)

    if t.status in WAITING:
        return queue.while_waiting(t, change)
    if getattr(t, "_worker_alive", False) or t.status == T.DOWNLOADING:
        return False
    change()                    # failed or cancelled: nothing starts it meanwhile
    return True


def _left(path):
    """The names of what yt-dlp leaves beside a video it names `path` until
    it is done: its .part and .ytdl, a fragmented download's fragments, the
    parts of a merge and theirs (Title.f137.mp4.part), a merge it was making
    (Title.temp.mp4). Not the video itself, nor anything else that only
    begins like it: those may be the user's."""
    import re
    was, ext = os.path.splitext(os.path.basename(path))
    tail = r"(?:\.part(?:-Frag\d+)?(?:\.part)?|\.ytdl)"
    left = re.compile(r"%s\.(?:(?:f[^.]+|temp)\.[^.]+%s?|%s%s)" % (
        re.escape(was), tail, re.escape(ext[1:]), tail))
    try:
        return [n for n in os.listdir(os.path.dirname(path) or ".") if left.fullmatch(n)]
    except OSError:
        return []


def _carry(old, new):
    """Rename what yt-dlp left of the video it named `old` (_left) to go with
    `new`, in the same folder. Never over a file that is there."""
    here = os.path.dirname(old) or "."
    was = os.path.splitext(os.path.basename(old))[0]
    now = os.path.splitext(os.path.basename(new))[0]
    if was == now:
        return
    for n in _left(old):
        frm, to = os.path.join(here, n), os.path.join(here, now + n[len(was):])
        if os.path.exists(to):
            log.warning("not renaming %s: %s is there", frm, to)
            continue
        try:
            os.rename(frm, to)
        except OSError as e:
            log.warning("could not rename %s: %s", frm, e)


class YtDlpDownloader:
    def __init__(self, dtask: "T.DownloadTask", segments=None):
        self.t = dtask
        # connections for a file the segmented engine takes over (None: its
        # default)
        self.segments = segments
        self._fetched = ""              # the file the engine finished, if it did
        self._settled = False           # named and filed already, this run
        self._moved = False             # ...into another folder than it began in

    def _take_over_plain_files(self, ydl):
        """Have the app's segmented engine fetch the video when yt-dlp would
        fetch one plain file over one connection.

        yt-dlp still extracts, picks the format, names the file and finishes
        it - its fixups and the rest run as ever; only the bytes come over
        parallel ranges. Every download goes through ydl.dl(): that is wrapped
        for the video's own file, and process_info is wrapped to tell that
        apart from the parts of a merge, which stay yt-dlp's. Subtitles and
        format tests call dl() with more arguments and are left alone too. A
        file the engine cannot start goes on to yt-dlp's own dl().
        """
        process_info = getattr(ydl, "process_info", None)
        dl = getattr(ydl, "dl", None)
        if process_info is None:
            return
        take_over = dl is not None and self.segments != 1
        whole = [False]

        def _process_info(info, *a, **kw):
            # yt-dlp has read the page and is about to download this video:
            # the first moment its name and kind are known
            _named(self.t, info)
            self._settle(ydl, info)
            whole[0] = take_over and not info.get("requested_formats")
            try:
                return process_info(info, *a, **kw)
            finally:
                whole[0] = False

        def _dl(name, info, *a, **kw):
            if whole[0] and not a and not kw and direct_format(info):
                whole[0] = False
                if self._fetch(ydl, name, info):
                    return True, True
            return dl(name, info, *a, **kw)

        ydl.process_info = _process_info
        if take_over:
            ydl.dl = _dl

    def _settle(self, ydl, info):
        """Give the task its real name and folder, before a byte is fetched.

        A pasted video page is added as "download.bin" in Other: nobody knows
        what it is yet. yt-dlp knows once it has read the page - the title, the
        container, so Video or Music - and used to keep that to itself until
        the file was finished. The list said "download.bin" for the whole
        download, and the finished file was moved out of Other afterwards.

        Once a run, when yt-dlp hands over the video it is about to download.
        A download that waited has usually been named already (NameScout);
        this is the same name by the same rules (folder_for), from the page as
        it is now.
        """
        if self._settled:
            return
        self._settled = True
        try:
            name = ydl.prepare_filename(info)
        except Exception as e:
            log.debug("yt-dlp: no name for %s yet: %s", self.t.url, e)
            return
        if not name:
            return
        there = folder_for(self.t, name)
        if there and self._send_to(ydl, there):
            here, name = os.path.dirname(name), ydl.prepare_filename(info)
            self._moved = True
            _leave(here, self.t.sort_base)
        take_name(self.t, name, info.get("filesize") or info.get("filesize_approx") or 0)
        log.info("yt-dlp named it: %s", self.t.filename)

    @staticmethod
    def _send_to(ydl, folder):
        """Point yt-dlp's output at another folder. It reads its template each
        time it names a file, so changing it now is in time for this video."""
        templates = (getattr(ydl, "params", None) or {}).get("outtmpl")
        if not isinstance(templates, dict) or "default" not in templates:
            return False
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            log.debug("yt-dlp: cannot use %s: %s", folder, e)
            return False
        # a "%" in a folder's name is not the start of a field; the file is
        # named as it was (TITLED or CHOSEN)
        templates["default"] = os.path.join(folder.replace("%", "%%"),
                                            os.path.basename(templates["default"]))
        return True

    def _fetch(self, ydl, name, info):
        """The segmented engine fetches info's file into `name`, yt-dlp's name
        for it. True when it finished; False when it did not start, and yt-dlp
        fetches it instead. Raises _Abort on pause or cancel, _Failed when it
        broke after starting."""
        import downloader
        import utils
        if self.t.pause_requested or self.t.cancel_requested:
            raise _Abort()   # asked while yt-dlp extracted: not one request more
        url = info["url"]
        if os.path.exists(name) or os.path.isfile(name + ".part"):
            return False     # already there (yt-dlp says so), or yt-dlp's own partial
        if utils.host_rule(url).get("ytdlp"):
            return False     # Settings: yt-dlp fetches from this host
        eng = downloader.Downloader(
            self.t, segments=(downloader.DEFAULT_SEGMENTS if self.segments is None
                              else self.segments),
            url=url, headers=media_headers(info), cookies=getattr(ydl, "cookiejar", None))
        if eng.num_segments == 1:
            return False     # a host rule or Max Connections says one connection
        before = self.t.save_path, self.t.filename
        self.t.save_path, self.t.filename = name, os.path.basename(name)
        if not eng.run_media():
            self.t.save_path, self.t.filename = before
            self.t.log_event("The video's link would not be fetched in parallel "
                             "ranges; yt-dlp downloads it itself")
            return False
        if eng.media_done:
            self._fetched = self.t.save_path
            return True
        if self.t.pause_requested or self.t.cancel_requested:
            raise _Abort()
        raise _Failed()

    def run(self):
        self.t.status = T.DOWNLOADING
        self.t.error = ""
        self.t.meta_failed = False      # a page that would not read while it waited
        self.t.supports_range = False
        log.info("yt-dlp start: %s", self.t.url)
        try:
            import yt_dlp
        except ImportError:
            self.t.status = T.ERROR
            self.t.error = "yt-dlp not installed — run: pip install yt-dlp"
            return

        out_dir = os.path.dirname(self.t.save_path) or "."
        _pre_existing = set(os.listdir(out_dir)) if os.path.isdir(out_dir) else set()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = f"cannot create folder: {e}"
            return

        final = {"path": ""}

        def hook(d):
            if self.t.cancel_requested or self.t.pause_requested:
                raise _Abort()
            st = d.get("status")
            if st == "downloading":
                self.t.downloaded = d.get("downloaded_bytes") or 0
                self.t.total_size = (d.get("total_bytes")
                                     or d.get("total_bytes_estimate") or 0)
            elif st == "finished":
                final["path"] = d.get("filename") or final["path"]

        opts, ffdir = _choosing_opts(self.t, out_dir)
        opts.update({
            "progress_hooks": [hook],
            "noprogress": True,
            "concurrent_fragment_downloads": 4,
            "retries": 5, "fragment_retries": 5,
            "continuedl": True,           # resume a paused/partial download
            "nopart": False,
        })

        def _attempt(o):
            """One yt-dlp run. Returns its guess at the output path."""
            with yt_dlp.YoutubeDL(o) as ydl:
                self._take_over_plain_files(ydl)
                info = ydl.extract_info(self.t.url, download=True)
                try:
                    return ydl.prepare_filename(_named(self.t, info))
                except Exception:
                    return ""

        try:
            guess = _cookies_last(_attempt, opts)
            if self._fetched and os.path.exists(self._fetched):
                path = self._fetched
            else:
                path = guess if (guess and os.path.exists(guess)) else final["path"]
                # yt-dlp fetched it itself: a partial the engine left from an
                # earlier run is garbage now
                import downloader
                downloader.discard_partial(self.t)
            if not (path and os.path.exists(path)) and not self._moved:
                # by what is new in the folder - which says nothing in a folder
                # the download moved to after that list was taken
                path = self._newest(out_dir, _pre_existing)
            if path and os.path.exists(path):
                self.t.save_path = path
                self.t.filename = os.path.basename(path)
                try:
                    sz = os.path.getsize(path)
                    self.t.total_size = sz
                    self.t.downloaded = sz
                except OSError:
                    pass
            self.t.status = T.COMPLETED
            log.info("yt-dlp done: %s", self.t.filename)
        except _Abort:
            self.t.status = T.CANCELLED if self.t.cancel_requested else T.PAUSED
            if self.t.cancel_requested:
                import downloader
                downloader.discard_partial(self.t)
        except _Failed:
            pass                        # the engine's own status and error stand
        except Exception as e:
            self.t.status = T.ERROR
            import re as _re
            msg = _re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip()    # strip ANSI colour codes
            low = msg.lower()
            # A 403 on the media URLs almost always means this build's yt-dlp
            # has aged out. YouTube changes how it signs those URLs every few
            # weeks and an older copy simply stops working. The version is
            # frozen into the build, so the user cannot update it themselves
            # and the message has to say that rather than blame the network.
            #
            # Measured rather than assumed: the URL that failed here on a
            # three-month-old yt-dlp downloaded fine on a current one — with a
            # JavaScript runtime present and without. The "no JS runtime"
            # warning yt-dlp prints alongside this is a red herring for it.
            if ("403" in low or "unable to download video data" in low) and                     "youtu" in (self.t.url or "").lower():
                self.t.error = (
                    "YouTube refused the download (403). That usually means "
                    "HyperFetch's bundled yt-dlp (%s) has gone stale — YouTube "
                    "changes how it signs video links every few weeks. A newer "
                    "HyperFetch build is the fix." % _ytdlp_version())
            elif ffdir is None and ("requested format is not available" in low
                                    or "ffmpeg" in low or "merging" in low):
                self.t.error = ("This video has no combined audio+video stream — it needs "
                                "ffmpeg to merge them (bundled in the app installer; on a "
                                "source run, put ffmpeg on your PATH).")
            elif "requested format is not available" in low:
                # ffmpeg IS present, so merging is not the problem — the site
                # served no downloadable video stream at all. Blaming ffmpeg here
                # sent the user looking for a missing binary that was never missing.
                self.t.error = ("The site offered no downloadable video stream for this "
                                "one. It may be private, region-locked, or need a sign-in "
                                "the app doesn't have.")
            else:
                self.t.error = "yt-dlp: " + msg[:200]
            log.error("yt-dlp failed: %s — %s", self.t.url, str(e)[:200])

    @staticmethod
    def _newest(out_dir, pre_existing=None):
        """Newest non-partial file in out_dir that was NOT in pre_existing —
        prevents picking up unrelated files."""
        pre = pre_existing or set()
        newest = None
        try:
            for name in os.listdir(out_dir):
                if name.endswith((".part", ".ytdl", ".tmp")):
                    continue
                if name in pre:
                    continue
                p = os.path.join(out_dir, name)
                if not os.path.isfile(p):
                    continue
                mt = os.path.getmtime(p)
                if newest is None or mt > newest[0]:
                    newest = (mt, p)
        except OSError:
            return ""
        return newest[1] if newest else ""


# ---- a name while it waits ---------------------------------------------------
# A video page is added as "watch.bin": the link says nothing else. yt-dlp
# knows the title once it has read the page, and a download does that as it
# starts - so everything still waiting its turn was a row of "watch.bin", and
# nobody could tell what was already in the list. NameScout reads the page for
# those and gives each the name and folder its download will, as
# torrent.MetadataPrefetcher does for a waiting magnet.
NAME_TICK = 3.0               # how often it looks for a waiting video with no name
# A page that would not read is asked for again: soon the first time - the
# app starts with Windows, often before the network is up - and then ever
# more seldom, for a video that is gone stays gone.
NAME_RETRY_FIRST = 60.0
NAME_RETRY = 900.0            # the second wait; twice as long each time after,
NAME_RETRY_MAX = 6 * 3600.0   # up to this
WAITING = (T.QUEUED, T.PAUSED, T.SCHEDULED)


def _plain(err):
    """yt-dlp's message without its colour codes and its "ERROR:"."""
    import re
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(err)).strip()
    return re.sub(r"^ERROR:\s*", "", msg)[:200]


def look_up(t, give_up=lambda: False):
    """What the task's video will be called and how big it is, read off its
    page: ``(path, size)``, in the folder the task is in. One extraction with
    the options its download uses; nothing is downloaded.

    ``("", 0)`` for a link with no one name to give: a playlist, of which no
    more than the first entry is listed. None when `give_up()` says to stop
    waiting - extraction cannot be interrupted, so it runs on a thread of its
    own, as in can_extract, and an answer nobody waits for is dropped. Raises
    what yt-dlp raised, and TimeoutError past EXTRACT_TIMEOUT.
    """
    opts, _ = _choosing_opts(t, os.path.dirname(t.save_path) or ".")
    opts.update({"extract_flat": "in_playlist", "playlist_items": "1",
                 "socket_timeout": 15})
    url, box = t.url, {}

    def attempt(o):
        import yt_dlp                   # a first import is slow too; keep it here
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info or info.get("_type") in ("playlist", "multi_video"):
                return "", 0
            size = info.get("filesize") or info.get("filesize_approx") or 0
            return ydl.prepare_filename(_named(t, info)), int(size)

    def look():
        try:
            box["found"] = _cookies_last(attempt, opts)
        except Exception as e:          # unsupported page, login wall, no formats
            box["error"] = e

    th = threading.Thread(target=look, daemon=True, name="ytdlp-name")
    th.start()
    deadline = time.monotonic() + EXTRACT_TIMEOUT
    while th.is_alive():
        if give_up():
            return None
        if time.monotonic() >= deadline:
            raise TimeoutError("the page took over %d s to read" % EXTRACT_TIMEOUT)
        th.join(0.2)
    if "error" in box:
        raise box["error"]
    return box["found"]


class NameScout:
    """Gives waiting videos their names, in the background.

    One thread, one page at a time, NAME_TICK apart: what downloads next
    first. The name is the one the download itself would give (look_up reads
    the page with its options) and is written through `waiting_fn` -
    QueueManager.while_waiting, which does it only while no worker has the
    task. A download that starts while its page is being read names itself,
    as it always has.
    """

    def __init__(self, tasks_fn, waiting_fn, enabled_fn=lambda: True, look_fn=None,
                 clock=time.time):
        self._tasks_fn = tasks_fn
        self._waiting_fn = waiting_fn
        self._enabled_fn = enabled_fn     # Settings -> Downloads, read every round
        self._look_fn = look_fn or look_up
        self._clock = clock
        self._fails = {}              # task id -> pages that would not read, in a row
        self._stop = threading.Event()
        self._thread = None
        # how many downloads it has settled: the window saves the list when
        # this moves, since nobody did anything that would save it
        self.named = 0

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="name-scout")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(NAME_TICK):
            try:
                self.run_once()
            except Exception:
                log.exception("name scout")

    def _due(self, t, now):
        return (t.status in WAITING and not getattr(t, "yt_named", False)
                and not getattr(t, "_worker_alive", False)
                and now >= float(getattr(t, "meta_retry_after", 0) or 0)
                and is_ytdlp_task(t))

    def _next(self):
        tasks = list(self._tasks_fn() or [])
        live = {t.id for t in tasks}
        for gone in [k for k in self._fails if k not in live]:
            del self._fails[gone]
        now = self._clock()
        due = [t for t in tasks if self._due(t, now)]
        # what downloads next first: the queued before the paused
        return min(due, key=lambda t: t.status == T.PAUSED) if due else None

    def run_once(self):
        """Name one waiting video. Returns the task asked about, or None."""
        if not self._enabled_fn():
            self._forget()
            return None
        t = self._next()
        if t is None or not available():
            return None
        was = t.save_path
        t.meta_fetching = True          # the card says "Fetching details…"
        try:
            found = self._look_fn(
                t, lambda: self._stop.is_set() or t.status not in WAITING)
        except Exception as e:
            self._failed(t, e)
            return t
        finally:
            t.meta_fetching = False
        if found is None:
            return t                    # its download started, or the app is closing
        name, size = found
        here = os.path.dirname(name)
        there = folder_for(t, name) if name else ""
        if there:
            # the folder is made before the lock is taken: a disk that has to
            # spin up must not hold the whole queue still
            try:
                os.makedirs(there, exist_ok=True)
                name = os.path.join(there, os.path.basename(name))
            except OSError as e:
                log.debug("cannot use %s: %s", there, e)
                there = ""
        taken = []

        def take():
            if t.save_path != was:
                return                  # renamed or moved by hand meanwhile: asked again
            if name:
                take_name(t, name, size)
            else:
                t.yt_named = True       # asked: a playlist has no one name
                t.meta_failed = False
            taken.append(True)

        self._waiting_fn(t, take)
        if not taken:
            return t
        self._fails.pop(t.id, None)
        self.named += 1
        if name:
            if there:
                _leave(here, t.sort_base)
            log.info("named while it waits: %s", t.filename)
        return t

    def _forget(self):
        """Switched off: nobody is reading pages, so no row goes on saying
        that one would not read - which looked as if the switch did nothing."""
        if not self._fails:
            return
        for t in list(self._tasks_fn() or []):
            if t.id in self._fails:
                t.meta_failed = False
                t.meta_retry_after = 0.0
        self._fails.clear()

    def _failed(self, t, why):
        n = self._fails.get(t.id, 0) + 1
        self._fails[t.id] = n
        wait = (NAME_RETRY_FIRST if n == 1
                else min(NAME_RETRY * 2 ** (n - 2), NAME_RETRY_MAX))
        t.meta_failed = True
        t.meta_retry_after = self._clock() + wait
        reason = _plain(why)
        if n == 1:                      # once: the next tries say the same
            t.log_event("Could not read the page while it waits: " + reason,
                        level="WARNING")
        log.info("no name yet for %s (try %d, next in %d min): %s",
                 t.filename, n, wait // 60, reason)
