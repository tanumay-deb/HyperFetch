"""Minimal HLS (.m3u8) video grabber.

Web video is usually delivered as an HLS playlist: a small text manifest that
lists many short .ts/.m4s segments. Downloading the .m3u8 itself just saves the
text, not the video — you have to fetch every segment and join them. This module
does that: master->variant selection, AES-128 decryption, the segments joined
into one file, with pause/cancel/progress. With ffmpeg at hand that file is
then rewritten as an ordinary MP4 (remux), which a player can seek in at once;
without it the joined file is what is saved - a .ts, or a fragmented .mp4 for
fMP4 playlists - which plays in VLC and most players all the same.
A live stream has no end to fetch, so it is reported as one, not saved as the
few seconds its playlist happens to list.
"""
import os
import re
import time
import logging
import tempfile
import struct
import threading
import subprocess
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

import task as T
import utils
from downloader import Session     # a sign-in kept across a redirect on its site

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("hyperfetch.hls")

HEADERS = utils.DEFAULT_HEADERS
TIMEOUT = 20
SEG_RETRIES = 3
PARALLEL = 6          # default concurrent segment downloads (overridden by the
                      # app's Max-connections setting when one is configured)
# Hard ceiling on segment threads, whatever Max-connections says.
#
# That setting goes up to 1000 and this is the one place it RAISES a worker
# count instead of lowering it — every worker being a real OS thread. Measured
# on a live app with the slider at 1000 and a long playlist: 988 threads and
# 7.3 GB of private bytes after 40 minutes, almost all of it thread stacks.
# Past a couple of dozen parallel segment fetches there is nothing to win — the
# link is already saturated and CDNs start rate-limiting the burst — so the
# setting stays useful for tuning down without being able to melt the process.
MAX_PARALLEL = 24

# How a live stream's task ends. Its playlist is a window that slides: the
# oldest segment drops off the front as each new one is added, and there is no
# #EXT-X-ENDLIST because the stream has no end. Downloading it saved the few
# seconds on offer at that moment and called them Completed. Short enough to
# read whole in the failure toast, which shows the first 60 characters.
LIVE_STREAM = "Live stream: it has no end, so there is no finished video."
# Longest wait (s) before the second look at a playlist that may be live. The
# wait is one target duration; the cap stops a server that claims an hour-long
# one from stalling the start for an hour.
LIVE_WAIT_MAX = 10.0


def is_hls(url="", filename="", ctype=""):
    u = (url or "").split("?")[0].lower()
    f = (filename or "").lower()
    c = (ctype or "").lower()
    return (u.endswith(".m3u8") or f.endswith(".m3u8")
            or "mpegurl" in c)


def _playlist_facts(text):
    """(seconds, live) of a media playlist, from this one copy of it.

    Live: no end tag, no #EXT-X-PLAYLIST-TYPE, and a first segment numbered
    above 0 - segments have already dropped off the front of a sliding window.
    HlsDownloader starts from the same signs and fetches a second time before
    it refuses a stream; this is for /probe, which stays one fetch."""
    seconds = 0.0
    for m in re.finditer(r"#EXTINF:\s*([\d.]+)", text):
        try:
            seconds += float(m.group(1))
        except ValueError:
            pass
    live = (not re.search(r"^[ \t]*#EXT-X-ENDLIST", text, re.M)
            and not re.search(r"^[ \t]*#EXT-X-PLAYLIST-TYPE:", text, re.M)
            and _media_sequence(text) > 0)
    return seconds, live


def probe_variants(url, headers=None):
    """An HLS master's quality variants, best first: ``[{label, height,
    bandwidth, url, size}]``. ``[]`` for a single-quality media playlist
    (nothing to choose) or on any fetch error. See probe()."""
    return probe(url, headers)["variants"]


# A key this engine cannot use: anything but AES-128 (FairPlay, PlayReady,
# Widevine, SAMPLE-AES) - _decrypt stops with "unsupported HLS encryption".
_LOCKED = re.compile(r"#EXT-X-(?:SESSION-)?KEY:[^\r\n]*METHOD=(?!NONE\b|AES-128\b)", re.I)


def probe(url, headers=None):
    """What the extension's /probe says of a playlist::

        {"variants": [...], "duration": seconds, "live": bool}

    variants is probe_variants' list. duration and live are those of the best
    rendition (of the playlist itself when it is a media playlist): the page
    uses them to tell its player's own stream from an ad's live one, or from
    another video's. All three are empty, 0 and False when nothing can be read.

    Runs in the app, so it has the real Referer/cookies/UA and no CORS — it
    works on the referer/auth-gated CDNs the browser extension's own fetch
    can't read."""
    base = {**HEADERS, **(headers or {})}
    asked = url
    sess = Session()
    try:
        r = _get(sess, url, base, origin=url)
        # its links are relative to where it came from (HlsDownloader._fetch)
        text, url = _playlist_text(r), r.url
    except requests.RequestException:
        return {"variants": [], "duration": 0.0, "live": False}
    if "#EXT-X-STREAM-INF" not in text:
        # media playlist — single quality
        seconds, live = _playlist_facts(text)
        return _locked({"variants": [], "duration": seconds, "live": live}, text)

    variants = _variants(text, url)

    # estimate sizes from the top variant's total duration (one extra fetch)
    duration, live, best = 0.0, False, ""
    if variants:
        try:
            best = _playlist_text(_get(sess, variants[0]["url"], base, origin=asked))
            duration, live = _playlist_facts(best)
        except requests.RequestException:
            duration, live = 0.0, False

    out = []
    for v in variants:
        if v["height"]:
            label = f"{v['height']}p"
        elif v["bandwidth"]:
            label = f"{round(v['bandwidth'] / 1000)} kbps"
        else:
            label = "variant"
        size = int(v["bandwidth"] / 8 * duration) if (duration and v["bandwidth"]) else 0
        out.append({"label": label, "height": v["height"],
                    "bandwidth": v["bandwidth"], "url": v["url"], "size": size})
    # a master is judged by its best rendition (and its own session keys)
    return _locked({"variants": out, "duration": duration, "live": live}, text + best)


def _variants(text, url):
    """A master playlist's qualities, best first: [{height, bandwidth, url}],
    its links joined against `url`, where it came from."""
    lines = text.splitlines()
    variants = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        attrs = line.split(":", 1)[-1]
        # anchor to a delimiter so AVERAGE-BANDWIDTH= can't shadow BANDWIDTH=
        bw = re.search(r"(?:^|,)\s*BANDWIDTH=(\d+)", attrs, re.I)
        res = re.search(r"(?:^|,)\s*RESOLUTION=\d+x(\d+)", attrs, re.I)
        uri = ""
        for j in range(i + 1, len(lines)):
            cand = lines[j].strip()
            if cand and not cand.startswith("#"):
                uri = cand
                break
        if not uri:
            continue
        variants.append({
            "height": int(res.group(1)) if res else 0,
            "bandwidth": int(bw.group(1)) if bw else 0,
            "url": urllib.parse.urljoin(url, uri),
        })
    variants.sort(key=lambda v: (v["height"], v["bandwidth"]), reverse=True)
    return variants


def _locked(found, text):
    """`found` with "locked": True when `text` is keyed so this engine cannot
    decrypt it; the key is left out otherwise, so the answer is what it was."""
    if _LOCKED.search(text):
        found["locked"] = True
    return found


def _playlist_text(r):
    """A playlist's text: UTF-8 (RFC 8216 4.1, and what yt-dlp reads) unless
    the server names a charset. requests reads text/* with none as ISO-8859-1,
    where a UTF-8 name's byte 0x85 is a line break to splitlines(): the default
    sound's tag, cut in two, read as no default (2026-11-11)."""
    if "charset=" not in (r.headers.get("Content-Type") or "").lower():
        r.encoding = "utf-8"
    return r.text


def _scoped(headers, origin, url):
    """`headers` for a request to `url`: the browser's Cookie only on the site
    of `origin` - the link the browser took the cookies for - and never from
    https down to http. A CDN a playlist names got the site's cookies (item
    36, 2026-11-12). The site: the host, the hosts under it, and those under
    the domain above it when that has two labels or more - the browser hands
    over a cookie of .example.com for www.example.com and sends it to
    cdn.example.com too (CloudFront's signed cookies work that way). An
    address is its own site only. Not the public suffix list: a stream on
    a.co.uk still sends them to b.co.uk."""
    if origin is None or not any(k.lower() == "cookie" for k in headers):
        return headers
    a, b = urllib.parse.urlsplit(origin), urllib.parse.urlsplit(url)
    ah, bh = a.hostname or "", b.hostname or ""
    parent = ah.partition(".")[2]
    named = not re.fullmatch(r"[\d.]+|.*:.*", ah)          # not an IP address
    same_site = bh == ah or (named and (bh.endswith("." + ah) or (
        parent.count(".") >= 1 and bh.endswith("." + parent))))
    if same_site and (a.scheme, b.scheme) != ("https", "http"):
        return headers
    return {k: v for k, v in headers.items() if k.lower() != "cookie"}


class Stopped(RuntimeError):
    """A pause or a cancel ended a fetch (`_get`'s `stop`)."""


def _get(session, url, headers, stats=None, origin=None, stop=None, **kw):
    """GET with retries. When a `stats` dict is passed, tally retry attempts and
    the 403 / timeout breakdown for the end-of-download HLS log summary.
    `origin` is the link the browser's cookies came with: they go to its host
    only (`_scoped`). With `stop`, the body is read here 8 KB at a time and a
    stop() that turns true ends the fetch (Stopped): a pause waited for every
    piece in flight to come whole - up to a minute for one that stalled
    (audit, 2026-11-24)."""
    headers = _scoped(headers, origin, url)
    last = None
    for _ in range(SEG_RETRIES):
        if stop is not None and stop():
            raise Stopped("stopped")
        try:
            r = session.get(url, headers=headers, timeout=TIMEOUT, stream=stop is not None,
                            verify=utils.VERIFY_TLS, proxies=utils.PROXIES, allow_redirects=True, **kw)
            r.raise_for_status()
            if stop is not None:
                body = bytearray()
                for chunk in r.iter_content(8192):
                    if stop():
                        r.close()
                        raise Stopped("stopped")
                    body += chunk
                r._content = bytes(body)        # what r.content gives, as requests keeps it
            return r
        except requests.RequestException as e:
            last = e
            resp = getattr(e, "response", None)
            if stats is not None:
                stats["retries"] += 1
                if isinstance(e, requests.Timeout):
                    stats["timeouts"] += 1
                if resp is not None and resp.status_code == 403:
                    stats["http403"] += 1
            # client errors (404/403/410…) won't succeed on retry — fail fast
            if resp is not None and 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                raise
            time.sleep(1)
    raise last


def _byte_range(text, after):
    """(start, length) from a playlist's "<length>[@<start>]"; with no start,
    `after` - where the previous range of the same file ended."""
    length, _, start = text.strip().strip('"').partition("@")
    try:
        return (int(start) if start else after), int(length)
    except ValueError:
        raise RuntimeError("the playlist gives a byte range that cannot be read: %r" % text)


def _media_sequence(text):
    """The number of a media playlist's first segment: its
    #EXT-X-MEDIA-SEQUENCE, or 0 without one (RFC 8216 4.3.3.2)."""
    m = re.search(r"^[ \t]*#EXT-X-MEDIA-SEQUENCE:[ \t]*(\d+)", text, re.M)
    return int(m.group(1)) if m else 0


def ffmpeg_path():
    """ffmpeg - the app's own (bin/) or one on PATH - or None."""
    import yt_dl
    folder = yt_dl._ffmpeg_dir()
    for name in ("ffmpeg.exe", "ffmpeg"):
        if folder and os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
    return None


def remux(src, dst, seconds=0.0, should_stop=lambda: False):
    """Rewrite a joined HLS download, `src`, as an ordinary MP4 at `dst`: the
    same video and audio, copied, with the index a player seeks by. True when
    `dst` is whole. False, and no `dst` left, when there is no ffmpeg, it
    failed, what came out is shorter than `seconds` (the playlist's length;
    0: unknown), or `should_stop()` said so - a pause or a cancel, asked five
    times a second.

    What is joined plays, but a player cannot seek in it. Measured in VLC
    3.0.24 on its Direct3D output (2026-10-10), frames shown in the first,
    second and third second after each +5 s skip:
      an ordinary MP4                      45 / 30 / 30
      joined fMP4 fragments, no index      25 /  0 /  0-27
      the same video after this rewrite    46 / 30 / 30
      a joined transport stream            18 /  0 /  0-19
    VLC jumps to the start of a four-second fragment in the one and has no
    index at all in the other; the picture stands still until it catches up.
    The rewrite took 1 s for 110 MB. Only video and audio are taken: a
    transport stream's timed metadata has no place in an MP4.
    """
    exe = ffmpeg_path()
    if not exe or should_stop():
        return False
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-map", "0:v?", "-map", "0:a?", "-c", "copy", "-f", "mp4",
           "-progress", "pipe:1", "-nostats", dst]
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.warning("ffmpeg would not start: %s", e)
        return False
    reached, said = [0], []

    def progress():
        for line in p.stdout:
            # both count microseconds, whatever the second one is called
            if line.startswith((b"out_time_us=", b"out_time_ms=")):
                try:
                    reached[0] = max(reached[0], int(line.split(b"=", 1)[1]))
                except ValueError:
                    pass                    # "N/A", before the first packet

    def errors():
        said.append(p.stderr.read()[-600:].decode("utf-8", "replace").strip())

    readers = [threading.Thread(target=f, daemon=True) for f in (progress, errors)]
    for r in readers:
        r.start()
    stopped = False
    while True:
        try:
            p.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if should_stop():
                stopped = True
                p.kill()
                p.wait()
                break
    for r in readers:
        r.join(5)
    stopped = stopped or should_stop()
    got = reached[0] / 1e6
    ok = (not stopped and p.returncode == 0 and os.path.isfile(dst)
          and os.path.getsize(dst) > 0 and (seconds <= 0 or got >= 0.9 * seconds))
    if not ok:
        try:
            os.remove(dst)
        except OSError:
            pass
        if not stopped:
            err = (said or [""])[0]
            for full in (src, dst):
                if full:
                    err = err.replace(full, os.path.basename(full))
                    err = err.replace(full.replace("\\", "/"), os.path.basename(full))
            summary = " | ".join(dict.fromkeys(
                line.strip() for line in err.splitlines() if line.strip()))[-240:]
            log.warning("ffmpeg did not rewrite %s as an MP4 (exit %s, %.0f of %.0f s): %s",
                        os.path.basename(src), p.returncode, got, seconds,
                        summary or "no output")
    return ok


class HlsDownloader:
    def __init__(self, dtask: "T.DownloadTask"):
        self.t = dtask
        self.headers = {**HEADERS, **(getattr(dtask, "headers", None) or {})}
        self._origin = dtask.url        # the link the browser's cookies are for
        self.session = Session()       # used for playlist/key fetches
        self._tls = threading.local()            # per-thread session for segments
        # retry / failure tally for the end-of-download log summary
        self._stats = {"retries": 0, "timeouts": 0, "http403": 0}
        # fMP4 (CMAF) playlists name an init segment with #EXT-X-MAP; set by
        # _parse_media, None for a classic MPEG-TS playlist.
        self._init_map = self._init_range = None
        # the master the quality was picked from (text, its address, the
        # quality), its other qualities once asked for, and how many
        # segments came from them (_from_other_quality)
        self._ladder = None
        self._others = None
        self._swapped = 0

    def _seg_session(self):
        s = getattr(self._tls, "s", None)
        if s is None:
            s = Session()
            self._tls.s = s
        return s

    # ----------------------------------------------------------- playlist parse
    def _fetch(self, url):
        """A playlist's text and the address it came from after redirects:
        its links are relative to that one, as a player reads them. Joined
        against the address asked, a moved playlist's segments were 404s and
        a moved master named no quality the browser picked (2026-11-12)."""
        r = _get(self.session, url, self.headers, origin=self._origin)
        return _playlist_text(r), r.url

    def _fetch_text(self, url):
        return self._fetch(url)[0]

    def _is_master(self, text):
        return "#EXT-X-STREAM-INF" in text

    def _pick_variant(self, text, base_url):
        """From a master playlist choose the highest-bandwidth variant URL."""
        best_bw, best_url = -1, None
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                bw = 0
                for attr in line.split(":", 1)[-1].split(","):
                    if attr.strip().upper().startswith("BANDWIDTH="):
                        try:
                            bw = int(attr.split("=", 1)[1])
                        except ValueError:
                            bw = 0
                # the next non-comment line is the variant URI
                for j in range(i + 1, len(lines)):
                    cand = lines[j].strip()
                    if cand and not cand.startswith("#"):
                        if bw > best_bw:
                            best_bw, best_url = bw, urllib.parse.urljoin(base_url, cand)
                        break
        return best_url

    def _sound_apart(self, master, master_url, variant_url):
        """(height of `variant_url`'s picture, the sound a player plays with
        it) when the master playlist `master` gives its sound a playlist of
        its own - the #EXT-X-MEDIA TYPE=AUDIO a player plays in the variant's
        AUDIO group (DEFAULT=YES, else the group's first; its attributes) has
        a URI: then the variant's segments hold no sound. None when they do -
        a default with no URI is sound in the variant (RFC 8216 4.3.4.1.1),
        other languages beside it are not missed sound - or when the variant
        is not in that master (height 0: apart, no height given)."""
        lines = [line.strip() for line in master.splitlines()]
        played = {}               # AUDIO group -> the rendition a player plays
        for line in lines:
            if line.startswith("#EXT-X-MEDIA:"):
                a = self._attrs(line)
                if a.get("TYPE", "").upper() != "AUDIO":
                    continue
                gid = a.get("GROUP-ID", "")
                default = a.get("DEFAULT", "").upper() == "YES"
                if gid not in played or (default and
                                         played[gid].get("DEFAULT", "").upper() != "YES"):
                    played[gid] = a
        groups = {g for g, a in played.items() if a.get("URI")}
        if not groups:
            return None
        same = lambda u: urllib.parse.unquote(u) == urllib.parse.unquote(variant_url)
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            uri = next((x for x in lines[i + 1:] if x and not x.startswith("#")), "")
            if not uri or not same(urllib.parse.urljoin(master_url, uri)):
                continue
            a = self._attrs(line)
            if a.get("AUDIO") not in groups:
                return None
            m = re.match(r"\d+x(\d+)", a.get("RESOLUTION", ""))
            return (int(m.group(1)) if m else 0), played[a["AUDIO"]]
        return None

    def _to_ytdlp(self, master_url, height, sound):
        """A stream whose sound is a playlist of its own, to yt-dlp, which
        fetches that too and joins the two (ffmpeg): this engine fetched the
        picture alone and finished a video with no sound (2026-11-03). The
        master, the height picked and the sound a player plays with it are
        what it is given; the name stays the page's (name_chosen), not
        yt-dlp's "master"."""
        import yt_dl
        log.info("HLS %s: its sound is a stream of its own; handing it to yt-dlp",
                 self.t.filename)
        self.t.log_event("Its sound is a stream of its own: yt-dlp fetches both and joins them")
        self.t.hls_variant = self.t.url         # the quality picked (item 7)
        self.t.url = master_url
        self.t.use_ytdlp = True                 # a resume goes straight to yt-dlp
        # That sound by its NAME in any group - yt-dlp adds a group's rank,
        # ", low"/", high" - else its language, else yt-dlp's own "best
        # audio", which takes a rendition with no DEFAULT for one as good as
        # the default and breaks the tie by name: the Hindi sound of an
        # English default (2026-11-11). Its choice of group stays its own.
        # yt_dl._choosing_opts keeps them.
        h = "[height<=%d]" % height if height else ""
        quoted = lambda v: v.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        picks = []
        if sound.get("NAME") and "\0" not in sound["NAME"]:    # NUL: refused by tokenize (3.12+)
            picks.append('ba[format_note~="^%s(?:, (?:low|high))?$"]'
                         % quoted(re.escape(sound["NAME"])))
        if sound.get("LANGUAGE") and "\0" not in sound["LANGUAGE"]:
            picks.append('ba[language="%s"]' % quoted(sound["LANGUAGE"]))
        self.t.yt_format = "/".join("bv*%s+%s" % (h, p) for p in picks + ["ba"]) + "/b" + h
        if not self.t.name_chosen:
            self.t.name_chosen = yt_dl.stem(os.path.basename(self.t.save_path))
        self.t.seg_total = self.t.seg_done = 0
        self.t.total_size = self.t.downloaded = 0
        yt_dl.YtDlpDownloader(self.t).run()

    def _parse_media(self, text, base_url):
        """Return (segments, endlist). segments = [(url, seq, key, part)]:
        part is (start, length) for a segment that is a byte range of its
        file (#EXT-X-BYTERANGE), else None. Such a playlist names one file on
        every line, and each line was fetched whole: the video saved once a
        segment (2026-11-01).
        endlist = True only if the playlist contains #EXT-X-ENDLIST — i.e. it is
        finite VOD. Sliding-window live/event playlists return endlist=False so
        the caller can refuse a partial-byte resume (their segment URIs change
        between fetches even when the segment count doesn't, which would silently
        concatenate bytes from a different point in the stream)."""
        segments = []
        key = None  # {"method","uri","iv"}
        seq = 0
        endlist = False
        part = None      # the next segment's #EXT-X-BYTERANGE, as written
        ends = {}        # file -> where the last range of it ended
        self._init_map = self._init_range = None
        for line in text.splitlines():
            line = line.strip()
            if line == "#EXT-X-ENDLIST":
                endlist = True
            elif line.startswith("#EXT-X-MAP"):
                attrs = self._attrs(line)
                uri = attrs.get("URI")
                if uri:
                    where = urllib.parse.urljoin(base_url, uri)
                    if self._init_map is None:
                        self._init_map = where
                        if attrs.get("BYTERANGE"):
                            self._init_range = _byte_range(attrs["BYTERANGE"], 0)
                            ends[where] = sum(self._init_range)
                    elif where != self._init_map:
                        # A second init mid-playlist (after a discontinuity) is
                        # rare; the file stays playable up to that point.
                        log.warning("HLS playlist switches init segment mid-stream; "
                                    "only the first is used")
            elif line.startswith("#EXT-X-MEDIA-SEQUENCE"):
                try:
                    seq = int(line.split(":", 1)[1])
                except ValueError:
                    seq = 0
            elif line.startswith("#EXT-X-KEY"):
                key = self._parse_key(line, base_url)
            elif line.startswith("#EXT-X-BYTERANGE:"):
                part = line.split(":", 1)[1]
            elif line and not line.startswith("#"):
                url = urllib.parse.urljoin(base_url, line)
                if part is not None:
                    # no "@offset": it goes on where the last range of the file ended
                    part = _byte_range(part, ends.get(url, 0))
                    ends[url] = sum(part)
                segments.append((url, seq, key, part))
                part = None
                seq += 1
        return segments, endlist

    @staticmethod
    def _seconds(text):
        """A media playlist's length: its #EXTINF durations, added up."""
        total = 0.0
        for line in text.splitlines():
            if line.startswith("#EXTINF:"):
                try:
                    total += float(line[8:].split(",")[0])
                except ValueError:
                    pass
        return total

    def _attrs(self, line):
        """A tag's ATTRIBUTE=value list as a dict, quotes stripped."""
        attrs = {}
        for part in self._split_attrs(line.split(":", 1)[1]):
            if "=" in part:
                k, v = part.split("=", 1)
                attrs[k.strip().upper()] = v.strip().strip('"')
        return attrs

    def _parse_key(self, line, base_url):
        attrs = {}
        body = line.split(":", 1)[1]
        # split on commas not inside quotes
        for part in self._split_attrs(body):
            if "=" in part:
                k, v = part.split("=", 1)
                attrs[k.strip().upper()] = v.strip().strip('"')
        method = attrs.get("METHOD", "NONE")
        if method == "NONE":
            return None
        uri = urllib.parse.urljoin(base_url, attrs.get("URI", ""))
        iv = attrs.get("IV")
        return {"method": method, "uri": uri, "iv": iv, "keybytes": None}

    @staticmethod
    def _split_attrs(s):
        out, cur, q = [], "", False
        for ch in s:
            if ch == '"':
                q = not q
            if ch == "," and not q:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    # ----------------------------------------------------------- live streams
    def _is_live_window(self, url, text):
        """True when the media playlist `text`, fetched from `url` and without
        #EXT-X-ENDLIST, is a live stream's sliding window.

        One marked VOD or EVENT keeps everything from its start, and one whose
        first segment is numbered 0 has never dropped a segment: neither is
        looked at twice. Anything else looks, on one fetch, just like VOD from
        a server that leaves the end tag out. So it is fetched again one target
        duration later, the soonest a client may reload (RFC 8216 6.3.4), and
        is live if the number of its first segment has moved on, which a live
        server must raise for each segment it drops. If the playlist has not
        changed at all, a cache may have served the old copy; the RFC's retry
        half a target duration later settles it."""
        if re.search(r"^[ \t]*#EXT-X-PLAYLIST-TYPE:", text, re.M):
            return False
        first = _media_sequence(text)
        if first <= 0:
            return False
        m = re.search(r"^[ \t]*#EXT-X-TARGETDURATION:[ \t]*(\d+(?:\.\d+)?)", text, re.M)
        target = float(m.group(1)) if m else 0
        # the spec requires the tag; without one, Apple's recommended 6 s
        wait = min(target if target > 0 else 6.0, LIVE_WAIT_MAX)
        host = urllib.parse.urlparse(url).netloc
        for delay in (wait, wait / 2):
            if not self._wait(delay):
                return False                  # paused or cancelled; run() acts on it
            try:
                again = self._fetch_text(url)
            except requests.RequestException as e:
                log.info("HLS host=%s: second look failed (%s); downloading",
                         host, type(e).__name__)
                return False
            now = _media_sequence(again)
            if now > first:
                log.info("HLS live stream host=%s: first segment %d -> %d; "
                         "nothing downloaded", host, first, now)
                return True
            if again != text:
                break                         # changed, but has not moved on
        log.info("HLS host=%s: no end tag, but the playlist did not move on; "
                 "downloading", host)
        return False

    def _wait(self, secs):
        """Sleep `secs`, waking at once for a pause or a cancel (a cancel sets
        pause too). False when one of them cut the wait short."""
        end = time.monotonic() + secs
        while not self.t.pause_requested:
            left = end - time.monotonic()
            if left <= 0:
                return True
            time.sleep(min(left, 0.2))
        return False

    # ----------------------------------------------------------- decryption
    def _decrypt(self, data, key, seq):
        if not key or key["method"] != "AES-128":
            if key and key["method"] not in ("NONE", "AES-128"):
                raise RuntimeError(f"unsupported HLS encryption: {key['method']}")
            return data
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        self._load_key(key)
        if key.get("iv"):
            iv_hex = key["iv"][2:] if key["iv"].lower().startswith("0x") else key["iv"]
            iv = bytes.fromhex(iv_hex.rjust(32, "0"))
        else:
            iv = struct.pack(">QQ", 0, seq)  # default IV = media sequence number
        dec = Cipher(algorithms.AES(key["keybytes"]), modes.CBC(iv)).decryptor()
        out = dec.update(data) + dec.finalize()
        # strip PKCS7 padding
        if out:
            pad = out[-1]
            if 0 < pad <= 16:
                out = out[:-pad]
        return out

    # ----------------------------------------------------------- segments
    def _prefetch_keys(self, segments):
        """Resolve every distinct AES key once, before parallel downloading."""
        seen = set()
        for _, _, key, _ in segments:
            if not key or key["method"] != "AES-128":
                if key and key["method"] not in ("NONE", "AES-128"):
                    raise RuntimeError(f"unsupported HLS encryption: {key['method']}")
                continue
            if id(key) in seen:
                continue
            seen.add(id(key))
            self._load_key(key)

    def _load_key(self, key):
        """key's 16 bytes, fetched the first time. An expired key link often
        answers 200 with a web page, and AES raised ValueError on it - which
        nothing caught: "Download ended unexpectedly"."""
        if key.get("keybytes") is None:
            data = _get(self.session, key["uri"], self.headers, origin=self._origin).content
            if len(data) != 16:
                raise RuntimeError(f"the stream's key link sent no key ({len(data)} bytes)"
                                   " - its link may have expired")
            key["keybytes"] = data

    def _fetch_segment(self, url, seq, key, part=None):
        """Download + decrypt one segment (runs on a worker thread); `part`
        = (start, length) of its file, for a byte-range playlist. HTTP lets a
        server answer a range with less than asked: the rest is asked for
        again, as the HTTP engine does - a short part was saved as it came,
        and the video finished "Completed" broken (2026-11-04)."""
        stop = lambda: self.t.pause_requested or self.t.cancel_requested
        if not part:
            r = _get(self._seg_session(), url, self.headers, self._stats,
                     origin=self._origin, stop=stop)
            raw_seg = r.content
            utils.global_limiter.wait(len(raw_seg), stop)
            self.t._limiter.wait(len(raw_seg), stop)
            return self._decrypt(raw_seg, key, seq)
        start, length = part
        data, empty = b"", 0
        # a short answer is asked again, but not a byte at a time: one
        # answer for each 8 KB of the part, and 64 more (item 11, 2026-11-12)
        answers = 64 + length // 8192
        while len(data) < length:
            answers -= 1
            if answers < 0:
                raise RuntimeError("the server sent a segment in too many small "
                                   "pieces (%d of %d bytes)" % (len(data), length))
            at = start + len(data)
            r = _get(self._seg_session(), url,
                     dict(self.headers, Range="bytes=%d-%d" % (at, start + length - 1)),
                     self._stats, origin=self._origin, stop=stop)
            if r.status_code != 206:
                # the whole file, again for every segment: refused, not saved
                raise RuntimeError("the server sent the whole file, not the part of "
                                   "it the playlist names (HTTP %d)" % r.status_code)
            m = re.match(r"\s*bytes\s+(\d+)-", r.headers.get("Content-Range", ""))
            if m and int(m.group(1)) != at:
                raise RuntimeError("the server sent another part of the file than "
                                   "the playlist names")
            got = r.content[:length - len(data)]
            if not got:
                empty += 1
                if empty >= SEG_RETRIES:
                    raise RuntimeError("the server sent %d of the %d bytes of a "
                                       "segment" % (len(data), length))
            else:
                utils.global_limiter.wait(len(got), stop)
                self.t._limiter.wait(len(got), stop)
            data += got
        return self._decrypt(data, key, seq)

    def _from_other_quality(self, index, total, seq):
        """(label, bytes) of segment `index` from another quality of the
        master it was picked from, or None; it must carry the same number
        (`seq`, #EXT-X-MEDIA-SEQUENCE on). A site's storage may refuse
        single files - 1-2 in 100 at each quality, not the same ones (403
        BucketPunish, 2026-11-24) - and every other quality had the piece:
        it is taken from the nearest one below the picked one, else the
        nearest above. Only a quality cut into the same number of segments,
        and transport streams only: an fMP4 fragment needs its own quality's
        init section."""
        if not self._ladder or self._init_map:
            return None
        if self._others is None:
            master, base, picked = self._ladder
            same = lambda u: urllib.parse.unquote(u) == urllib.parse.unquote(picked)  # noqa: E731
            found = _variants(master, base)
            mine = next((i for i, v in enumerate(found) if same(v["url"])), -1)
            # best first: below = after it, nearest first; above = before it, reversed
            order = found[mine + 1:] + found[:mine][::-1] if mine >= 0 else found
            self._others = [[v, None] for v in order
                            if self._sound_apart(master, base, v["url"]) is None]
        for other in self._others:
            v, segs = other
            if segs is None:
                segs = other[1] = self._segments_of(v["url"], total)
            if not segs or segs[index][1] != seq:
                continue
            try:
                data = self._fetch_segment(*segs[index])
            except (requests.RequestException, RuntimeError):
                continue
            label = "%dp" % v["height"] if v["height"] else "%d kbps" % round(v["bandwidth"] / 1000)
            return label, data
        return None

    def _segments_of(self, url, total):
        """Another quality's segments, or [] when they cannot stand in for
        this one's: it will not come, it is fMP4, or it is cut otherwise."""
        try:
            text, base = self._fetch(url)
        except requests.RequestException:
            return []
        mine = self._init_map, self._init_range
        try:
            segs, _ = self._parse_media(text, base)
            fmp4 = self._init_map is not None
        except RuntimeError:
            return []
        finally:
            self._init_map, self._init_range = mine
        return segs if len(segs) == total and not fmp4 else []

    def _stopped(self, temp_path):
        """A cancel or a pause asked for: the status says so - and a cancel
        takes the temp file with it. True then."""
        if not (self.t.cancel_requested or self.t.pause_requested):
            return False
        if self.t.cancel_requested:
            self._rm(temp_path)
            self.t.status = T.CANCELLED
        else:
            self.t.status = T.PAUSED
        return True

    def _save_as_mp4(self, temp_path, seconds):
        """Save the joined download as an ordinary MP4 in its place (remux).
        True when that is the file now; False when it is still to be saved
        as it was joined - no ffmpeg, the rewrite failed, or a pause or
        cancel came (the temp file is untouched either way).

        ffmpeg writes beside the destination, so the move to another drive
        that a finished download needs anyway is this one pass. A ".ts" name
        becomes ".mp4" - not over somebody's file of that name - and any
        other name, the browser's as a rule, is kept.
        """
        # asked for in Settings -> Downloads, not done unasked
        if not utils.HLS_TO_MP4 or ffmpeg_path() is None:
            return False
        dest = self.t.save_path
        name = os.path.basename(dest)
        if name.lower().endswith(".ts"):
            dest = utils.unique_path(os.path.dirname(dest) or ".", name[:-3] + ".mp4")
        elif len(os.path.abspath(dest)) > 232:
            dest = utils.unique_path(os.path.dirname(dest) or ".", name)
        staged = "%s.%s.hfmove" % (dest, self.t.id[:12])
        self.t.finishing = True                 # the card: "Saving as MP4…"
        try:
            ok = remux(temp_path, staged, seconds,
                       lambda: self.t.pause_requested or self.t.cancel_requested)
            if ok:
                with utils._finish_lock:        # as finalize_download: never over a file
                    if os.path.exists(dest):
                        dest = utils.unique_path(os.path.dirname(dest) or ".",
                                                 os.path.basename(dest))
                    os.replace(staged, dest)
                size = os.path.getsize(dest)
        except OSError as e:
            log.warning("HLS: the MP4 could not be put in its place: %s", e)
            self._rm(staged)
            ok = False
        finally:
            self.t.finishing = False
        if not ok:
            if not (self.t.pause_requested or self.t.cancel_requested):
                self.t.log_event("Could not be rewritten as an ordinary MP4; "
                                 "saved as it was joined")
            return False
        self._rm(temp_path)
        self.t.save_path, self.t.filename = dest, os.path.basename(dest)
        self.t.total_size = self.t.downloaded = size
        self.t.log_event("Saved as an ordinary MP4, so a player can seek in it at once")
        return True

    # ----------------------------------------------------------- run
    def run(self):
        self.t.status = T.DOWNLOADING
        self.t.error = ""
        self.t.supports_range = False

        # output as .ts (concatenated transport stream)
        if self.t.save_path.lower().endswith(".m3u8"):
            self.t.save_path = self.t.save_path[:-5] + ".ts"
            self.t.filename = os.path.basename(self.t.save_path)
        os.makedirs(os.path.dirname(self.t.save_path) or ".", exist_ok=True)
        temp_path = utils.temp_download_path(self.t)

        # where the quality was picked, if known: as given (master_url), and
        # where it came from (master_base, which its links are joined against)
        master = master_url = master_base = ""
        picked = self.t.url          # the quality, by the address it was asked at
        try:
            text, base = self._fetch(self.t.url)
            if self._is_master(text):
                master, master_url, master_base = text, self.t.url, base
                variant = self._pick_variant(text, base)
                if not variant:
                    raise RuntimeError("no playable variant in master playlist")
                picked = variant
                text, base = self._fetch(variant)
            elif getattr(self.t, "hls_master", ""):
                # the browser picked this quality from it (task.hls_master)
                master_url = self.t.hls_master
                try:
                    master, master_base = self._fetch(master_url)
                except requests.RequestException as e:
                    log.info("HLS: the master playlist would not come (%s); the "
                             "stream as it was picked", type(e).__name__)
            segments, endlist = self._parse_media(text, base)
        except (requests.RequestException, RuntimeError) as e:
            self.t.status = T.ERROR
            self.t.error = f"HLS parse failed: {e}"
            return

        if not segments:
            self.t.status = T.ERROR
            self.t.error = "HLS playlist had no segments (live stream or DRM?)"
            return

        if not endlist and self._is_live_window(picked, text):
            self.t.status = T.ERROR
            self.t.error = LIVE_STREAM
            return

        apart = self._sound_apart(master, master_base, picked) if master else None
        if apart is not None:
            self._to_ytdlp(master_url, *apart)
            return
        if master:
            self._ladder = (master, master_base, picked)

        total = len(segments)
        seconds = self._seconds(text)

        # fMP4 (CMAF): the init segment (ftyp + moov) named by #EXT-X-MAP has
        # to lead the file, and what comes out is an MP4, not a transport
        # stream. Joined without it, the fragments play nowhere.
        if self._init_map and self.t.save_path.lower().endswith(".ts"):
            self.t.save_path = self.t.save_path[:-3] + ".mp4"
            self.t.filename = os.path.basename(self.t.save_path)

        # ---- resume: skip segments already in the .hfdownload file ----
        # Gates: (1) finite VOD (#EXT-X-ENDLIST) — a sliding-window live/event
        # playlist republishes the same segment COUNT but different content
        # (shifted MEDIA-SEQUENCE), so appending would silently corrupt the
        # output. (2) seg_total matches. (3) the file is at least as big as the
        # saved byte count — equality would race with the unlocked two-store
        # write order (seg_done then downloaded); >= is safe because we truncate
        # the temp file back to `self.t.downloaded` before reopening in append,
        # so any extra bytes from a partial trailing segment get re-fetched.
        can_resume = (endlist and self.t.seg_total == total
                      and self.t.seg_done > 0
                      and os.path.exists(temp_path)
                      and os.path.getsize(temp_path) >= self.t.downloaded > 0)
        if can_resume:
            done = self.t.seg_done
            downloaded = self.t.downloaded
            segments = segments[done:]
            # truncate any partial trailing segment bytes so append starts clean
            with open(temp_path, "r+b") as f:
                f.truncate(downloaded)
            open_mode = "ab"
        else:
            done = 0
            downloaded = 0
            open_mode = "wb"
        self.t.seg_total = total
        self.t.seg_done = done
        self.t.downloaded = downloaded

        # Pre-fetch distinct decryption keys once (avoids a race when several
        # segment threads would otherwise fetch the same key concurrently).
        try:
            self._prefetch_keys(segments)
        except (requests.RequestException, RuntimeError) as e:
            self.t.status = T.ERROR
            self.t.error = f"HLS key fetch failed: {e}"
            return

        # parallelism follows the app's Max-connections setting when configured
        # (Settings -> Network), else the built-in default.
        cap = utils.MAX_CONNECTIONS if utils.MAX_CONNECTIONS > 0 else PARALLEL
        workers = max(1, min(cap, MAX_PARALLEL, max(1, len(segments))))
        host = urllib.parse.urlparse(base).netloc
        log.info("HLS start host=%s segments=%d parallel=%d%s",
                 host, total, workers, " (resume)" if can_resume else "")
        seg_t0 = time.time()
        try:
            with open(temp_path, open_mode) as f, \
                    ThreadPoolExecutor(max_workers=workers) as ex:
                # A fresh file starts with the init segment; a resumed one
                # already has it, and its byte count includes it.
                if self._init_map and open_mode == "wb":
                    try:
                        init = self._fetch_segment(self._init_map, -1, None, self._init_range)
                    except (requests.RequestException, RuntimeError) as e:
                        self.t.status = T.ERROR
                        self.t.error = f"HLS init segment failed: {e}"
                        return
                    f.write(init)
                    f.flush()
                    downloaded += len(init)
                    self.t.downloaded = downloaded
                # fetch ahead, write in playlist order: up to two pieces a
                # connection in flight, the oldest written as soon as it is
                # whole and the next asked for at once. Batches waited for
                # their slowest piece with every other connection idle (audit,
                # 2026-11-24); memory stays bounded by `ahead` pieces.
                ahead = 2 * workers
                flight = deque()                # (segment, future), in order
                nxt = 0
                try:
                    while nxt < len(segments) or flight:
                        if self.t.cancel_requested:
                            break
                        if self.t.pause_requested:
                            self.t.status = T.PAUSED
                            return
                        while nxt < len(segments) and len(flight) < ahead:
                            flight.append((segments[nxt],
                                           ex.submit(self._fetch_segment, *segments[nxt])))
                            nxt += 1
                        seg, fut = flight.popleft()
                        err = other = None
                        try:
                            data = fut.result()
                        except (requests.RequestException, RuntimeError) as e:
                            err = e
                        if err is not None and (self.t.pause_requested
                                                or self.t.cancel_requested):
                            continue            # the loop's top says which
                        resp = getattr(err, "response", None)
                        if resp is not None and resp.status_code in (403, 404, 410):
                            # done = this segment's place in the whole playlist
                            other = self._from_other_quality(done, total, seg[1])
                        if other:
                            label, data = other
                            self._swapped += 1
                            log.info("HLS segment %d/%d refused (HTTP %d); taken from %s",
                                     done + 1, total, resp.status_code, label)
                            if self._swapped == 1:
                                self.t.log_event("The server refused a piece at this quality; "
                                                 "it was taken from another quality")
                        elif err is not None:
                            e = err
                            if resp is not None and resp.status_code == 403:
                                self.t.status = T.ERROR
                                self.t.error = "HTTP 403 Forbidden - URL expired"
                                log.warning("HLS 403 host=%s at segment %d/%d "
                                            "(retries=%d 403=%d timeouts=%d)",
                                            host, done + 1, total, self._stats["retries"],
                                            self._stats["http403"], self._stats["timeouts"])
                                return
                            self.t.status = T.ERROR
                            self.t.error = f"segment {done + 1}/{total} failed: {e}"
                            log.warning("HLS fail host=%s segment %d/%d: %s "
                                        "(retries=%d 403=%d timeouts=%d)",
                                        host, done + 1, total, e, self._stats["retries"],
                                        self._stats["http403"], self._stats["timeouts"])
                            return
                        f.write(data)
                        # flush BEFORE bumping seg_done so a sudden exit can never
                        # leave seg_done > durable bytes (same lesson as the byte
                        # downloader: a counted-but-unwritten segment would make
                        # resume skip past missing data).
                        f.flush()
                        downloaded += len(data)
                        done += 1
                        # Write downloaded BEFORE seg_done so an autosave that races
                        # into the gap snapshots {seg_done=N, downloaded≥segN_end}.
                        # The reverse order produced {seg_done=N+1, downloaded=old}
                        # and the resume gate's size check threw away progress.
                        self.t.downloaded = downloaded
                        self.t.seg_done = done
                        # estimate total size from average segment so far -> live %
                        self.t.total_size = int(downloaded / done * total)
                finally:
                    for _, waiting in flight:   # not started: not fetched at all
                        waiting.cancel()
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = utils.format_disk_error(e, temp_path)
            return

        if self._stopped(temp_path):
            return

        # An ordinary MP4 when ffmpeg is at hand (remux has why); without it,
        # or when the rewrite fails, what was joined - as it always was.
        if not self._save_as_mp4(temp_path, seconds):
            if self._stopped(temp_path):        # paused or cancelled meanwhile
                return
            # cross-volume-safe atomic finalize (shared helper). On failure mark
            # ERROR and keep the temp for retry instead of claiming COMPLETED
            # with no file.
            try:
                utils.saved_at(self.t, utils.finalize_download(temp_path, self.t.save_path))
            except OSError as e:
                self.t.status = T.ERROR
                self.t.error = f"finalize failed (pick another folder, then Resume): {e}"
                return
            self.t.total_size = downloaded
            self.t.downloaded = downloaded
        self.t.status = T.COMPLETED
        el = max(0.001, time.time() - seg_t0)
        log.info("HLS done host=%s segments=%d %.1fMB in %.1fs (%.2f MB/s) "
                 "retries=%d 403=%d timeouts=%d other-quality=%d", host, total,
                 downloaded / 1048576, el, (downloaded / el) / 1048576, self._stats["retries"],
                 self._stats["http403"], self._stats["timeouts"], self._swapped)
        if self._swapped:
            self.t.log_event("%d of %d pieces came from another quality" % (self._swapped, total))

    @staticmethod
    def _rm(path):
        utils.remove_temp(path)
