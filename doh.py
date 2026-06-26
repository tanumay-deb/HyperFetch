"""DNS-over-HTTPS resolver.

When enabled (Settings -> Network -> DNS over HTTPS), this overrides
socket.getaddrinfo so every HTTP(S) connection the app makes (downloader, HLS,
yt-dlp — all in-process) resolves hostnames via Cloudflare DoH instead of the
system resolver. Best-effort: any DoH failure falls back to the normal resolver.

Torrent traffic runs in the aria2 subprocess and is NOT affected.

Note: cloudflare-dns.com is resolved via the system resolver; the patched
getaddrinfo skips DoH for that lookup to avoid recursion.
"""
import time
import socket
import threading

import requests

_DOH_URL = "https://cloudflare-dns.com/dns-query"
_DOH_HOST = "cloudflare-dns.com"            # resolved via the system resolver (no recursion)

_real_getaddrinfo = socket.getaddrinfo
_enabled = False
_installed = False
_cache = {}                                 # host -> (ips, expiry)
_lock = threading.Lock()


def _is_ip(host):
    for fam in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(fam, host)
            return True
        except OSError:
            continue
    return False


def _resolve(host):
    """Return a list of IPv4 strings for host via DoH, or None on failure."""
    if host == _DOH_HOST:
        return None                          # never DoH-resolve the DoH endpoint
    now = time.time()
    with _lock:
        hit = _cache.get(host)
        if hit and hit[1] > now:
            return hit[0]
    ips, ttl = [], 300
    try:
        r = requests.get(_DOH_URL, params={"name": host, "type": "A"},
                         headers={"accept": "application/dns-json"},
                         timeout=5)
        if not r.ok:
            return None
        for ans in r.json().get("Answer", []):
            if ans.get("type") == 1 and ans.get("data"):     # A record
                ips.append(ans["data"])
                ttl = min(ttl, max(30, int(ans.get("TTL", 300))))
    except Exception:
        return None
    if not ips:
        return None
    with _lock:
        _cache[host] = (ips, now + ttl)
    return ips


def _doh_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if (_enabled and host and host != _DOH_HOST and not _is_ip(host)
            and family in (0, socket.AF_INET)):
        ips = _resolve(host)
        if ips:
            st = type or socket.SOCK_STREAM
            return [(socket.AF_INET, st, proto, "", (ip, port)) for ip in ips]
    return _real_getaddrinfo(host, port, family, type, proto, flags)


def enable(on):
    """Turn DoH on/off. Installs the getaddrinfo override once; the flag gates
    behavior thereafter so toggling off restores the system resolver path."""
    global _enabled, _installed
    _enabled = bool(on)
    if _enabled and not _installed:
        socket.getaddrinfo = _doh_getaddrinfo
        _installed = True
