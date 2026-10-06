"""What the newest release is, asked of the GitHub Releases API.

The version, its page, and - when the release has one - where its installer
is and the SHA-256 GitHub lists for it. The window does the rest
(gui2/app_update.py): says so, downloads the installer into its own list,
checks it against that hash and offers to run it. The answer is cached for an
hour so opening Settings does not hammer the API.
"""
import json
import os
import re
import time
import urllib.request
import urllib.error

import utils

# Set to your repo (owner/name). Empty disables the check cleanly.
REPO = "tanumay-deb/HyperFetch"       # GitHub repo the update check queries

CACHE_TTL = 3600           # seconds
HTTP_TIMEOUT = 6


def _cache_path():
    return os.path.join(utils.app_data_dir(), "update_check.json")


def _parse_semver(v):
    """Tolerant 'X.Y.Z' parse; trailing chars (-rc1, +meta) are ignored.
    Returns a tuple of ints, or () if unparseable."""
    if not v:
        return ()
    v = v.lstrip("vV").strip()
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", v)
    if not m:
        return ()
    return tuple(int(x or 0) for x in m.groups())


def _newer(latest, current):
    a, b = _parse_semver(latest), _parse_semver(current)
    if not a or not b:
        return False
    return a > b


def _fetch_latest(repo):
    """Hit the GitHub Releases API. Returns {tag, url} or None on any failure."""
    if not repo:
        return None
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "HyperFetch-Updater"
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None
    tag = data.get("tag_name") or ""
    page = data.get("html_url") or f"https://github.com/{repo}/releases/latest"
    info = {"tag": tag, "url": page}
    for asset in data.get("assets") or []:
        if (asset.get("name") or "").lower().endswith("setup.exe"):
            # where the installer is, and the SHA-256 GitHub lists for it:
            # what is downloaded is checked against it before it is run
            info["installer"] = asset.get("browser_download_url") or ""
            digest = asset.get("digest") or ""
            if digest.startswith("sha256:"):
                info["sha256"] = digest.split(":", 1)[1].lower()
            break
    return info


def _answer(tag, current_version, known):
    out = {"available": _newer(tag, current_version), "version": tag,
           "url": known.get("url", "")}
    for key in ("installer", "sha256"):
        if known.get(key):
            out[key] = known[key]
    return out


def file_matches(path, sha256):
    """Whether the file at `path` hashes to `sha256` (hex). False when it is
    missing or unreadable too: a file that cannot be checked is not run."""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
    except OSError:
        return False
    return h.hexdigest() == (sha256 or "").lower()


def check_for_update(current_version, force=False, repo=REPO):
    """Return a dict ``{available, version, url}`` or ``None`` when offline /
    no release info. Uses an on-disk cache so this is cheap to call.

    Pass ``force=True`` from a user-initiated "Check now" button to bypass
    the cache; the periodic background check should leave it False.
    """
    cache_p = _cache_path()
    if not force:
        try:
            with open(cache_p, encoding="utf-8") as f:
                cached = json.load(f)
            if time.time() - cached.get("checked_at", 0) < CACHE_TTL:
                return _answer(cached.get("tag") or "", current_version, cached)
        except (OSError, ValueError):
            pass

    info = _fetch_latest(repo)
    if not info:
        return None
    try:
        with open(cache_p, "w", encoding="utf-8") as f:
            json.dump({"checked_at": time.time(), **info}, f)
    except OSError:
        pass
    return _answer(info["tag"], current_version, info)
