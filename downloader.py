"""Segmented HTTP downloader with pause / resume / cancel and resume-from-disk.

A ``Downloader`` is bound to one :class:`task.DownloadTask` and run synchronously
inside a worker thread by the queue. It updates the task's fields in place so the
GUI (polling on a timer) reflects live progress.
"""
import os
import time
import shutil
import logging
import collections
import tempfile
import threading
import urllib.parse

log = logging.getLogger("hyperfetch.downloader")

class ResizableSemaphore:
    def __init__(self, value):
        self.cond = threading.Condition()
        self.value = value
        self.active = 0
    def acquire(self, timeout=None):
        with self.cond:
            if timeout is None:
                while self.active >= self.value:
                    self.cond.wait()
            else:
                end = time.time() + timeout
                while self.active >= self.value:
                    rem = end - time.time()
                    if rem <= 0:
                        return False
                    self.cond.wait(rem)
            self.active += 1
            return True
    def release(self):
        with self.cond:
            self.active -= 1
            self.cond.notify()
    def set_limit(self, value):
        with self.cond:
            self.value = value
            self.cond.notify_all()


import requests
import urllib3
from requests.adapters import HTTPAdapter

import utils
import task as T

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _make_session(pool):
    """A keep-alive session whose connection pool is sized to the segment count.
    Reusing one session across a download's segments and retries avoids a fresh
    TCP+TLS handshake per request — the biggest single throughput win on HTTPS
    and high-latency links. urllib3's pools are thread-safe for our per-thread
    GETs; sizing the pool to the segment count avoids 'pool is full' churn that
    would silently discard (and not reuse) connections."""
    s = requests.Session()
    ad = HTTPAdapter(pool_connections=pool, pool_maxsize=pool, max_retries=0)
    s.mount("http://", ad)
    s.mount("https://", ad)
    return s

# Read size. Pause and cancel are checked between reads, and a read waits for
# all of its bytes. Measured at 128 KB/s per connection (an 8-way share of an
# 8 Mbit/s line): with 1 MiB reads a pause took 6.1 s, with 64 KiB 0.1-0.3 s.
# Over loopback, server in the same process, the smaller reads still moved
# 2.7 Gbit/s (1 MiB: 4.8).
CHUNK = 65536
DEFAULT_SEGMENTS = 8   # parallel connections when the server supports ranges
HEADERS = utils.DEFAULT_HEADERS
CONNECT_TIMEOUT = 15
MAX_RETRIES = 5        # per-segment attempts before the task errors
# A server pushing back on one connection too many (see _pushed_back): the
# refused range waits this long, then for a free slot at the gate; a refusal
# this soon after one of ours ended is taken as push-back too; and a
# connection of ours that ended this soon before a request went out may still
# count against it at the server. PUSHBACK_LAG stays under PUSHBACK_WAIT, so a
# range asking again after its wait is judged afresh.
PUSHBACK_WAIT = 0.25
PUSHBACK_WINDOW = 1.0
PUSHBACK_LAG = 0.1
# an extra link (task.mirrors) that hands back this many parts without having
# served a byte is dropped: it refuses us or is down, not merely busy
HANDED_BACK_MAX = 3
STAGGER = 0.01         # delay between segment thread starts (rate-limit friendly)

# Process-wide cap on concurrent segment connections across ALL downloads.
# Without it, N concurrent tasks each open `segments` sockets (N×8), and those
# 30-50 connections starve each other on one uplink — total throughput collapses
# to roughly a single connection's worth. Bounding the total keeps each live
# connection fed. 16 saturates a fast link without thrashing.
GLOBAL_MAX_CONNS = 64
_GLOBAL_CONNS = ResizableSemaphore(GLOBAL_MAX_CONNS)


def probe_info(url, headers=None):
    """Lightweight probe for the file-info dialog: size + content type."""
    info = {"size": 0, "type": ""}
    base = {**HEADERS, **(headers or {})}
    try:
        r = requests.head(url, headers=base, allow_redirects=True,
                          timeout=10, verify=utils.VERIFY_TLS, proxies=utils.PROXIES)
        info["size"] = int(r.headers.get("Content-Length", 0))
        info["type"] = r.headers.get("Content-Type", "").split(";")[0].strip()
    except (requests.RequestException, ValueError):
        pass

    # Some servers refuse/ignore HEAD (or rate-limit it) — fall back to a
    # 1-byte ranged GET and read the size from Content-Range.
    if info["size"] == 0:
        try:
            with requests.get(url, headers={**base, "Range": "bytes=0-0"},
                              stream=True, allow_redirects=True,
                              timeout=10, verify=utils.VERIFY_TLS, proxies=utils.PROXIES) as r:
                cr = r.headers.get("Content-Range", "")
                if "/" in cr:
                    total = cr.split("/")[-1]
                    if total.isdigit():
                        info["size"] = int(total)
                if info["size"] == 0 and r.status_code == 200:
                    info["size"] = int(r.headers.get("Content-Length", 0))
                if not info["type"]:
                    info["type"] = r.headers.get("Content-Type", "").split(";")[0].strip()
        except (requests.RequestException, ValueError):
            pass
    return info


def parts_for(total, segments):
    """How many parts a file of `total` bytes is fetched in; `segments` 0 = by its size."""
    if segments == 0:
        mb = total / (1024 * 1024)
        if mb < 10: return 1
        elif mb < 100: return 4
        elif mb < 1000: return 8
        elif mb < 5000: return 16
        return 32
    return min(segments, max(1, total // (1024 * 1024) or 1))


class MirrorMismatch(Exception):
    """A second link answered with something other than this file's bytes."""


def check_mirror(t, url):
    """None when `url` can serve this download's file in parts, else why not.

    Asked before a link is added (one ranged GET of one byte): it must take
    ranges and say the same total size as the download. Same size is not proof
    of the same bytes; the engine checks every answer's size again, and a
    .sha256 check (Settings) catches the rest."""
    if not url.lower().startswith(("http://", "https://")):
        return "Only http:// and https:// links can be added."
    if url == t.url or url in (getattr(t, "mirrors", None) or []):
        return "That link is already used by this download."
    if not (t.total_size > 0 and t.supports_range):
        # not started yet, or a server that cannot send parts: ask the first link
        info = probe_info(t.url, getattr(t, "headers", None))
        size = info["size"]
        if not size:
            return "The download's own link does not say how big the file is."
    else:
        size = t.total_size
    try:
        with requests.get(url, headers={**HEADERS, "Range": "bytes=0-0"}, stream=True,
                          allow_redirects=True, timeout=CONNECT_TIMEOUT,
                          verify=utils.VERIFY_TLS, proxies=utils.PROXIES) as r:
            total = r.headers.get("Content-Range", "").rpartition("/")[2]
            if r.status_code != 206:
                return f"That server cannot send parts of the file (HTTP {r.status_code})."
    except requests.RequestException as e:
        return f"That link did not answer: {e.__class__.__name__}."
    if not total.isdigit() or int(total) != size:
        return "That link's file is not the same size as this one: not the same file."
    return None


def discard_partial(t):
    """Forget a task's partial download: its temp file and its segments."""
    utils.remove_temp(utils.temp_download_path(t))
    t.temp_path = ""
    t.segments = []
    t.downloaded = 0


class Downloader:
    def __init__(self, dtask: "T.DownloadTask", segments=DEFAULT_SEGMENTS,
                 url=None, headers=None, cookies=None):
        """``url``, ``headers`` and ``cookies`` are for a video page's file
        (see run_media): the link yt-dlp found, the headers it would send
        there, and its cookie jar. By default requests go to the task's own
        URL with its own headers."""
        self.t = dtask
        self.url = url or dtask.url
        self.num_segments = max(0, segments)
        # global ceiling from Settings -> Network -> Max Connections (0 = off).
        # Only clamps an explicit count; Auto mode (0) is left untouched.
        if utils.MAX_CONNECTIONS > 0 and self.num_segments > 0:
            self.num_segments = min(self.num_segments, utils.MAX_CONNECTIONS)
        # per-host rule may force a segment count (some servers reject many
        # parallel connections) — still capped by the global Max Connections.
        _rule_segs = utils.host_rule(self.url).get("segments")
        if _rule_segs:
            self.num_segments = int(_rule_segs)
            if utils.MAX_CONNECTIONS > 0:
                self.num_segments = min(self.num_segments, utils.MAX_CONNECTIONS)
            log.debug("host rule: %s -> %d segments", self.url, self.num_segments)
        # browser-supplied headers (Cookie/Referer/UA) merged into every request
        if headers is None:
            headers = getattr(dtask, "headers", None) or {}
        self._base_headers = {**HEADERS, **headers}
        self._probe_ctype = ""
        self._media = False             # set by run_media
        # run_media's file is on disk whole. The task is not Completed on that:
        # yt-dlp still has the file (its post-processors), and the window acts
        # on Completed the moment it reads it - see run_media.
        self.media_done = False
        # adaptive connection gate: shrinks when the server answers 429.
        # Initialized to a safe non-zero placeholder; run() sets the real cap
        # from the ACTUAL segment count once segments exist — deriving it from
        # num_segments would be 0 in Auto mode and deadlock every worker.
        self._conn_cv = threading.Condition()
        self._active_conns = 0
        self._max_conns = max(1, self.num_segments)
        self._conn_cap = self._max_conns
        self._streaming = 0             # connections whose response was accepted
        self._link_streams = collections.Counter()  # the same, by link
        self._served = set()            # links that have streamed to us this run
        self._stream_ended = {}         # link -> when the last of its streams ended
        self._ends = collections.deque(maxlen=64)  # (link, when) of the latest ends
        self._link_ok = False           # the link has served us in this run
        self._dead = set()              # links dropped in this run
        self._to_own = set()            # segments an extra link was too busy for
        self._handed = collections.Counter()  # parts each extra link handed back
        # one keep-alive session for this download's probe + all segment threads.
        # Auto mode (num_segments==0) can fan out to 32 segments, so size for that.
        self._session = _make_session(max(8, self.num_segments or 32))
        if cookies is not None:
            # a jar, not a Cookie header: requests then sends each cookie only
            # to the hosts it belongs to, redirects included, as yt-dlp does
            self._session.cookies.update(cookies)

    # ------------------------------------------------------------ conn gate
    def _acquire_conn(self):
        # 1) per-task adaptive gate (shrinks to 1 on HTTP 429)
        with self._conn_cv:
            while self._active_conns >= self._max_conns:
                if self.t.pause_requested or self.t.cancel_requested:
                    return False
                self._conn_cv.wait(0.2)
            self._active_conns += 1
        # 2) process-wide gate across ALL downloads — prevents the N×segments
        #    socket pileup. Poll so pause/cancel stays responsive while waiting.
        while not _GLOBAL_CONNS.acquire(timeout=0.2):
            if self.t.pause_requested or self.t.cancel_requested:
                with self._conn_cv:                 # hand the per-task slot back
                    self._active_conns -= 1
                    self._conn_cv.notify_all()
                return False
        return True

    def _release_conn(self):
        _GLOBAL_CONNS.release()
        with self._conn_cv:
            self._active_conns -= 1
            self._conn_cv.notify_all()

    def _throttle_conns(self):
        """Server is pushing back (429 or connection resets): halve allowed
        parallel connections. _grow_conns() restores them on later successes."""
        with self._conn_cv:
            self._max_conns = max(1, self._max_conns // 2)

    def _grow_conns(self):
        """A stream connected successfully: allow one more parallel connection,
        up to the segment count (TCP-slow-start-style ramp; see run())."""
        with self._conn_cv:
            if self._max_conns < self._conn_cap:
                self._max_conns += 1
                self._conn_cv.notify_all()

    def _streams(self, n, src):
        with self._conn_cv:
            self._streaming += n
            self._link_streams[src] += n
            if n > 0:
                self._served.add(src)
                self._link_ok |= src == self.url
            else:
                self._stream_ended[src] = time.monotonic()
                self._ends.append((src, self._stream_ended[src]))

    def _pushed_back(self, sent, src):
        """A request was refused - 403, 429 or a reset - before it got going.
        `sent`: when it went out (time.monotonic()).

        While another of this download's connections is streaming, that is the
        server's limit and not a dead link: it is serving us, just not on one
        more connection. So is a refusal just after one of ours ended: a server
        that accounts for a connection a moment longer than we use it -
        connection tracking does - refuses the next one then. The gate drops
        to the connections the server was counting as ours (halving
        undershoots): those streaming, and those that ended just before the
        request went out (PUSHBACK_LAG) or while it was on its way. It grows
        back as streams are accepted, as always; no lasting ceiling, since one
        learnt at such a moment would be one too low for the rest.

        It dropped to the ones streaming alone before, and that was one too
        low: the refused range then waited for the gate, its every slot in
        use, while the server had room - and the gate grows only when a
        stream is accepted, so not before one ended. At that end the next
        range got in and the one after was refused, on the account of the
        one that had just ended, and the gate was one short again: the rest
        of the download came down one connection at a time. On the stand-in
        in tests/test_conn_limits.py (two connections, 6 MB, one connection
        4.0 s) that took 3.3 s, not 2.6: in 1 run of 180, and in 67 of 120
        with a 15.6 ms clock, as on Windows. Now in none of 300.

        False when nothing is streaming or just ended: then it is the link's
        answer. Only `src`'s own streams say so: while an extra link (task.mirrors)
        streams, an expired first link must still read as expired.

        Measured on a stand-in CDN serving two connections, before this:
        refused with a reset, halving and backing off took twice as long as
        one connection; refused with 403, the download failed outright.
        """
        with self._conn_cv:
            now = time.monotonic()
            if (self._link_streams[src] <= 0
                    and now - self._stream_ended.get(src, float("-inf")) >= PUSHBACK_WINDOW):
                return False
            held = self._streaming
            if now - sent < PUSHBACK_WINDOW:    # a timeout tells nothing of that
                held += sum(1 for s, t in self._ends if s == src and t >= sent - PUSHBACK_LAG)
            self._max_conns = max(1, min(self._max_conns, held))
            return True

    # ------------------------------------------------------------------ probe
    def _capture_response(self, r):
        """Record what the server actually answered, for the Headers tab.

        Only the first segment's response is kept — they are all the same
        request bar the Range header, so storing one per segment would just be
        noise. Set-Cookie is dropped: it is a credential, it is the one response
        header worth leaking, and this ends up on disk with the task.
        """
        if getattr(self.t, "response_headers", None):
            return
        try:
            hdrs = {k: v for k, v in r.headers.items()
                    if k.lower() not in ("set-cookie", "set-cookie2")}
            self.t.response_headers = hdrs
            self.t.response_status = r.status_code
            self.t.remote_address = ""
            raw = getattr(r, "raw", None)
            sock = getattr(getattr(raw, "_connection", None), "sock", None)
            if sock is not None:
                peer = sock.getpeername()
                self.t.remote_address = f"{peer[0]}:{peer[1]}"
        except Exception:
            pass                        # diagnostics must never break a download

    def _probe(self):
        """One request to learn total size + range support, following redirects."""
        try:
            r = self._session.head(self.url, headers=self._base_headers,
                              allow_redirects=True,
                              timeout=CONNECT_TIMEOUT, verify=utils.VERIFY_TLS, proxies=utils.PROXIES)
            size = int(r.headers.get("Content-Length", 0))
            accept = r.headers.get("Accept-Ranges", "none").lower()
            self._probe_ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        except requests.RequestException:
            size, accept = 0, "none"

        # Some servers lie on HEAD; confirm with a tiny ranged GET.
        if size == 0 or accept == "none":
            try:
                r = self._session.get(self.url,
                                 headers={**self._base_headers, "Range": "bytes=0-0"},
                                 stream=True, allow_redirects=True,
                                 timeout=CONNECT_TIMEOUT, verify=utils.VERIFY_TLS, proxies=utils.PROXIES)
                if r.status_code == 206:
                    accept = "bytes"
                    cr = r.headers.get("Content-Range", "")
                    if "/" in cr:
                        try:
                            size = int(cr.split("/")[-1])
                        except ValueError:
                            pass
                if size == 0:
                    size = int(r.headers.get("Content-Length", 0))
                ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if ctype:
                    self._probe_ctype = ctype
                r.close()
            except requests.RequestException:
                pass

        self.t.total_size = size
        self.t.supports_range = accept != "none" and size > 0

    def _web_page(self):
        """The server answered with a web page where a file was expected.

        yt-dlp gets one look first: a pasted video page from a site that
        yt_dl._SITES does not name lands here, and yt-dlp knows far more sites
        than that list. A video there hands the task to its engine, the same
        path a ticked "Use yt-dlp" takes; nothing there is the old error.

        Only a link that could be a video page is shown to yt-dlp: one whose
        name is a media name, or names no type at all (a page link is saved
        as "download.bin"). A name that says program, archive, document or
        picture asked for that file. A removed file often redirects to the
        vendor's home page, yt-dlp would find the promo clip on it, and
        "setup.exe" would finish as that clip - a wrong file, silently.
        """
        import yt_dl
        asked_for = utils.category_for(os.path.basename(self.t.save_path))
        found = False
        if asked_for in ("Video", "Music", "Other"):
            self.t.log_event("Got a web page, not the file — asking yt-dlp for a video on it")
            found = yt_dl.can_extract(self.t)
        if self.t.cancel_requested or self.t.pause_requested:
            self.t.status = T.CANCELLED if self.t.cancel_requested else T.PAUSED
            return
        if found:
            log.info("HTTP %s: web page with a video, handing to yt-dlp", self.t.filename)
            self.t.log_event("yt-dlp found a video on the page")
            self.t.use_ytdlp = True          # a resume goes straight to yt-dlp too
            self.t.total_size = 0            # the probe measured the page, not the video
            yt_dl.YtDlpDownloader(self.t, segments=self.num_segments).run()
            return
        self.t.status = T.ERROR
        self.t.error = ("Server sent a web page, not the file "
                        "(login/cookies required — use the browser extension)")

    # ------------------------------------------------------------- planning
    @staticmethod
    def _format_disk_error(exc, path):
        """Turn a bare OSError from the segment write into something actionable.
        Detects ENOSPC ("No space left on device") and reports the actual free
        bytes on the target volume so the user can pick a different drive in
        Settings and resume from the partial .hfdownload still on disk."""
        import errno
        if getattr(exc, "errno", None) == errno.ENOSPC:
            try:
                free = shutil.disk_usage(os.path.dirname(path) or ".").free
                free_mb = free >> 20
                return (f"disk full — {free_mb} MiB free. "
                        "Pick a different folder in Settings and Resume.")
            except OSError:
                return "disk full — pick a different folder in Settings and Resume."
        return f"disk error: {exc}"

    def _check_disk_space(self, path, needed):
        """Raise OSError if the target volume can't hold the file (+64 MiB slack)."""
        import shutil
        try:
            free = shutil.disk_usage(os.path.dirname(path) or ".").free
        except OSError:
            return  # can't tell — let the write try
        if free < needed + (64 << 20):
            raise OSError(
                f"not enough disk space: need {needed >> 20} MiB, "
                f"{free >> 20} MiB free")

    def _build_segments(self):
        """Create segments, pre-allocating the file if total size is known."""
        temp_path = utils.temp_download_path(self.t)

        if self.t.total_size > 0:
            self._check_disk_space(temp_path, self.t.total_size)
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) != self.t.total_size:
                # full length, nothing written: see utils.allocate_file
                utils.allocate_file(temp_path, self.t.total_size)

        if not self.t.supports_range or self.num_segments == 1:
            seg = T.Segment(0, 0, max(0, self.t.total_size - 1))
            self.t.segments = [seg]
            return

        n = parts_for(self.t.total_size, self.num_segments)

        part = self.t.total_size // n
        segs = []
        for i in range(n):
            start = i * part
            end = self.t.total_size - 1 if i == n - 1 else start + part - 1
            seg = T.Segment(i, start, end)
            segs.append(seg)
        self.t.segments = segs

    # ------------------------------------------------------------- workers
    def _source_for(self, seg):
        """The link this segment is fetched from: the extra links the user
        added (task.mirrors) take segments in turn with the download's own
        one. Read at every request, so a link added while it runs is used for
        the next part asked for, and a dropped one is passed over."""
        if self._media or not self.t.supports_range:
            return self.url
        own = self.url not in self._dead
        if own and seg.index in self._to_own:
            return self.url
        live = [self.url] * own + self._mirrors_left()
        return live[seg.index % len(live)] if live else self.url

    def _mirrors_left(self, but=None):
        return [m for m in getattr(self.t, "mirrors", None) or []
                if m not in self._dead and m != but]

    def _drop_mirror(self, url, why):
        if url not in self._dead:
            self._dead.add(url)
            host = urllib.parse.urlsplit(url).hostname or "?"
            self.t.log_event(f"Stopped using the link at {host}: {why}", level="WARNING")
            log.warning("mirror %s dropped for %s: %s", host, self.t.filename, why)

    def _worker(self, seg):
        """Stream one segment, writing directly to the pre-allocated .hfdownload file."""
        temp_path = utils.temp_download_path(self.t)
        attempts = 0
        refusals = 0                    # 403s after the link served us (below)

        while not self.t.pause_requested and not self.t.cancel_requested:
            if self.t.supports_range:
                if seg.complete:
                    return
                start = seg.start + seg.downloaded
                headers = {**self._base_headers, "Range": f"bytes={start}-{seg.end}"}
                mode = "r+b"
            else:
                headers = dict(self._base_headers)
                if self.t.total_size > 0:
                    mode = "r+b"
                else:
                    mode = "wb" if seg.downloaded == 0 else "ab"
                
                if not self.t.supports_range:
                    mode = "wb"
                    seg.downloaded = 0

            src = self._source_for(seg)
            if src != self.url:
                # the browser's cookies and referer are for the first link's site
                headers = {**HEADERS, "Range": headers["Range"]}
            if not self._acquire_conn():
                return  # paused while waiting for a connection slot
            retry_exc = None
            wait_attempt = attempts
            accepted = False
            sent = time.monotonic()
            try:
                # with-block guarantees the response/socket closes on every
                # path — incl. raise_for_status() failures and mid-stream errors
                with self._session.get(src, headers=headers, stream=True,
                                  allow_redirects=True, timeout=CONNECT_TIMEOUT,
                                  verify=utils.VERIFY_TLS, proxies=utils.PROXIES) as r:
                    r.raise_for_status()
                    if src != self.url:
                        # only the very bytes asked for, of a file this size
                        cr = r.headers.get("Content-Range", "")
                        if (r.status_code != 206 or not cr.startswith(f"bytes {start}-")
                                or cr.rpartition("/")[2] != str(self.t.total_size)):
                            raise MirrorMismatch(f"answered {r.status_code} {cr or 'without a range'}")
                    accepted = True
                    self._streams(+1, src)
                    try:
                        self._grow_conns()          # stream accepted -> ramp up one more slot
                        self._capture_response(r)   # for the drawer's Headers tab
                        with open(temp_path, mode) as f:
                            if mode == "r+b":
                                f.seek(seg.start + seg.downloaded)
                            for chunk in r.iter_content(CHUNK):
                                if self.t.pause_requested:
                                    return
                                if chunk:
                                    utils.global_limiter.wait(len(chunk))
                                    self.t._limiter.wait(len(chunk))
                                    f.write(chunk)
                                    # Flush to the OS BEFORE advancing the counter so a
                                    # later abrupt exit (daemon threads killed mid-write)
                                    # can never leave a counted-but-unwritten gap that
                                    # resume would wrongly treat as already-downloaded.
                                    f.flush()
                                    seg.downloaded += len(chunk)
                    finally:
                        self._streams(-1, src)
                return
            except (requests.RequestException, MirrorMismatch) as e:
                resp = getattr(e, "response", None)
                code = resp.status_code if resp is not None else None
                if src != self.url:
                    busy = not isinstance(e, MirrorMismatch) and code in (403, 429, None)
                    if busy and not self._link_streams[src] and src not in self._served:
                        self._handed[src] += 1
                    if self._handed[src] >= HANDED_BACK_MAX:
                        busy = False        # it never works: refusing us, or down
                    if busy and self.url not in self._dead:
                        # most likely the extra link's own cap on connections:
                        # this part goes to the first link, the extra one keeps
                        # the others
                        self._to_own.add(seg.index)
                        log.debug("seg %d: extra link busy (%s), back to the first link",
                                  seg.index, code or "reset")
                        continue
                    if not busy or self._mirrors_left(but=src):
                        # an extra link that fails is dropped, never the download:
                        # its part goes on from the next link at once
                        self._drop_mirror(src, e)
                        continue
                    # the only link left: retried below like the first one
                if not accepted and code in (403, 429, None) and self._pushed_back(sent, src):
                    # the server's limit, not the link's (see _pushed_back):
                    # the range waits a moment, then for a slot at the gate,
                    # and that is not one of its retries
                    log.debug("seg %d refused (%s) while others stream — gate now %d",
                              seg.index, code or "reset", self._max_conns)
                    retry_exc, wait_attempt = e, None
                elif code == 403 and self._link_ok and refusals < 2:
                    # The link served us moments ago, nothing of ours is
                    # streaming now: a server counting a connection until it
                    # closes refuses the next one as ours ends. Two short
                    # waits (2 s, 4 s) tell that from a link that has expired.
                    refusals += 1
                    retry_exc, wait_attempt = e, refusals
                # client errors won't succeed on retry — fail fast with an
                # actionable message (no wasted retry/backoff on a 404/410/403).
                elif code is not None and 400 <= code < 500 and code not in (408, 429):
                    if (src == self.url and code in (403, 410) and not self._media
                            and self.t.supports_range and self._mirrors_left()):
                        # the first link expired: the extra links serve the rest
                        self._drop_mirror(self.url, f"HTTP {code}")
                        continue
                    if not self.t.cancel_requested:
                        self.t.status = T.ERROR
                        if code in (403, 410) and self._media:
                            # a video page's link: a resume extracts a new one
                            self.t.error = (f"The video's link expired (HTTP {code}) "
                                            "— Resume fetches a new one")
                        elif code in (403, 410):
                            self.t.error = f"URL expired (HTTP {code}) — right-click → Refresh Address"
                        elif code in (401, 407):
                            self.t.error = f"Login required (HTTP {code}) — use the browser extension"
                        elif code == 404:
                            self.t.error = "Not found (HTTP 404) — the file may have moved"
                        else:
                            self.t.error = f"HTTP {code} — the server refused the download"
                        log.warning("HTTP %s seg %d: %s", code, seg.index, self.t.filename)
                    return
                else:
                    attempts += 1
                    if attempts > MAX_RETRIES:
                        if not self.t.cancel_requested:
                            self.t.status = T.ERROR
                            # transient (timeout / connection reset) — resumes from disk
                            self.t.error = "Connection lost — Resume to retry"
                            log.error("seg %d of %s gave up after %d retries: %s",
                                      seg.index, self.t.filename, MAX_RETRIES, e)
                        return
                    if code == 429:
                        self._throttle_conns()
                        log.warning("429 rate-limited: %s — halved to %d connections",
                                    self.t.filename, self._max_conns)
                    elif code is None:
                        # connection-level failure (reset/refused/timeout, no HTTP
                        # code): the server or path is rejecting our concurrency —
                        # back off the gate like a 429 so the surviving retries go
                        # out at a rate the server tolerates.
                        self._throttle_conns()
                        log.debug("seg %d conn failure — gate now %d: %s",
                                  seg.index, self._max_conns, e)
                    else:
                        log.debug("seg %d retry %d/%d: %s — %s",
                                  seg.index, attempts, MAX_RETRIES, self.t.filename, e)
                    retry_exc, wait_attempt = e, attempts
            except OSError as e:
                # disk full / file locked: not retryable, surface a useful
                # error with the actual free space + needed space so the user
                # can pick a different drive in Settings and resume.
                if not self.t.cancel_requested:
                    self.t.status = T.ERROR
                    self.t.error = self._format_disk_error(e, temp_path)
                return
            finally:
                # Release the slot BEFORE the retry backoff so other tasks can use
                # it during a Retry-After window. Holding the global semaphore for
                # up to 60 s of sleep would re-create the cross-task starvation the
                # global cap was added to prevent.
                self._release_conn()
            if retry_exc is not None:
                self._backoff_sleep(retry_exc, wait_attempt)

    @staticmethod
    def _retry_wait(exc, attempt):
        """Seconds to wait before a retry; honors Retry-After on 429.
        attempt None: a pushed-back range, which waits a moment, or as long
        as Retry-After asks, and leaves the rest to the gate."""
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code == 429:
            ra = resp.headers.get("Retry-After", "")
            if ra.isdigit():
                return min(int(ra), 60)
        if attempt is None:
            return PUSHBACK_WAIT
        return min(2 ** attempt, 30)        # 2, 4, 8, 16, 30

    def _backoff_sleep(self, exc, attempt):
        """Sleep in small steps so pause/cancel stays responsive.
        Polling cancel_requested too (not just pause) — otherwise a Cancel
        during a 60s Retry-After window would sit dormant until the sleep
        ended, instead of breaking out within ~200ms."""
        deadline = time.monotonic() + self._retry_wait(exc, attempt)
        while not self.t.pause_requested and not self.t.cancel_requested:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(0.2, left))

    # ------------------------------------------------------------- run
    def run(self):
        try:
            self._run()
        finally:
            try:
                self._session.close()
            except Exception:
                pass

    def _run(self):
        # magnet / .torrent -> aria2c sidecar engine, not an HTTP byte download
        import torrent
        if torrent.is_torrent_task(self.t.url, self.t.filename):
            torrent.TorrentDownloader(self.t).run()
            return

        # media pages (YouTube etc.) and DASH manifests -> yt-dlp; forced via the
        # New Download toggle or auto-detected by host/extension. Not a direct
        # file, so it can't be byte-split.
        import yt_dl
        if yt_dl.is_ytdlp_task(self.t, self._probe_ctype):
            yt_dl.YtDlpDownloader(self.t, segments=self.num_segments).run()
            return

        # HLS (.m3u8) playlists need segment fetch+concat, not a byte download
        import hls
        if hls.is_hls(self.t.url, self.t.filename, self._probe_ctype):
            hls.HlsDownloader(self.t).run()
            return

        self.t.status = T.DOWNLOADING
        self.t.error = ""
        os.makedirs(os.path.dirname(self.t.save_path) or ".", exist_ok=True)
        temp_path = utils.temp_download_path(self.t)

        try:
            if not self.t.segments:
                self._probe()
                # a web page where the file should be: a video page yt-dlp may
                # know, or an auth-gated host's (Google Drive etc.) login /
                # interstitial page — never write it out as the file. A name
                # ending .html/.htm asked for the page itself.
                ext = os.path.splitext(self.t.save_path)[1].lower()
                if (self._probe_ctype.startswith("text/html")
                        and ext not in ("", ".html", ".htm")):
                    self._web_page()
                    return
                self._build_segments()
            else:
                if self.t.total_size > 0 and not os.path.exists(temp_path):
                    for seg in self.t.segments:
                        seg.downloaded = 0
                    self._build_segments()
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = str(e)
            return
        self._transfer(temp_path, first=4)

    def run_media(self):
        """Fetch a video page's file in byte ranges.

        self.url is the link yt-dlp found on the page; t.url stays the page and
        t.save_path is the name yt-dlp gave the file. The link is new on every
        run - yt-dlp extracts again, because such links are signed, short-lived
        or tied to this address - so a partial on disk is continued only when
        the server describes the same file: the same size, and the same ETag
        when it gives one. Anything else starts over.

        Returns False, having written nothing, when the link cannot be fetched
        that way - no ranges, no size, a refusal, a web page, no answer - so
        the caller lets yt-dlp fetch it as before. True when it ran: media_done
        says the file is whole, otherwise t.status says how it ended.

        A file that is whole leaves the task Downloading. yt-dlp then runs its
        post-processors on it (a fixup through ffmpeg can take seconds), and
        only after that is it the finished video; YtDlpDownloader marks it
        Completed then. The window reads the status twice a second and on
        Completed moves the file to its category folder and records it. Told
        early, it moved the file from under yt-dlp.
        """
        self._media = True
        try:
            return self._run_media()
        finally:
            try:
                self._session.close()
            except Exception:
                pass

    def _run_media(self):
        temp_path = utils.temp_download_path(self.t)
        found = self._probe_range()
        if found is None:
            return False
        size, etag = found
        same = (self.t.segments and os.path.exists(temp_path)
                and size == self.t.total_size
                and (etag == self.t.etag or not (etag and self.t.etag)))
        if self.t.segments and not same:
            self.t.log_event("The server's file is not the one partly on disk — starting over")
            discard_partial(self.t)
        self.t.total_size, self.t.etag = size, etag
        self.t.supports_range = True
        self.t.status = T.DOWNLOADING
        self.t.error = ""
        try:
            os.makedirs(os.path.dirname(self.t.save_path) or ".", exist_ok=True)
            if not self.t.segments:
                self._build_segments()
        except OSError as e:
            self.t.status = T.ERROR
            self.t.error = str(e)
            return True
        # One connection first, then one more for each the server accepts. A
        # CDN that serves only so many answers the extra ones at once, and
        # only a stream already accepted tells that refusal from a dead link
        # (_pushed_back); opened together, the refusals can arrive first.
        self._transfer(temp_path, first=1)
        return True

    def _probe_range(self):
        """(size, etag) when self.url serves its file in byte ranges: the first
        byte comes back as a 206 with the file's total size, and it is not a
        web page. None otherwise, or when nothing answers."""
        try:
            with self._session.get(self.url,
                                   headers={**self._base_headers, "Range": "bytes=0-0"},
                                   stream=True, allow_redirects=True,
                                   timeout=CONNECT_TIMEOUT, verify=utils.VERIFY_TLS,
                                   proxies=utils.PROXIES) as r:
                ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
                total = r.headers.get("Content-Range", "").rpartition("/")[2].strip()
                if (r.status_code != 206 or not total.isdigit() or int(total) <= 0
                        or ctype.startswith("text/html")):
                    log.info("no byte ranges for %s: HTTP %s, %s, Content-Range %r",
                             self.t.filename, r.status_code, ctype or "no type",
                             r.headers.get("Content-Range"))
                    return None
                self._probe_ctype = ctype
                self._link_ok = True
                return int(total), r.headers.get("ETag", "")
        except requests.RequestException as e:
            log.info("no answer for %s: %s", self.t.filename, e)
            return None

    def _transfer(self, temp_path, first):
        """Run the segments to the end: the workers, then the final state."""
        # Connection ramp-up (TCP-style slow start): opening every segment
        # socket at once trips per-IP burst protection on big CDNs (Google's
        # gvt1 resets a 30-connection burst and the whole task dies with
        # "Connection lost"). Start at `first` (4; 1 for a video's link, see
        # _run_media); each stream that connects grows the gate by 1 (up to
        # the real segment count), and 429s/resets halve it — the task
        # self-tunes to what the server tolerates instead of failing.
        n = max(1, len(self.t.segments))
        self._conn_cap = n
        self._max_conns = min(first, n)

        self.t.recompute_downloaded()
        log.info("HTTP %s: %d segment(s), %d bytes, range=%s",
                 self.t.filename, len(self.t.segments), self.t.total_size,
                 self.t.supports_range)

        threads = []
        for i, seg in enumerate(self.t.segments):
            th = threading.Thread(target=self._worker, args=(seg,), daemon=True)
            threads.append(th)
            th.start()
            if i < len(self.t.segments) - 1:
                time.sleep(STAGGER)

        while any(th.is_alive() for th in threads):
            self.t.recompute_downloaded()
            time.sleep(0.25)
        for th in threads:
            th.join()
        self.t.recompute_downloaded()

        # ---- decide final state ----
        if self.t.cancel_requested:
            utils.remove_temp(temp_path)
            self.t.status = T.CANCELLED
            return
        if self.t.status == T.ERROR:
            return
        if self.t.pause_requested:
            self.t.status = T.PAUSED
            return

        all_done = all(s.complete for s in self.t.segments) if self.t.supports_range \
            else self.t.downloaded > 0
        if all_done:
            # cross-volume-safe atomic finalize (shared helper); on failure leave
            # temp_path in %TEMP% for a retry and never mark COMPLETED.
            try:
                # room for a second copy only where finishing is a copy: on one
                # drive it is a rename, and the file's own bytes already took
                # the space (an 8 GB file on 10 GB free failed at 100% here)
                if not utils.same_drive(temp_path, self.t.save_path):
                    self._check_disk_space(self.t.save_path, self.t.total_size)
                utils.finalize_download(temp_path, self.t.save_path)
            except OSError as e:
                self.t.status = T.ERROR
                self.t.error = self._format_disk_error(e, self.t.save_path)
                return
            # a sidecar sits beside a file's own URL; a video's signed link
            # has none, and appending to its query only bothers the CDN
            if utils.HASH_CHECK and not self._media and self._verify_hash() is False:
                self.t.status = T.ERROR
                self.t.error = "SHA-256 mismatch — the file may be corrupt"
                return
            if self._media:
                self.media_done = True      # Completed is yt-dlp's to say
            else:
                self.t.status = T.COMPLETED
        else:
            self.t.status = T.PAUSED

    def _fetch_expected_hash(self):
        """Look for a <url>.sha256 / .sha256sum sidecar; return the 64-hex digest
        or None if absent/unreachable."""
        import re as _re
        for suffix in (".sha256", ".sha256sum"):
            try:
                r = self._session.get(self.url + suffix, headers=self._base_headers,
                                 timeout=10, verify=utils.VERIFY_TLS, proxies=utils.PROXIES)
                if r.status_code == 200:
                    m = _re.search(r"\b([a-fA-F0-9]{64})\b", r.text)
                    if m:
                        return m.group(1)
            except requests.RequestException:
                pass
        return None

    def _verify_hash(self, always_digest=False):
        """Verify the finished file's SHA-256 against the sidecar digest.
        Returns True (match), False (mismatch), or None (no sidecar / unreadable).
        The computed digest is stored on the task for the drawer's Integrity
        section. `always_digest=True` (Force Recheck) hashes the file even when
        no sidecar exists, so the user still gets a digest to compare by hand;
        the normal completion path skips that cost when there is nothing to
        compare against."""
        import hashlib
        expected = self._fetch_expected_hash()
        if not expected and not always_digest:
            self.t.hash_status = "nohash"
            return None
        h = hashlib.sha256()
        try:
            with open(self.t.save_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    if self.t.cancel_requested:
                        return None
                    h.update(chunk)
        except OSError:
            self.t.hash_status = "nohash"
            return None
        self.t.sha256 = h.hexdigest().lower()
        if not expected:
            self.t.hash_status = "nohash"
            return None
        ok = self.t.sha256 == expected.lower()
        self.t.hash_status = "ok" if ok else "fail"
        return ok
