"""Segmented HTTP downloader with pause / resume / cancel and resume-from-disk.

A ``Downloader`` is bound to one :class:`task.DownloadTask` and run synchronously
inside a worker thread by the queue. It updates the task's fields in place so the
GUI (polling on a timer) reflects live progress.
"""
import os
import time
import shutil
import logging
import tempfile
import threading

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

CHUNK = 1048576        # 1 MiB read size
DEFAULT_SEGMENTS = 8   # parallel connections when the server supports ranges
HEADERS = utils.DEFAULT_HEADERS
CONNECT_TIMEOUT = 15
MAX_RETRIES = 5        # per-segment attempts before the task errors
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


class Downloader:
    def __init__(self, dtask: "T.DownloadTask", segments=DEFAULT_SEGMENTS):
        self.t = dtask
        self.num_segments = max(0, segments)
        # global ceiling from Settings -> Network -> Max Connections (0 = off).
        # Only clamps an explicit count; Auto mode (0) is left untouched.
        if utils.MAX_CONNECTIONS > 0 and self.num_segments > 0:
            self.num_segments = min(self.num_segments, utils.MAX_CONNECTIONS)
        # browser-supplied headers (Cookie/Referer/UA) merged into every request
        self._base_headers = {**HEADERS, **(getattr(dtask, "headers", None) or {})}
        self._probe_ctype = ""
        # adaptive connection gate: shrinks when the server answers 429.
        # Initialized to a safe non-zero placeholder; run() sets the real cap
        # from the ACTUAL segment count once segments exist — deriving it from
        # num_segments would be 0 in Auto mode and deadlock every worker.
        self._conn_cv = threading.Condition()
        self._active_conns = 0
        self._max_conns = max(1, self.num_segments)
        # one keep-alive session for this download's probe + all segment threads.
        # Auto mode (num_segments==0) can fan out to 32 segments, so size for that.
        self._session = _make_session(max(8, self.num_segments or 32))

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
        """Server is rate limiting: halve allowed parallel connections."""
        with self._conn_cv:
            self._max_conns = max(1, self._max_conns // 2)

    # ------------------------------------------------------------------ probe
    def _probe(self):
        """One request to learn total size + range support, following redirects."""
        try:
            r = self._session.head(self.t.url, headers=self._base_headers,
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
                r = self._session.get(self.t.url,
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
        temp_path = utils.temp_download_path(self.t.id)

        if self.t.total_size > 0:
            self._check_disk_space(temp_path, self.t.total_size)
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) != self.t.total_size:
                with open(temp_path, "wb") as f:
                    f.truncate(self.t.total_size)

        if not self.t.supports_range or self.num_segments == 1:
            seg = T.Segment(0, 0, max(0, self.t.total_size - 1))
            self.t.segments = [seg]
            return

        if self.num_segments == 0:
            mb = self.t.total_size / (1024 * 1024)
            if mb < 10: n = 1
            elif mb < 100: n = 4
            elif mb < 1000: n = 8
            elif mb < 5000: n = 16
            else: n = 32
        else:
            n = min(self.num_segments, max(1, self.t.total_size // (1024 * 1024) or 1))

        part = self.t.total_size // n
        segs = []
        for i in range(n):
            start = i * part
            end = self.t.total_size - 1 if i == n - 1 else start + part - 1
            seg = T.Segment(i, start, end)
            segs.append(seg)
        self.t.segments = segs

    # ------------------------------------------------------------- workers
    def _worker(self, seg):
        """Stream one segment, writing directly to the pre-allocated .hfdownload file."""
        temp_path = utils.temp_download_path(self.t.id)
        attempts = 0

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

            if not self._acquire_conn():
                return  # paused while waiting for a connection slot
            retry_exc = None
            try:
                # with-block guarantees the response/socket closes on every
                # path — incl. raise_for_status() failures and mid-stream errors
                with self._session.get(self.t.url, headers=headers, stream=True,
                                  allow_redirects=True, timeout=CONNECT_TIMEOUT,
                                  verify=utils.VERIFY_TLS, proxies=utils.PROXIES) as r:
                    r.raise_for_status()
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
                return
            except requests.RequestException as e:
                resp = getattr(e, "response", None)
                if resp is not None and resp.status_code == 403:
                    if not self.t.cancel_requested:
                        self.t.status = T.ERROR
                        self.t.error = "HTTP 403 Forbidden - URL expired"
                        log.warning("403 (URL expired) seg %d: %s", seg.index, self.t.filename)
                    return
                attempts += 1
                if attempts > MAX_RETRIES:
                    if not self.t.cancel_requested:
                        self.t.status = T.ERROR
                        self.t.error = str(e)
                        log.error("seg %d of %s failed after %d retries: %s",
                                  seg.index, self.t.filename, MAX_RETRIES, e)
                    return
                resp = getattr(e, "response", None)
                if resp is not None and resp.status_code == 429:
                    self._throttle_conns()
                    log.warning("429 rate-limited: %s — halved to %d connections",
                                self.t.filename, self._max_conns)
                else:
                    log.debug("seg %d retry %d/%d: %s — %s",
                              seg.index, attempts, MAX_RETRIES, self.t.filename, e)
                retry_exc = e
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
                self._backoff_sleep(retry_exc, attempts)

    @staticmethod
    def _retry_wait(exc, attempt):
        """Seconds to wait before a retry; honors Retry-After on 429."""
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code == 429:
            ra = resp.headers.get("Retry-After", "")
            if ra.isdigit():
                return min(int(ra), 60)
        return min(2 ** attempt, 30)        # 2, 4, 8, 16, 30

    def _backoff_sleep(self, exc, attempt):
        """Sleep in small steps so pause/cancel stays responsive.
        Polling cancel_requested too (not just pause) — otherwise a Cancel
        during a 60s Retry-After window would sit dormant until the sleep
        ended, instead of breaking out within ~200ms."""
        deadline = time.time() + self._retry_wait(exc, attempt)
        while time.time() < deadline \
                and not self.t.pause_requested \
                and not self.t.cancel_requested:
            time.sleep(0.2)

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

        # media pages (YouTube etc.) -> yt-dlp; forced via the New Download toggle
        # or auto-detected by host. Not a direct file, so it can't be byte-split.
        import yt_dl
        if getattr(self.t, "use_ytdlp", False) or yt_dl.is_ytdlp_url(self.t.url):
            yt_dl.YtDlpDownloader(self.t).run()
            return

        # HLS (.m3u8) playlists need segment fetch+concat, not a byte download
        import hls
        if hls.is_hls(self.t.url, self.t.filename, self._probe_ctype):
            hls.HlsDownloader(self.t).run()
            return

        self.t.status = T.DOWNLOADING
        self.t.error = ""
        os.makedirs(os.path.dirname(self.t.save_path) or ".", exist_ok=True)
        temp_path = utils.temp_download_path(self.t.id)

        try:
            if not self.t.segments:
                self._probe()
                # auth-gated hosts (Google Drive etc.) send an HTML login/interstitial
                # page instead of the file — catch it before writing a broken file
                ext = os.path.splitext(self.t.save_path)[1].lower()
                if (self._probe_ctype.startswith("text/html")
                        and ext not in ("", ".html", ".htm")):
                    self.t.status = T.ERROR
                    self.t.error = ("Server sent a web page, not the file "
                                    "(login/cookies required — use the browser extension)")
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

        # Set the connection cap from the ACTUAL segment count (covers fresh
        # builds, Auto mode, and resume-from-disk where segments are restored
        # and _build_segments is skipped). Must be > 0 or the gate deadlocks.
        self._max_conns = max(1, len(self.t.segments))

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
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
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
            # temp_path is in %TEMP%, often a DIFFERENT volume than the
            # destination -> shutil.move is copy+delete and can fail (dest full,
            # AV/permission lock). Move into a sibling temp in the DEST dir first,
            # then atomically replace, so a failure never both deletes the old
            # file AND loses the new one, and never marks COMPLETED on failure.
            try:
                self._check_disk_space(self.t.save_path, self.t.total_size)
                staged = self.t.save_path + ".hfmove"
                shutil.move(temp_path, staged)        # cross-volume copy lands here
                os.replace(staged, self.t.save_path)  # atomic same-volume swap
            except OSError as e:
                self.t.status = T.ERROR
                self.t.error = self._format_disk_error(e, self.t.save_path)
                for leftover in (self.t.save_path + ".hfmove",):
                    try:
                        if os.path.exists(leftover):
                            os.remove(leftover)
                    except OSError:
                        pass
                return  # leave temp_path in %TEMP% for a retry
            if utils.HASH_CHECK and self._verify_hash() is False:
                self.t.status = T.ERROR
                self.t.error = "SHA-256 mismatch — the file may be corrupt"
                return
            self.t.status = T.COMPLETED
        else:
            self.t.status = T.PAUSED

    def _fetch_expected_hash(self):
        """Look for a <url>.sha256 / .sha256sum sidecar; return the 64-hex digest
        or None if absent/unreachable."""
        import re as _re
        for suffix in (".sha256", ".sha256sum"):
            try:
                r = self._session.get(self.t.url + suffix, headers=self._base_headers,
                                 timeout=10, verify=utils.VERIFY_TLS, proxies=utils.PROXIES)
                if r.status_code == 200:
                    m = _re.search(r"\b([a-fA-F0-9]{64})\b", r.text)
                    if m:
                        return m.group(1)
            except requests.RequestException:
                pass
        return None

    def _verify_hash(self):
        """Verify the finished file's SHA-256 against the sidecar digest.
        Returns True (match), False (mismatch), or None (no sidecar / unreadable)."""
        import hashlib
        expected = self._fetch_expected_hash()
        if not expected:
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
        ok = h.hexdigest().lower() == expected.lower()
        self.t.hash_status = "ok" if ok else "fail"
        return ok
