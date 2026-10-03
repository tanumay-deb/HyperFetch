"""Getting a connection past a provider's site filter.

A provider that blocks a site does it twice over. Its DNS answers the name with
a block page - doh.py looks the name up elsewhere. And a filter on the line
resets the TLS handshake when it reads the site's name in the first packet.

Measured on Airtel, 2026-10-03, against eporner's servers, with the real
addresses in hand: 31 of 60 ordinary handshakes were reset (22 of 30 over IPv4,
9 of 30 over IPv6). With the handshake's first message written to the socket in
two pieces, 0 of 120. The filter reads one packet and does not put two
together. That is also why a browser got through where the app did not: its
first message is too big for one packet. Padding ours past one packet did not
help (the name still sat in the first), so the size is not the point - the
split is.

The first message is written by the TLS library in a single call, deep under
requests, yt-dlp and the rest, so it cannot be split where it is sent. Instead
the connection goes to a relay on 127.0.0.1, which passes every byte on
unchanged except that the first write is forwarded as two. TLS stays end to end
between the library and the site: the relay only ever sees ciphertext, and the
library still checks the site's certificate against the name it asked for.

doh.py sends a connection here by answering the lookup of a blocked name with
the relay's address in place of the site's.
"""
import logging
import select
import socket
import threading
import time

log = logging.getLogger("hyperfetch.unblock")

CONNECT_TIMEOUT = 8.0     # seconds to wait on one of the site's addresses
GAP = 0.03                # between the two pieces, so they leave as two packets
CHUNK = 65536

_relays = {}              # (host, port) -> _Relay
_lock = threading.Lock()


def split_point(data, host):
    """Where to cut the first write: inside the site's name when it is there,
    so no packet carries the whole of it; otherwise just past the start."""
    name = (host or "").encode("idna") if host else b""
    at = data.find(name) if name else -1
    if at >= 0 and len(name) >= 2:
        return at + len(name) // 2
    return min(5, max(1, len(data) - 1))


class _Relay:
    """Listens on 127.0.0.1 for one site and port, and carries each connection
    to the first of the site's addresses that answers."""

    def __init__(self, host, port, addresses):
        self.host, self.port = host, port
        self.addresses = list(addresses)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))       # this machine only
        self.listener.listen(32)
        self.address = self.listener.getsockname()
        threading.Thread(target=self._accept, daemon=True,
                         name="unblock-%s" % host).start()

    def _accept(self):
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._carry, args=(client,), daemon=True,
                             name="unblock-conn").start()

    def _reach(self):
        """A connection to the site, on the first address that answers. The
        order is the caller's: IPv6 before IPv4, as a browser prefers."""
        for ip in list(self.addresses):
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect((ip, self.port))
                sock.settimeout(None)
                # the first piece has to leave at once, as its own packet
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return sock
            except OSError as e:
                log.debug("%s: %s did not answer (%s)", self.host, ip, e)
                sock.close()
        return None

    def _carry(self, client):
        site = self._reach()
        if site is None:
            client.close()
            return
        first = True
        try:
            while True:
                ready, _, _ = select.select([client, site], [], [], 60)
                for src in ready:
                    data = src.recv(CHUNK)
                    if not data:
                        return
                    if src is site:
                        client.sendall(data)
                    elif first:
                        first = False
                        at = split_point(data, self.host)
                        site.sendall(data[:at])
                        if data[at:]:
                            time.sleep(GAP)
                            site.sendall(data[at:])
                    else:
                        site.sendall(data)
        except OSError:
            pass                        # either end went away; so does the other
        finally:
            client.close()
            site.close()


def address_for(host, port, addresses):
    """The local address to connect to in place of ``host``'s own: a relay that
    carries the connection to one of ``addresses`` and gets its handshake past
    the filter. One relay per site and port, kept for the life of the app."""
    key = (host, int(port))
    with _lock:
        relay = _relays.get(key)
        if relay is None:
            relay = _relays[key] = _Relay(host, int(port), addresses)
            log.info("%s is filtered on the line; its connections go through a "
                     "local relay", host)
        else:
            relay.addresses = list(addresses)
        return relay.address
