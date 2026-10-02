"""Asking trackers how many seeders a torrent has, before it runs.

A torrent waiting in the queue says nothing about its swarm: aria2 counts
seeders only once it is running. A tracker's scrape reply does - seeders,
completed downloads and leechers, for up to 70 torrents in one question - so
SwarmScout asks the trackers about waiting torrents now and then and writes the
answer onto them (swarm_seeds, swarm_peers, swarm_at). The queue then starts
the torrent with the most seeders first (swarm.py, queue_manager.py).

Two protocols: UDP (BEP 15: connect, then scrape) and HTTP (the announce
address's "scrape" twin, a bencoded reply). UDP goes direct, as aria2's tracker
traffic does; HTTP takes the proxy only when torrents do (utils.PROXY_TORRENTS),
as aria2's --all-proxy does. Names resolve through socket.getaddrinfo, so the
DNS-over-HTTPS fallback (doh.py) covers them too.

The trackers learn what they learn anyway when the torrent runs: this address
and the torrent's infohash. Settings -> Torrents -> "Check seeders of queued
torrents" turns it off.
"""
import base64
import functools
import logging
import os
import re
import secrets
import socket
import struct
import threading
import time
import urllib.parse

import task as T
import torrent
import utils

log = logging.getLogger(__name__)

PROTOCOL_ID = 0x41727101980   # BEP 15's constant that opens a "connect"
BATCH = 70                    # infohashes per scrape request
TIMEOUT = 5.0                 # seconds for one tracker request
PARALLEL = 4                  # trackers asked at once
MAX_TRACKERS = 8              # trackers asked about one torrent
EVERY = 15 * 60               # a torrent is asked about again after this long
TICK = 10                     # how often the scout looks for torrents to ask about
FAIL_LIMIT = 3                # failures in a row before a tracker is rested...
FAIL_SKIP = 3600              # ...for this long
MAX_BODY = 1 << 20            # an HTTP scrape reply larger than this is not one

WAITING = (T.QUEUED, T.PAUSED, T.SCHEDULED)

_CONNECT, _SCRAPE, _ERROR = 0, 2, 3


class ScrapeError(Exception):
    """The tracker could not say: unreachable, silent, or a reply that is not one."""


# ---- UDP (BEP 15) ------------------------------------------------------------

def _udp_address(url):
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as e:
        raise ScrapeError("not a tracker address: %s" % url) from e
    if not host or not port:
        raise ScrapeError("not a tracker address: %s" % url)
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM)
    except (OSError, UnicodeError) as e:
        raise ScrapeError("cannot resolve %s: %s" % (host, e)) from e
    if not infos:
        raise ScrapeError("cannot resolve %s" % host)
    # IPv4 first: nearly every tracker has it, and many machines have no IPv6 route.
    infos.sort(key=lambda i: i[0] != socket.AF_INET)
    return infos[0][0], infos[0][4]


def _udp_ask(sock, packet, tid, timeout):
    """Send a request, send it again halfway through the wait (UDP loses
    packets), and return (action, body) of the reply to this transaction."""
    start = time.monotonic()
    deadline, resend_at = start + timeout, start + timeout / 2
    sock.send(packet)
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise ScrapeError("no reply")
        if resend_at is not None and now >= resend_at:
            sock.send(packet)
            resend_at = None
        until = deadline if resend_at is None else min(deadline, resend_at)
        sock.settimeout(max(0.01, until - now))
        try:
            data = sock.recv(65536)
        except socket.timeout:
            continue
        except OSError as e:          # the port is closed: Windows reports the ICMP here
            raise ScrapeError(str(e)) from e
        if len(data) < 8:
            continue
        action, got = struct.unpack(">II", data[:8])
        if got != tid:
            continue                  # a late reply to an earlier question
        if action == _ERROR:
            raise ScrapeError("tracker says: %s" % data[8:208].decode("utf-8", "replace"))
        return action, data[8:]


def udp_scrape(url, infohashes, timeout=TIMEOUT):
    """{infohash: (seeders, completed, leechers)} from a UDP tracker."""
    hashes = [bytes(h) for h in infohashes]
    family, addr = _udp_address(url)
    out = {}
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
    except OSError as e:
        raise ScrapeError(str(e)) from e
    with sock:
        try:
            sock.connect(addr)        # replies from anywhere else are not read
            tid = secrets.randbits(32)
            action, body = _udp_ask(sock, struct.pack(">QII", PROTOCOL_ID, _CONNECT, tid),
                                    tid, timeout)
            if action != _CONNECT or len(body) < 8:
                raise ScrapeError("not a tracker's reply")
            conn = struct.unpack(">Q", body[:8])[0]
            for i in range(0, len(hashes), BATCH):
                chunk = hashes[i:i + BATCH]
                tid = secrets.randbits(32)
                action, body = _udp_ask(sock, struct.pack(">QII", conn, _SCRAPE, tid)
                                        + b"".join(chunk), tid, timeout)
                if action != _SCRAPE or len(body) < 12 * len(chunk):
                    raise ScrapeError("not a scrape reply")
                for j, h in enumerate(chunk):
                    out[h] = struct.unpack(">III", body[12 * j:12 * j + 12])
        except OSError as e:
            raise ScrapeError(str(e)) from e
    return out


# ---- HTTP --------------------------------------------------------------------

def scrape_url(announce):
    """An HTTP tracker's scrape address, or None when it has none.

    By convention it is the announce address with the last path segment's
    leading "announce" swapped for "scrape"; a tracker whose address is not
    built that way does not support scraping."""
    try:
        parts = urllib.parse.urlsplit(announce)
    except ValueError:
        return None
    head, sep, last = parts.path.rpartition("/")
    if not last.startswith("announce"):
        return None
    return urllib.parse.urlunsplit(
        parts._replace(path=head + sep + "scrape" + last[len("announce"):]))


def _count(v):
    return v if isinstance(v, int) and v >= 0 else 0


def http_scrape(announce, infohashes, timeout=TIMEOUT):
    """{infohash: (seeders, completed, leechers)} from an HTTP tracker. A
    torrent the tracker does not know is left out of its reply."""
    import requests
    url = scrape_url(announce)
    if not url:
        raise ScrapeError("this tracker has no scrape address")
    hashes = [bytes(h) for h in infohashes]
    proxies = utils.PROXIES if (utils.PROXIES and utils.PROXY_TORRENTS) else None
    parts = urllib.parse.urlsplit(url)
    out = {}
    with requests.Session() as s:
        s.trust_env = False           # direct unless torrents use the proxy
        for i in range(0, len(hashes), BATCH):
            chunk = hashes[i:i + BATCH]
            ask = "&".join("info_hash=" + urllib.parse.quote_from_bytes(h, safe="")
                           for h in chunk)
            full = urllib.parse.urlunsplit(parts._replace(
                query=parts.query + "&" + ask if parts.query else ask))
            try:
                with s.get(full, timeout=timeout, verify=utils.VERIFY_TLS, proxies=proxies,
                           stream=True,
                           headers={"User-Agent": "HyperFetch/%s" % utils.APP_VERSION}) as r:
                    if r.status_code != 200:
                        raise ScrapeError("HTTP %d" % r.status_code)
                    body = b""
                    for piece in r.iter_content(65536):
                        body += piece
                        if len(body) > MAX_BODY:
                            raise ScrapeError("reply too large")
            except requests.RequestException as e:
                raise ScrapeError(str(e)) from e
            try:
                reply = torrent._bdecode(body)
            except ValueError as e:
                raise ScrapeError("not a scrape reply") from e
            if not isinstance(reply, dict):
                raise ScrapeError("not a scrape reply")
            if isinstance(reply.get(b"failure reason"), bytes):
                raise ScrapeError("tracker says: %s"
                                  % reply[b"failure reason"][:200].decode("utf-8", "replace"))
            files = reply.get(b"files")
            if not isinstance(files, dict):
                raise ScrapeError("not a scrape reply")
            for h in chunk:
                f = files.get(h)
                if isinstance(f, dict):
                    out[h] = (_count(f.get(b"complete")), _count(f.get(b"downloaded")),
                              _count(f.get(b"incomplete")))
    return out


def scrape_tracker(tracker, infohashes, timeout=TIMEOUT):
    """Ask one tracker, whichever protocol it speaks."""
    if tracker.lower().startswith("udp://"):
        return udp_scrape(tracker, infohashes, timeout)
    return http_scrape(tracker, infohashes, timeout)


# ---- which torrent, which trackers ------------------------------------------

def infohash_bytes(ih):
    """The 20-byte infohash from its 40-hex or 32-base32 spelling; None otherwise."""
    ih = (ih or "").strip()
    try:
        if re.fullmatch(r"[0-9a-fA-F]{40}", ih):
            return bytes.fromhex(ih)
        if re.fullmatch(r"[A-Za-z2-7]{32}", ih):
            return base64.b32decode(ih, casefold=True)
    except ValueError:
        pass
    return None


@functools.lru_cache(maxsize=64)
def _torrent_file(path, mtime_ns, size):
    """(infohash hex, [trackers], private) of a .torrent file; the stamp keys
    the cache, so an unchanged file is read once."""
    ih = torrent.torrent_infohash(path)
    trackers = []
    try:
        with open(path, "rb") as f:
            meta = torrent._bdecode(f.read())
    except (OSError, ValueError):
        return ih, trackers, False
    if not isinstance(meta, dict):
        return ih, trackers, False
    flat = [meta.get(b"announce")]
    for tier in meta.get(b"announce-list") or []:
        flat.extend(tier if isinstance(tier, list) else [tier])
    for a in flat:
        if isinstance(a, bytes):
            trackers.append(a.decode("utf-8", "replace").strip())
    return ih, trackers, torrent.is_private_torrent(path)


def _file_facts(task):
    # the .torrent it was added from, else the copy kept once its metadata arrived
    path = torrent.metadata_torrent_path(task)
    if not path:
        return "", [], False
    try:
        st = os.stat(path)
    except OSError:
        return "", [], False
    return _torrent_file(path, st.st_mtime_ns, st.st_size)


def task_infohash(task):
    """The 20-byte infohash of a torrent task, or None while it is unknown."""
    ih = getattr(task, "infohash", "") or torrent.magnet_infohash(task.url or "")
    if not ih:
        ih = _file_facts(task)[0]
    return infohash_bytes(ih)


def scrapeable(tracker):
    t = (tracker or "").strip().lower()
    if t.startswith("udp://"):
        return True
    return t.startswith(("http://", "https://")) and scrape_url(tracker) is not None


def trackers_for(task):
    """The trackers to ask about a torrent, best first: its own (the magnet's
    tr= list, or the .torrent's announce list), then the public ones - the
    same the running torrent announces to (aria2d's --bt-tracker). A private
    torrent talks to its own trackers and nobody else (BEP 27), so it gets no
    public ones. Only trackers that can be scraped, each once."""
    _, file_trackers, private = _file_facts(task)
    own = torrent.magnet_trackers(task.url) if torrent.is_magnet(task.url) else file_trackers
    public = [] if private else list(torrent.PUBLIC_TRACKERS)
    out, seen = [], set()
    for tr in list(own) + public:
        tr = (tr or "").strip()
        if tr and tr.lower() not in seen and scrapeable(tr):
            seen.add(tr.lower())
            out.append(tr)
    return out


# ---- the scout ---------------------------------------------------------------

class SwarmScout:
    """Asks trackers about waiting torrents in the background.

    A torrent never asked about is asked within TICK seconds; after that every
    EVERY seconds. Its infohashes go to its first MAX_TRACKERS trackers that
    are not resting, grouped so one request per tracker covers every torrent
    due. The most seeders and the most leechers any tracker reports are what
    is written. When no tracker answers, what was known is left alone.
    """

    def __init__(self, tasks_fn, enabled_fn=lambda: True, scrape_fn=scrape_tracker,
                 clock=time.time):
        self._tasks_fn = tasks_fn
        self._enabled_fn = enabled_fn
        self._scrape_fn = scrape_fn
        self._clock = clock
        self.trackers_fn = trackers_for
        self._asked = {}              # task id -> when it was last asked about
        self._fails = {}              # tracker -> failures in a row
        self._rest_until = {}         # tracker -> left alone until then
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="swarm-scout")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(TICK):
            try:
                self.run_once()
            except Exception:
                log.exception("swarm scout")

    def _due(self, now):
        tasks = list(self._tasks_fn())
        live = {t.id for t in tasks}
        for gone in [k for k in self._asked if k not in live]:
            del self._asked[gone]
        due = []
        for t in tasks:
            if t.status not in WAITING or not torrent.is_torrent_task(t.url, t.filename):
                continue
            last = max(self._asked.get(t.id, 0.0), float(getattr(t, "swarm_at", 0.0) or 0.0))
            if last and now - last < EVERY:
                continue
            ih = task_infohash(t)
            if ih:
                due.append((t, ih))
        return due

    def run_once(self):
        """One pass: ask about every torrent that is due. Returns how many were."""
        if not self._enabled_fn():
            return 0
        now = self._clock()
        due = self._due(now)
        plan = {}                     # tracker -> infohashes to ask it about
        for t, ih in due:
            self._asked[t.id] = now
            try:
                trackers = self.trackers_fn(t)
            except Exception:
                log.debug("trackers of %s", t.filename, exc_info=True)
                continue
            usable = [tr for tr in trackers if self._rest_until.get(tr, 0.0) <= now]
            for tr in usable[:MAX_TRACKERS]:
                plan.setdefault(tr, set()).add(ih)
        best = {}                     # infohash -> [seeders, leechers]
        for tr, (ok, got) in self._ask_all(plan).items():
            if not ok:
                self._failed(tr, now)
                continue
            self._fails.pop(tr, None)
            for ih, (seeds, done, peers) in got.items():
                if ih not in plan[tr] or not (seeds or done or peers):
                    continue          # all zeros: this tracker has never heard of it
                b = best.setdefault(ih, [0, 0])
                b[0], b[1] = max(b[0], seeds), max(b[1], peers)
        if self._stop.is_set():
            return len(due)
        at = self._clock()
        for t, ih in due:
            if ih in best:
                # counts before the stamp: a reader never sees a new time on old numbers
                t.swarm_seeds, t.swarm_peers = best[ih]
                t.swarm_at = at
        return len(due)

    def _failed(self, tracker, now):
        n = self._fails.get(tracker, 0) + 1
        self._fails[tracker] = n
        if n >= FAIL_LIMIT:
            self._rest_until[tracker] = now + FAIL_SKIP

    def _ask_all(self, plan):
        """{tracker: (ok, reply or error)}, PARALLEL at a time. Plain daemon
        threads, not an executor: an executor's workers are waited for at exit,
        and a queue of silent trackers would hold the app open."""
        jobs = sorted(plan.items())
        results, lock = {}, threading.Lock()

        def worker():
            while not self._stop.is_set():
                with lock:
                    if not jobs:
                        return
                    tr, hashes = jobs.pop(0)
                try:
                    res = (True, self._scrape_fn(tr, sorted(hashes)))
                except Exception as e:
                    log.debug("scrape %s: %s", tr, e)
                    res = (False, e)
                with lock:
                    results[tr] = res

        threads = [threading.Thread(target=worker, daemon=True, name="swarm-scout-ask")
                   for _ in range(min(PARALLEL, len(jobs)))]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return results
