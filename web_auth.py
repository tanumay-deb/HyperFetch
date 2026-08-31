"""Password + session secret for the web interface.

Deliberately NOT the extension's pairing token. That token is handed to any
local caller by /pair, would sit in the page source where the browser (and any
extension) can read it, and cannot be rotated without re-pairing the extension.
The web UI is reachable from the LAN, so it needs a secret the user chooses,
that is stored hashed, and that can be changed on its own.

Stored in ``web_auth.json`` in the app-data dir, 0600, alongside ``pair_token``.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time

import utils

log = logging.getLogger("hyperfetch.webauth")

# scrypt cost. n=2**15 is roughly 100ms on a modern desktop — slow enough that
# guessing over a LAN is hopeless, fast enough that a phone login feels instant.
_N, _R, _P = 2 ** 15, 8, 1
_DKLEN = 32

# Login throttle. The web UI can be reached by every device on the network, so
# an unlimited guess rate would make a weak password worthless.
MAX_ATTEMPTS = 5
LOCKOUT = 300.0            # seconds locked out after MAX_ATTEMPTS failures

# Two thresholds, because the risk is not the same in both modes. Bound to
# 127.0.0.1 the only thing that can reach the page is someone already at this
# PC, so a short password is the user's own business. Once it answers the LAN
# it is the only thing between the network and the download queue, and the
# LAN toggle refuses a password that was set below MIN_LAN_PASSWORD.
MIN_PASSWORD = 4
MIN_LAN_PASSWORD = 8

DEFAULT_USERNAME = "admin"


def _path():
    return os.path.join(utils.app_data_dir(), "web_auth.json")


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    """Write the auth file 0600. Returns True on success.

    Never silently swallows a failure: a password the user believes they set,
    which was never stored, is worse than no password at all.
    """
    p = _path()
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass                     # best effort; Windows ACLs differ
        return True
    except OSError as e:
        log.error("could not save the web password (%s) — it is NOT set", e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                          n=_N, r=_R, p=_P, dklen=_DKLEN,
                          maxmem=64 * 1024 * 1024)


def has_password():
    """True when a web password has been set."""
    d = _load()
    return bool(d.get("salt") and d.get("hash"))


def is_enabled():
    """True when the user has switched the web client on in Settings.

    Separate from "a password exists" so the two failure messages can differ:
    a user who never turned it on needs different advice from one who did.
    """
    return bool(_load().get("enabled"))


def set_enabled(on):
    d = _load()
    d["enabled"] = bool(on)
    return _save(d)


def username():
    """The configured username, or the default when none was ever set."""
    u = (_load().get("username") or "").strip()
    return u or DEFAULT_USERNAME


def set_username(user):
    """Change the username without touching the password."""
    d = _load()
    d["username"] = (user or "").strip() or DEFAULT_USERNAME
    return _save(d)


def is_weak():
    """True when the stored password is too short for LAN exposure.

    Recorded at set time because only a hash is kept — the length of the
    password cannot be recovered later to check it.
    """
    return bool(_load().get("weak"))


def set_password(password, *, user=None, for_lan=False):
    """Set (or change) the web credentials. Returns True if stored.

    `for_lan` applies the stricter threshold, for the caller that is about to
    expose this to the network.
    """
    password = password or ""
    floor = MIN_LAN_PASSWORD if for_lan else MIN_PASSWORD
    if len(password) < floor:
        raise ValueError(
            "the web password must be at least %d characters" % floor)
    salt = secrets.token_bytes(16)
    d = _load()
    d.update({
        "algo": "scrypt",
        "n": _N, "r": _R, "p": _P,
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(_hash(password, salt)).decode(),
        "weak": len(password) < MIN_LAN_PASSWORD,
        "set_at": time.time(),
    })
    if user is not None:
        d["username"] = (user or "").strip() or DEFAULT_USERNAME
    # Changing the password invalidates every existing session, which is the
    # only way "log everyone out" can work when sessions live in signed cookies.
    d["secret_key"] = base64.b64encode(secrets.token_bytes(32)).decode()
    return _save(d)


def clear_password():
    """Remove the password (and with it, LAN access)."""
    d = _load()
    for k in ("salt", "hash", "set_at", "weak"):
        d.pop(k, None)
    d["secret_key"] = base64.b64encode(secrets.token_bytes(32)).decode()
    return _save(d)


def verify_password(password):
    """Constant-time check. False when no password is set — an unset password
    must never mean "everyone is allowed in"."""
    d = _load()
    salt_b64, want_b64 = d.get("salt"), d.get("hash")
    if not (salt_b64 and want_b64):
        return False
    if not password:
        return False
    try:
        salt = base64.b64decode(salt_b64)
        want = base64.b64decode(want_b64)
        n = int(d.get("n") or _N)
        r = int(d.get("r") or _R)
        p = int(d.get("p") or _P)
        got = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                             dklen=len(want), maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def verify(user, password):
    """Constant-time check of both halves.

    Both sides are always evaluated — no `and` short-circuit — so the time
    taken does not tell an attacker whether it was the username that was
    wrong. Username match is case-insensitive: it is an identifier the user
    types on a phone keyboard, not a secret, and the password carries the
    security.
    """
    want = username().casefold().encode("utf-8")
    got = (user or "").strip().casefold().encode("utf-8")
    user_ok = hmac.compare_digest(got, want)
    pass_ok = verify_password(password)
    return user_ok & pass_ok


def password_stamp():
    """Short id of the CURRENT password, stored in the session at login.

    Flask reads app.secret_key once at construction, so rotating the signing key
    on a password change does not invalidate cookies until the app restarts —
    the promise "changing the password signs other devices out" was not actually
    delivered by the key rotation alone. Comparing this stamp on every request
    makes it true immediately. Derived from the stored hash, so it never reveals
    anything about the password itself.
    """
    d = _load()
    h = d.get("hash") or ""
    if not h:
        return ""
    return hashlib.sha256(h.encode("utf-8")).hexdigest()[:16]


def secret_key():
    """Persistent key for signing session cookies.

    Persisted so a restart does not log the user's phone out. Regenerated
    whenever the password changes, which is what makes a password change
    actually end existing sessions.
    """
    d = _load()
    key = d.get("secret_key")
    if key:
        try:
            return base64.b64decode(key)
        except ValueError:
            pass
    key_b = secrets.token_bytes(32)
    d["secret_key"] = base64.b64encode(key_b).decode()
    _save(d)
    return key_b


class LoginThrottle:
    """Per-address failure counter with a lockout.

    Keyed on the socket address, so one noisy device cannot lock out the rest of
    the house, and a successful login clears that address immediately.
    """

    def __init__(self, max_attempts=MAX_ATTEMPTS, lockout=LOCKOUT):
        self.max_attempts = max_attempts
        self.lockout = lockout
        self._fails = {}                  # addr -> [count, first_fail_time]
        self._lock = threading.Lock()

    def locked_for(self, addr):
        """Seconds remaining before `addr` may try again (0 when it may now)."""
        with self._lock:
            rec = self._fails.get(addr)
            if not rec or rec[0] < self.max_attempts:
                return 0.0
            left = self.lockout - (time.time() - rec[1])
            if left <= 0:
                self._fails.pop(addr, None)
                return 0.0
            return left

    def record_failure(self, addr):
        with self._lock:
            rec = self._fails.get(addr)
            if rec and rec[0] >= self.max_attempts:
                return                    # already locked; do not extend it
            if not rec or time.time() - rec[1] > self.lockout:
                self._fails[addr] = [1, time.time()]
            else:
                rec[0] += 1
                if rec[0] >= self.max_attempts:
                    rec[1] = time.time()  # lockout starts at the LAST failure
                    log.warning("web login locked out for %s after %d failures",
                                addr, rec[0])

    def record_success(self, addr):
        with self._lock:
            self._fails.pop(addr, None)
