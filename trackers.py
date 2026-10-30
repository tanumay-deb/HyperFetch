"""The public tracker list, kept fresh from two maintained lists.

torrent.PUBLIC_TRACKERS is a list picked by hand on one day, and a fixed list
rots: tracker.openbittorrent.com was on it until its domain expired. So once a
day the app fetches ngosang/trackerslist's trackers_best.txt and newTrackon's
stable list, puts the trackers on both first, and keeps the result in
``trackers.json``. Nothing fetched, or too little, leaves the last good list,
or the built-in one. Settings -> Torrents -> "Keep the public tracker list up
to date" turns it off.

aria2 takes a new list on the running daemon (changeGlobalOption bt-tracker,
measured on 1.37.0) for downloads added after it; torrent.tell_trackers does
that. Every tracker ever put on the list is remembered, because magnet
metadata saved meanwhile has the list of its day baked in, and a private
torrent must exclude all of them (torrent.tracker_opts).

Each line is checked before aria2 sees it: the list goes on aria2's command
line joined by commas, so a tracker is a plain udp/http(s) announce address
and nothing else.
"""
import logging
import os
import re
import threading
import time

import utils

log = logging.getLogger(__name__)

SOURCES = [
    "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
    "https://newtrackon.com/api/stable",
]
EVERY = 24 * 3600           # fetched again after this long
RETRY = 3600                # after a failed fetch
MAX = 15                    # trackers on the list (aria2 asks one at a time)
MIN_GOOD = 3                # fewer than this fetched is not a list to switch to
KEEP_EVER = 500             # trackers remembered for private torrents' excludes
MAX_BODY = 256 * 1024
TIMEOUT = 15

_ADDRESS = re.compile(
    r"^(udp|https?)://[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)+(?::\d{1,5})?/announce$")

_lock = threading.Lock()
_cache = None               # (mtime, data) of trackers.json as last read


def _path():
    return os.path.join(utils.app_data_dir(), "trackers.json")


def parse(text):
    """The announce addresses in a fetched list, in order, each once."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        tr = line.strip()
        if len(tr) <= 200 and _ADDRESS.match(tr) and tr.lower() not in seen:
            seen.add(tr.lower())
            out.append(tr)
    return out


def pick(lists):
    """Trackers on every list first, in the first list's order, then the rest
    in turn from each list; MAX at most."""
    lists = [lst for lst in lists if lst]
    if not lists:
        return []
    on_all = [tr for tr in lists[0]
              if all(tr.lower() in {x.lower() for x in lst} for lst in lists[1:])]
    out, seen = list(on_all), {tr.lower() for tr in on_all}
    for row in range(max(len(lst) for lst in lists)):
        for lst in lists:
            if row < len(lst) and lst[row].lower() not in seen:
                seen.add(lst[row].lower())
                out.append(lst[row])
    return out[:MAX]


def _load():
    global _cache
    path = _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        if _cache and _cache[0] == mtime:
            return _cache[1]
    data = utils.load_json(path, {})
    if not isinstance(data, dict):
        data = {}
    with _lock:
        _cache = (mtime, data)
    return data


def current():
    """The public trackers to add to a torrent: the fetched list, else the built-in."""
    import torrent
    got = [tr for tr in _load().get("list") or [] if isinstance(tr, str) and _ADDRESS.match(tr)]
    return got if len(got) >= MIN_GOOD else list(torrent.PUBLIC_TRACKERS)


def ever():
    """Every tracker the fetched list has held (for private torrents' excludes)."""
    return [tr for tr in _load().get("ever") or [] if isinstance(tr, str) and _ADDRESS.match(tr)]


def _fetch(url):
    import requests
    r = requests.get(url, timeout=TIMEOUT, stream=True, proxies=utils.PROXIES,
                     verify=utils.VERIFY_TLS, headers=utils.DEFAULT_HEADERS)
    try:
        r.raise_for_status()
        body = b""
        for chunk in r.iter_content(16384):
            body += chunk
            if len(body) > MAX_BODY:
                raise ValueError("list larger than %d bytes" % MAX_BODY)
        return body.decode("utf-8", "replace")
    finally:
        r.close()


def _save(data):
    global _cache
    utils.save_json(_path(), data)
    with _lock:
        _cache = None


def refresh(fetch=_fetch, now=None):
    """Fetch the lists if a day has passed (an hour after a failure). True when
    the list changed."""
    now = time.time() if now is None else now
    data = _load()
    due = float(data["at"]) + EVERY if data.get("at") is not None else now   # never fetched: now
    if data.get("failed_at"):
        due = min(due, float(data["failed_at"]) + RETRY)
    if now < due:
        return False
    lists = []
    for url in SOURCES:
        try:
            lists.append(parse(fetch(url)))
        except Exception as e:
            log.info("tracker list %s: %s", url.split("/")[2], e)
    picked = pick(lists)
    if len(picked) < MIN_GOOD:
        data = dict(data, failed_at=now)
        _save(data)
        return False
    old = data.get("list") or []
    known = {tr.lower() for tr in data.get("ever") or []}
    ever_list = list(data.get("ever") or []) + [tr for tr in picked if tr.lower() not in known]
    _save({"at": now, "list": picked, "ever": ever_list[-KEEP_EVER:]})
    if picked != old:
        log.info("public tracker list: %d trackers (%d new)", len(picked),
                 len({t.lower() for t in picked} - {t.lower() for t in old}))
        return True
    return False


class Refresher:
    """Keeps the list fresh in the background: a look every hour, a fetch when due."""

    def __init__(self, enabled_fn=lambda: True, tick=3600):
        self._enabled_fn = enabled_fn
        self._tick = tick
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self._thread:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="tracker-list")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        if self._stop.wait(30):         # not in the busy first seconds
            return
        while True:
            try:
                if self._enabled_fn():
                    refresh()
            except Exception:
                log.exception("tracker list")
            if self._stop.wait(self._tick):
                return
