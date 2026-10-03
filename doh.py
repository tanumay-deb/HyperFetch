"""DNS-over-HTTPS resolver.

When enabled (Settings -> Network -> DNS over HTTPS), this overrides
socket.getaddrinfo so every HTTP(S) connection the app makes (downloader, HLS,
yt-dlp — all in-process) resolves hostnames via Cloudflare DoH instead of the
system resolver. Best-effort: any DoH failure falls back to the normal resolver.

Separately, the automatic fallback (on by default) keeps the system resolver
for everything but asks DoH again when the answer is an internet provider's
block page. Indian providers block sites by DNS: Airtel answered eporner,
xhamster and pornhub with 202.56.230.30, so the app failed every download
there while a browser - on a VPN extension or its own secure DNS - worked.

The real address is not enough on its own: the same provider also resets the
TLS handshake of a connection made straight to it (unblock.py has the
measurements). So a TCP connection to a blocked site is answered here with the
address of a local relay that carries it past that filter, and the relay is
given the site's real addresses - IPv6 first, as a browser prefers, because a
site's download links can be tied to the address the browser came from.

Torrent traffic runs in the aria2 subprocess and is NOT affected.

Note: cloudflare-dns.com is resolved via the system resolver; the patched
getaddrinfo skips DoH for that lookup to avoid recursion.
"""
import logging
import socket
import threading
import time

import requests

log = logging.getLogger("hyperfetch.doh")

_DOH_URL = "https://cloudflare-dns.com/dns-query"
_DOH_HOST = "cloudflare-dns.com"            # resolved via the system resolver (no recursion)

# Addresses providers answer with in place of a blocked site: their block
# pages. Only these trigger the fallback - never 0.0.0.0 or 127.0.0.1, which
# are how people block things themselves in a hosts file. A wrong entry costs
# one extra DoH lookup, nothing more; add others as they turn up.
BLOCK_PAGES = {
    "202.56.230.30",    # Airtel, seen 2026-09-26
    "13.127.247.216",   # Airtel, seen 2026-10-03 (static.eporner.com and its HLS hosts)
}

_real_getaddrinfo = socket.getaddrinfo
_enabled = False
_auto = False
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


_RECORD = {"A": 1, "AAAA": 28}


def _query(host, rtype):
    """The addresses of one record type for host via DoH; None on failure."""
    if host == _DOH_HOST:
        return None                          # never DoH-resolve the DoH endpoint
    now = time.time()
    key = (host, rtype)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > now:
            return hit[0]
    ips, ttl = [], 300
    try:
        r = requests.get(_DOH_URL, params={"name": host, "type": rtype},
                         headers={"accept": "application/dns-json"},
                         timeout=5)
        if not r.ok:
            return None
        for ans in r.json().get("Answer", []):
            if ans.get("type") == _RECORD[rtype] and ans.get("data"):
                ips.append(ans["data"])
                ttl = min(ttl, max(30, int(ans.get("TTL", 300))))
    except Exception:
        return None
    if not ips:
        return None
    with _lock:
        _cache[key] = (ips, now + ttl)
    return ips


def _resolve(host):
    """Return a list of IPv4 strings for host via DoH, or None on failure."""
    return _query(host, "A")


def _resolve6(host):
    """The same for IPv6."""
    return _query(host, "AAAA")


def _entries(ips, port, type, proto):
    st = type or socket.SOCK_STREAM
    return [(socket.AF_INET, st, proto, "", (ip, port)) for ip in ips]


def _is_block_page(results):
    """Whether every address in a system answer is a known block page."""
    ips = {r[4][0] for r in results if len(r) > 4 and r[4]}
    return bool(ips) and ips <= BLOCK_PAGES


def _doh_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    askable = (host and host != _DOH_HOST and not _is_ip(host)
               and family in (0, socket.AF_INET))
    if _enabled and askable:
        ips = _resolve(host)
        if ips:
            return _entries(ips, port, type, proto)
    results = _real_getaddrinfo(host, port, family, type, proto, flags)
    if _auto and not _enabled and askable and _is_block_page(results):
        ips, ips6 = _resolve(host) or [], _resolve6(host) or []
        if ips or ips6:
            log.info("%s is blocked by the provider's DNS; using secure DNS for it", host)
            tcp_port = _tcp_port(port, type)
            if tcp_port:
                import unblock
                relay = unblock.address_for(host, tcp_port, list(ips6) + list(ips))
                return [(socket.AF_INET, socket.SOCK_STREAM,
                         proto or socket.IPPROTO_TCP, "", relay)]
            if ips:
                return _entries(ips, port, type, proto)
        else:
            log.warning("%s is blocked by the provider's DNS and secure DNS did not answer", host)
    return results


def _tcp_port(port, type):
    """The port number when a lookup is for a TCP connection, else 0. Only
    those can go through the relay; a UDP lookup, or one that only wants the
    address, gets the real one."""
    if type not in (0, socket.SOCK_STREAM):
        return 0
    try:
        return max(0, int(port))
    except (TypeError, ValueError):
        return 0


def _install():
    global _installed
    if not _installed:
        socket.getaddrinfo = _doh_getaddrinfo
        _installed = True


def enable(on):
    """Turn DoH on/off for every lookup. Installs the getaddrinfo override once;
    the flags gate behavior thereafter, so toggling off restores the system
    resolver path."""
    global _enabled
    _enabled = bool(on)
    if _enabled:
        _install()


def set_auto(on):
    """Turn the automatic fallback on/off: system DNS for everything, DoH only
    for a site the system answers with a provider's block page."""
    global _auto
    _auto = bool(on)
    if _auto:
        _install()
