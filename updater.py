"""Lightweight update-availability check against the GitHub Releases API.

No binary patching, no code signing, no silent install — those need infra
that doesn't exist yet. This just tells the user "hey, v1.2.0 is out, here's
the release page" so they can grab the new build manually. Result is cached
for 1 h so opening Settings doesn't hammer the API.
"""
import json
import os
import re
import time
import urllib.request
import urllib.error

import utils

# Set to your repo (owner/name). Empty disables the check cleanly.
REPO = "anthropics/IDMClone"          # placeholder — replace before shipping

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
    return {"tag": tag, "url": page}


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
                tag = cached.get("tag") or ""
                return {
                    "available": _newer(tag, current_version),
                    "version": tag,
                    "url": cached.get("url", ""),
                }
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
    return {
        "available": _newer(info["tag"], current_version),
        "version": info["tag"],
        "url": info["url"],
    }
