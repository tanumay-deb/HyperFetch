"""Segmented HTTP downloader with pause / resume / cancel and resume-from-disk.

A ``Downloader`` is bound to one :class:`task.DownloadTask` and run synchronously
inside a worker thread by the queue. It updates the task's fields in place so the
GUI (polling on a timer) reflects live progress.
"""
import os
import time
import threading

import requests
import urllib3

import utils
import task as T

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CHUNK = 65536          # 64 KiB read size
DEFAULT_SEGMENTS = 8   # parallel connections when the server supports ranges
HEADERS = {"User-Agent": "Mozilla/5.0 (HyperFetch)"}
CONNECT_TIMEOUT = 15
MAX_RETRIES = 5        # per-segment attempts before the task errors
STAGGER = 0.15         # delay between segment thread starts (rate-limit friendly)


def probe_info(url, headers=None):
    """Lightweight probe for the file-info dialog: size + content type."""
    info = {"size": 0, "type": ""}
    base = {**HEADERS, **(headers or {})}
    try:
        r = requests.head(url, headers=base, allow_redirects=True,
                          timeout=10, verify=utils.VERIFY_TLS)
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
                              timeout=10, verify=utils.VERIFY_TLS) as r:
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
        self.num_segments = max(1, segments)
        # browser-supplied headers (Cookie/Referer/UA) merged into every request
        self._base_headers = {**HEADERS, **(getattr(dtask, "headers", None) or {})}
        self._probe_ctype = ""
        # adaptive connection gate: shrinks when the server answers 429
        self._conn_cv = threading.Condition()
        self._active_conns = 0
        self._max_conns = self.num_segments

    # ------------------------------------------------------------ conn gate
    def _acquire_conn(self):
        with self._conn_cv:
            while self._active_conns >= self._max_conns:
                if self.t.pause_requested:
                    return False
                self._conn_cv.wait(0.2)
            self._active_conns += 1
            return True

    def _release_conn(self):
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
            r = requests.head(self.t.url, headers=self._base_headers,
                              allow_redirects=True,
                              timeout=CONNECT_TIMEOUT, verify=utils.VERIFY_TLS)
            size = int(r.headers.get("Content-Length", 0))
            accept = r.headers.get("Accept-Ranges", "none").lower()
            self._probe_ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        except requests.RequestException:
            size, accept = 0, "none"

        # Some servers lie on HEAD; confirm with a tiny ranged GET.
        if size == 0 or accept == "none":
            try:
                r = requests.get(self.t.url,
                                 headers={**self._base_headers, "Range": "bytes=0-0"},
                                 stream=True, allow_redirects=True,
                                 timeout=CONNECT_TIMEOUT, verify=utils.VERIFY_TLS)
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
        temp_path = self.t.save_path + ".hfdownload"

        if self.t.total_size > 0:
            self._check_disk_space(temp_path, self.t.total_size)
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) != self.t.total_size:
                with open(temp_path, "wb") as f:
                    f.truncate(self.t.total_size)

        if not self.t.supports_range or self.num_segments == 1:
            seg = T.Segment(0, 0, max(0, self.t.total_size - 1))
            self.t.segments = [seg]
            return

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
        temp_path = self.t.save_path + ".hfdownload"
        attempts = 0

        while not self.t.pause_requested:
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
            try:
                # with-block guarantees the response/socket closes on every
                # path — incl. raise_for_status() failures and mid-stream errors
                with requests.get(self.t.url, headers=headers, stream=True,
                                  allow_redirects=True, timeout=CONNECT_TIMEOUT,
                                  verify=utils.VERIFY_TLS) as r:
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
                                seg.downloaded += len(chunk)
                return
            except requests.RequestException as e:
                attempts += 1
                if attempts > MAX_RETRIES:
                    if not self.t.cancel_requested:
                        self.t.status = T.ERROR
                        self.t.error = str(e)
                    return
                resp = getattr(e, "response", None)
                if resp is not None and resp.status_code == 429:
                    self._throttle_conns()
                self._backoff_sleep(e, attempts)
            except OSError as e:
                # disk full / file locked: not retryable, surface it
                if not self.t.cancel_requested:
                    self.t.status = T.ERROR
                    self.t.error = f"disk error: {e}"
                return
            finally:
                self._release_conn()

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
        """Sleep in small steps so pause/cancel stays responsive."""
        deadline = time.time() + self._retry_wait(exc, attempt)
        while time.time() < deadline and not self.t.pause_requested:
            time.sleep(0.2)

    # ------------------------------------------------------------- run
    def run(self):
        # HLS (.m3u8) playlists need segment fetch+concat, not a byte download
        import hls
        if hls.is_hls(self.t.url, self.t.filename, self._probe_ctype):
            hls.HlsDownloader(self.t).run()
            return

        self.t.status = T.DOWNLOADING
        self.t.error = ""
        os.makedirs(os.path.dirname(self.t.save_path) or ".", exist_ok=True)
        temp_path = self.t.save_path + ".hfdownload"

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
                
        self.t.recompute_downloaded()

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
            try:
                if os.path.exists(self.t.save_path):
                    os.remove(self.t.save_path)
                os.rename(temp_path, self.t.save_path)
            except OSError:
                pass
            self.t.status = T.COMPLETED
        else:
            self.t.status = T.PAUSED
