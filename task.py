"""Download task model + segment bookkeeping shared across GUI, queue and downloader."""
import os
import time
import uuid
import threading
import itertools
from typing import List, Optional
import utils

# Task status constants
QUEUED = "Queued"
DOWNLOADING = "Downloading"
PAUSED = "Paused"
COMPLETED = "Completed"
ERROR = "Error"
CANCELLED = "Cancelled"

_id_counter = itertools.count(1)


class Segment:
    """One byte-range of a multi-segment download. Writes to ``{path}.part{index}``."""

    def __init__(self, index, start, end):
        self.index = index
        self.start = start          # absolute byte offset in the final file
        self.end = end              # inclusive end offset
        self.downloaded = 0         # bytes already written to the part file

    @property
    def size(self):
        return self.end - self.start + 1

    @property
    def complete(self):
        return self.downloaded >= self.size


class DownloadTask:
    """Holds all mutable state for a single download.

    The GUI reads these fields on a timer; the downloader thread writes them.
    Simple attribute reads/writes under the GIL are fine for display purposes.
    """

    def __init__(self, url, save_path, filename="", total_size=0,
                 segments=None, downloaded=0, supports_range=True,
                 status=QUEUED, error="", task_id=None, speed_limit=0,
                 headers=None):
        self.id = task_id or uuid.uuid4().hex
        self.url = url
        self.save_path = save_path
        self.filename = filename or url.split("/")[-1]
        # extra request headers (Cookie/Referer/User-Agent from the browser)
        # needed for auth-gated downloads like Google Drive
        self.headers = headers or {}
        self.total_size = total_size
        self.downloaded = downloaded
        self.supports_range = supports_range
        self.segments: List[Segment] = segments or []
        self.status = status
        self.error = error

        # HLS segment progress (0 when not an HLS download)
        self.seg_total = 0
        self.seg_done = 0
        
        self.speed_limit = speed_limit
        self._limiter = utils.RateLimiter()
        self._limiter.set_limit(self.speed_limit)

        # control flags consumed by the downloader threads
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self.lock = threading.Lock()

    # ---- priority queue ordering: lower priority value first, then FIFO ----
    def __lt__(self, other):
        # Fallback to id-based ordering if needed for consistency
        return self.id < getattr(other, "id", "")

    # ---- control API used by the GUI buttons ----
    def request_pause(self):
        self._pause.set()

    def clear_pause(self):
        self._pause.clear()

    def request_cancel(self):
        self._cancel.set()
        self._pause.set()  # also break any sleeping/streaming loops

    @property
    def pause_requested(self):
        return self._pause.is_set()

    @property
    def cancel_requested(self):
        return self._cancel.is_set()

    @property
    def percent(self):
        # HLS: total bytes unknown up front, drive % from segment count
        if self.seg_total > 0:
            return min(100, int(self.seg_done * 100 / self.seg_total))
        if self.total_size <= 0:
            return 0
        return min(100, int(self.downloaded * 100 / self.total_size))

    def recompute_downloaded(self):
        """Sum segment progress -> total downloaded (used after resume)."""
        if self.segments:
            self.downloaded = sum(s.downloaded for s in self.segments)

    def set_speed_limit(self, bps):
        self.speed_limit = bps
        self._limiter.set_limit(bps)

    # ---- persistence -------------------------------------------------
    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "save_path": self.save_path,
            "filename": self.filename,
            "total_size": self.total_size,
            "downloaded": self.downloaded,
            "supports_range": self.supports_range,
            "segments": [s.__dict__ for s in self.segments],
            "status": self.status,
            "error": self.error,
            "speed_limit": self.speed_limit,
            # never write cookies/auth to disk; keep only safe headers (Referer/UA)
            "headers": utils.strip_sensitive(self.headers)
        }

    @classmethod
    def from_dict(cls, d):
        segs = []
        for sd in d.get("segments", []):
            seg = Segment(sd["index"], sd["start"], sd["end"])
            seg.downloaded = sd.get("downloaded", 0)
            segs.append(seg)
        # anything that was in flight when the app closed/crashed must come
        # back as Paused, otherwise it shows "Downloading" with nothing running
        # and resume_task() refuses to requeue it
        status = d.get("status", QUEUED)
        if status in (DOWNLOADING, QUEUED):
            status = PAUSED
        return cls(
            url=d["url"], save_path=d["save_path"],
            filename=d.get("filename", ""), total_size=d.get("total_size", 0),
            segments=segs, downloaded=d.get("downloaded", 0),
            supports_range=d.get("supports_range", True),
            status=status, error=d.get("error", ""),
            task_id=d.get("id"), speed_limit=d.get("speed_limit", 0),
            headers=d.get("headers") or {}
        )
