"""Pairing a Firefox install by asking the person at this computer.

Chrome and Edge pair themselves: /pair serves the token to their store
listings' fixed extension ids. A Firefox install has none - its address is a
random moz-extension:// UUID, different on every install - so /pair can never
recognise it. It asks instead: the extension shows a short code and posts it to
/pair/request, the window asks "Pair Firefox with HyperFetch?" showing the same
code, and the person compares the two. An address they allow gets the token
from then on, across restarts.

Anything can ask. Every Firefox add-on has a moz-extension address, and a
local program can put one in an Origin header. So asking is cheap and allowing
is the gate: one question at a time, a refused address is not asked about
again for a while, only a few requests may wait at once, and the code is how
the person tells HyperFetch's request from anyone else's.
"""
import re
import threading
import time
from collections import deque

import utils

# Firefox's extension origin: the scheme and a UUID, nothing else.
ORIGIN_RE = re.compile(r"^moz-extension://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                       r"[0-9a-f]{4}-[0-9a-f]{12}$")
CODE_RE = re.compile(r"^\d{4}$")

DENY_COOLDOWN = 10 * 60   # a refused address is not asked about again for this long
PENDING_TTL = 2 * 60      # a request nobody keeps asking about is dropped after this
MAX_PENDING = 3           # requests waiting at once; more is somebody knocking


class PairRequests:
    """Firefox installs asking to pair, and the ones already allowed.

    Shared between the server thread (request) and the window (next_prompt,
    decide), so every method takes the lock.
    """

    def __init__(self, path, clock=time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._trusted = self._load()
        self._pending = {}        # origin -> {"code": str, "at": last time it asked}
        self._denied = {}         # origin -> time the refusal runs out
        self._prompts = deque()   # origins waiting for the window to ask about

    def _load(self):
        data = utils.load_json(self._path, {})
        origins = data.get("origins", []) if isinstance(data, dict) else []
        return {o for o in origins if isinstance(o, str) and ORIGIN_RE.match(o)}

    def _save(self):
        utils.save_json(self._path, {"origins": sorted(self._trusted)})

    def request(self, origin, code):
        """What to tell the extension: "approved", "pending", "denied",
        "busy" (too many waiting) or "invalid"."""
        origin, code = origin or "", code or ""
        if not ORIGIN_RE.match(origin) or not CODE_RE.match(code):
            return "invalid"
        now = self._clock()
        with self._lock:
            if origin in self._trusted:
                return "approved"
            if self._denied.get(origin, 0) > now:
                return "denied"
            self._denied.pop(origin, None)
            for o, p in list(self._pending.items()):
                if o != origin and now - p["at"] > PENDING_TTL:
                    del self._pending[o]
            waiting = self._pending.get(origin)
            if waiting:
                # Still asking while its page is open. The code stays the one
                # the window was given, so the two can only ever match.
                waiting["at"] = now
                return "pending"
            if len(self._pending) >= MAX_PENDING:
                return "busy"
            self._pending[origin] = {"code": code, "at": now}
            self._prompts.append(origin)
            return "pending"

    def next_prompt(self):
        """(origin, code) for the window to ask about next, or None."""
        with self._lock:
            while self._prompts:
                origin = self._prompts.popleft()
                waiting = self._pending.get(origin)
                if waiting and origin not in self._trusted:
                    return origin, waiting["code"]
            return None

    def decide(self, origin, allow):
        """The person's answer. It counts even if the request has meanwhile
        expired: they compared the code and said yes (or no) to this address."""
        with self._lock:
            self._pending.pop(origin, None)
            if allow:
                if ORIGIN_RE.match(origin or ""):
                    self._trusted.add(origin)
                    self._denied.pop(origin, None)
                    self._save()
            else:
                self._denied[origin] = self._clock() + DENY_COOLDOWN

    def forget_all(self):
        """Unpair every Firefox install. Returns how many there were."""
        with self._lock:
            n = len(self._trusted)
            self._trusted.clear()
            self._save()
            return n

    def count(self):
        with self._lock:
            return len(self._trusted)
