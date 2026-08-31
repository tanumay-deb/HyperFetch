"""Accounts for the public users site.

Deliberately separate from ``web_auth``, which holds the single admin login for
the LAN control page. Three reasons they do not share a store:

- Different exposure. The control page answers this network; this one answers
  the internet through a tunnel. A leak on one must not be a leak on the other.
- Different session cookies. Two Flask apps on two ports with two secret keys,
  so a session minted here can never authenticate against control.
- Different write patterns. One file written by two modules is a race waiting
  to happen.

Stored in ``site_auth.json`` in the app-data dir, 0600.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time

import utils

log = logging.getLogger("hyperfetch.siteauth")

# Same scrypt cost as web_auth: ~100ms, slow enough that guessing over the
# internet is hopeless, fast enough that a phone login feels instant.
_N, _R, _P = 2 ** 15, 8, 1
_DKLEN = 32

MIN_PASSWORD = 8          # this one faces the internet, so no loopback discount
MAX_USERS = 200

# Usernames become folder names under the download directory, so they are
# checked against what a filesystem will accept rather than what looks tidy.
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,30}[A-Za-z0-9]$")
_RESERVED = {
    "con", "prn", "aux", "nul", "clock$",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
    # ours, so a user cannot sit on a path the app already means something by
    "admin", "administrator", "root", "system", "hyperfetch",
}

# Registration throttle. The signup form is reachable by anyone who finds the
# URL, so it gets the same treatment as the login form.
MAX_ATTEMPTS = 8
LOCKOUT = 300.0

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"        # reserved for the approval flow, unused today
STATUS_DISABLED = "disabled"

_lock = threading.Lock()


def _path():
    return os.path.join(utils.app_data_dir(), "site_auth.json")


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    """Write 0600. Returns True on success.

    Never swallows a failure quietly: an account the user believes they created,
    which was never stored, is worse than a visible error.
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
            pass                      # best effort; Windows ACLs differ
        return True
    except OSError as e:
        log.error("could not save site accounts (%s) — the change was NOT kept", e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                          n=_N, r=_R, p=_P, dklen=_DKLEN,
                          maxmem=64 * 1024 * 1024)


# ---------------------------------------------------------------- site switch
def is_enabled():
    """Whether the site answers at all.

    Separate from the tunnel: turning this off leaves the tunnel up so visitors
    get a "temporarily unavailable" page instead of a connection error, which
    reads as maintenance rather than a broken link.
    """
    return bool(_load().get("enabled"))


def set_enabled(on):
    with _lock:
        d = _load()
        d["enabled"] = bool(on)
        return _save(d)


# ---------------------------------------------------------------- invite code
def invite_code():
    """The code new accounts must present. Created on first read.

    Registration is open to anyone who finds the URL, and the URL is public
    HTTPS that does get crawled — so without this, strangers could queue
    downloads onto this machine.
    """
    with _lock:
        d = _load()
        code = d.get("invite_code")
        if not code:
            code = secrets.token_urlsafe(9)
            d["invite_code"] = code
            _save(d)
        return code


def set_invite_code(code):
    code = (code or "").strip()
    if len(code) < 6:
        raise ValueError("the invite code must be at least 6 characters")
    with _lock:
        d = _load()
        d["invite_code"] = code
        return _save(d)


def rotate_invite_code():
    with _lock:
        d = _load()
        d["invite_code"] = secrets.token_urlsafe(9)
        _save(d)
        return d["invite_code"]


def check_invite(code):
    return hmac.compare_digest((code or "").strip(), invite_code())


# ------------------------------------------------------------------ usernames
def normalise_username(name):
    """The stored form. Case-insensitive, because people retype these on a
    phone keyboard that likes to capitalise the first letter."""
    return (name or "").strip().casefold()


def username_error(name):
    """Why this username cannot be used, or "" when it can.

    Rejected rather than silently rewritten: a user who typed `my name` and got
    an account called `my_name` has been handed a login they will not remember.
    """
    raw = (name or "").strip()
    if not raw:
        return "Pick a username."
    if len(raw) < 3 or len(raw) > 32:
        return "Usernames are 3 to 32 characters."
    if not USERNAME_RE.match(raw):
        return ("Use letters, numbers, dots, dashes and underscores, "
                "starting and ending with a letter or number.")
    low = normalise_username(raw)
    if low in _RESERVED or low.split(".")[0] in _RESERVED:
        return "That username is reserved."
    return ""


# ---------------------------------------------------------------------- users
def _users(d=None):
    d = _load() if d is None else d
    users = d.get("users")
    return users if isinstance(users, list) else []


def list_users():
    """Every account, without the secret material."""
    return [public(u) for u in _users()]


def public(u):
    """The safe view of an account — never the salt or the hash."""
    return {
        "id": u.get("id", ""),
        "username": u.get("username", ""),
        "email": u.get("email", ""),
        "provider": u.get("provider", "local"),
        "status": u.get("status", STATUS_ACTIVE),
        "plan": u.get("plan", "unlimited"),
        "created": float(u.get("created", 0) or 0),
    }


def get_user(user_id):
    return next((u for u in _users() if u.get("id") == user_id), None)


def find_user(name_or_email):
    """Look up by username or email, both case-insensitively."""
    key = normalise_username(name_or_email)
    if not key:
        return None
    for u in _users():
        if u.get("username") == key or normalise_username(u.get("email")) == key:
            return u
    return None


def create_user(username, email, password, code):
    """Register an account. Returns the public view.

    Raises ValueError with a message meant for the person reading it.
    """
    if not check_invite(code):
        raise ValueError("That invite code is not valid.")
    why = username_error(username)
    if why:
        raise ValueError(why)
    if len(password or "") < MIN_PASSWORD:
        raise ValueError("Passwords are at least %d characters." % MIN_PASSWORD)

    low = normalise_username(username)
    with _lock:
        d = _load()
        users = _users(d)
        if len(users) >= MAX_USERS:
            raise ValueError("This site is not accepting new accounts.")
        for u in users:
            if u.get("username") == low:
                raise ValueError("That username is taken.")
            if email and normalise_username(u.get("email")) == normalise_username(email):
                raise ValueError("There is already an account for that email.")

        salt = secrets.token_bytes(16)
        user = {
            "id": "u_" + secrets.token_hex(8),
            "username": low,
            "email": (email or "").strip(),
            # Federated accounts will carry provider="google" and no password,
            # so adding Google sign-in later does not need a migration.
            "provider": "local",
            "algo": "scrypt", "n": _N, "r": _R, "p": _P,
            "salt": base64.b64encode(salt).decode(),
            "hash": base64.b64encode(_hash(password, salt)).decode(),
            # Every account is usable immediately today. The field exists so
            # switching to admin approval later is a default change, not a
            # schema change.
            "status": STATUS_ACTIVE,
            "plan": "unlimited",
            "created": time.time(),
        }
        users.append(user)
        d["users"] = users
        if not _save(d):
            raise ValueError("Could not save the account. Try again.")
        log.info("site account created: %s", low)
        return public(user)


def verify(name_or_email, password):
    """Constant-time password check. Returns the public user, or None.

    A disabled account fails here rather than at some later route, so there is
    one place where "may this person in" is decided.
    """
    u = find_user(name_or_email)
    if not u:
        # Hash anyway, so a wrong username does not answer faster than a wrong
        # password and reveal which usernames exist.
        _hash(password or "", b"decoy-salt-000000")
        return None
    if u.get("status") != STATUS_ACTIVE or u.get("provider") != "local":
        _hash(password or "", b"decoy-salt-000000")
        return None
    try:
        salt = base64.b64decode(u.get("salt") or "")
        want = base64.b64decode(u.get("hash") or "")
        if not (salt and want and password):
            return None
        got = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                             n=int(u.get("n") or _N), r=int(u.get("r") or _R),
                             p=int(u.get("p") or _P), dklen=len(want),
                             maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return None
    return public(u) if hmac.compare_digest(got, want) else None


def stamp(user_id):
    """Short id of an account's CURRENT password, stored in its session.

    Flask reads secret_key once at construction, so rotating it does not
    invalidate live cookies until restart. Comparing this per request makes
    "reset a password and that person is signed out" true immediately — and
    only for that person, which a shared key could not express.
    """
    u = get_user(user_id)
    if not u:
        return ""
    h = u.get("hash") or ""
    marker = h + "|" + str(u.get("status", ""))
    return hashlib.sha256(marker.encode("utf-8")).hexdigest()[:16]


def set_password(user_id, password):
    if len(password or "") < MIN_PASSWORD:
        raise ValueError("Passwords are at least %d characters." % MIN_PASSWORD)
    with _lock:
        d = _load()
        users = _users(d)
        u = next((x for x in users if x.get("id") == user_id), None)
        if not u:
            return False
        salt = secrets.token_bytes(16)
        u.update({"algo": "scrypt", "n": _N, "r": _R, "p": _P,
                  "salt": base64.b64encode(salt).decode(),
                  "hash": base64.b64encode(_hash(password, salt)).decode()})
        d["users"] = users
        return _save(d)


def set_status(user_id, status):
    if status not in (STATUS_ACTIVE, STATUS_PENDING, STATUS_DISABLED):
        raise ValueError("unknown status")
    with _lock:
        d = _load()
        users = _users(d)
        u = next((x for x in users if x.get("id") == user_id), None)
        if not u:
            return False
        u["status"] = status
        d["users"] = users
        return _save(d)


def delete_user(user_id):
    """Remove an account. Its files are deliberately left on disk — silently
    deleting gigabytes because a login was removed is not a good surprise."""
    with _lock:
        d = _load()
        users = _users(d)
        keep = [u for u in users if u.get("id") != user_id]
        if len(keep) == len(users):
            return False
        d["users"] = keep
        return _save(d)


def secret_key():
    """Signing key for this site's session cookies, distinct from control's."""
    with _lock:
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


class Throttle:
    """Failure counter with a lockout, keyed on whatever the caller chooses.

    Used twice on the login route: once per address so one noisy device cannot
    lock out the house, and once per account so guessing at one username from
    many addresses still runs into a wall.
    """

    def __init__(self, max_attempts=MAX_ATTEMPTS, lockout=LOCKOUT):
        self.max_attempts = max_attempts
        self.lockout = lockout
        self._fails = {}
        self._lock = threading.Lock()

    def locked_for(self, key):
        with self._lock:
            rec = self._fails.get(key)
            if not rec or rec[0] < self.max_attempts:
                return 0.0
            left = self.lockout - (time.time() - rec[1])
            if left <= 0:
                self._fails.pop(key, None)
                return 0.0
            return left

    def record_failure(self, key):
        with self._lock:
            rec = self._fails.get(key)
            if rec and rec[0] >= self.max_attempts:
                return                      # already locked; do not extend it
            if not rec or time.time() - rec[1] > self.lockout:
                self._fails[key] = [1, time.time()]
            else:
                rec[0] += 1
                if rec[0] >= self.max_attempts:
                    rec[1] = time.time()    # lockout starts at the LAST failure
                    log.warning("site login locked out for %s", key)

    def record_success(self, key):
        with self._lock:
            self._fails.pop(key, None)
