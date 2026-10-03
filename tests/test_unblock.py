"""Getting a connection past a provider's site filter (unblock.py).

A provider that blocks a site does it twice: its DNS answers with a block page
(doh.py looks the name up elsewhere), and a filter on the line resets the TLS
handshake when it reads the site's name in the first packet. Measured on
Airtel, 2026-10-03, against eporner's servers: 31 of 60 ordinary handshakes
were reset; with the first message written to the socket in two pieces, 0 of
120. The filter reads one packet and does not put two together.

So the app's connection to such a site goes through a relay on 127.0.0.1 that
passes every byte on unchanged, except that the first write is forwarded as
two. These tests run it against servers in this process.
"""
import socket
import threading
import time

import pytest

import unblock

NAME = "blocked.example"
# shaped like a TLS ClientHello: record header, then the site's name somewhere in it
HELLO = b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03" + b"r" * 60 + NAME.encode() + b"e" * 200


class _Site(threading.Thread):
    """A server standing in for the site. It records each piece exactly as it
    arrives, and echoes everything back."""

    def __init__(self, echo=True):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.echo = echo
        self.pieces = []            # per connection: the chunks as received
        self.ended = []             # one entry per connection the site saw end
        self.start()

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            got = []
            self.pieces.append(got)
            threading.Thread(target=self._serve, args=(conn, got), daemon=True).start()

    def _serve(self, conn, got):
        try:
            with conn:
                while True:
                    try:
                        data = conn.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    got.append(data)
                    if self.echo:
                        conn.sendall(data)
        finally:
            self.ended.append(1)

    def close(self):
        # On Linux, close() alone leaves the socket listening while run() waits
        # in accept(), so a connect to the closed site still gets through.
        # shutdown() stops it. It raises on Windows, where close() is enough,
        # and on a second close.
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


@pytest.fixture
def site():
    s = _Site()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(unblock, "_relays", {})


def _through(site_port, addresses=("127.0.0.1",), name=NAME):
    where = unblock.address_for(name, site_port, list(addresses))
    return socket.create_connection(where, timeout=5)


def _read(sock, n):
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            break
        out += chunk
    return out


def _wait(cond, secs=5.0):
    deadline = time.time() + secs
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_the_first_write_reaches_the_site_in_two_pieces(site):
    with _through(site.port) as c:
        c.sendall(HELLO)
        assert _read(c, len(HELLO)) == HELLO, "the bytes were not passed on intact"
    got = site.pieces[0]
    assert len(got) >= 2, "the first message arrived whole, as one packet would"
    assert b"".join(got) == HELLO


def test_the_sites_name_never_travels_whole(site):
    """The filter looks for the name; no single piece may carry all of it."""
    with _through(site.port) as c:
        c.sendall(HELLO)
        _read(c, len(HELLO))
    assert all(NAME.encode() not in piece for piece in site.pieces[0])


def test_a_first_write_without_the_name_is_split_as_well(site):
    plain = b"GET / HTTP/1.1\r\nHost: other\r\n\r\n"
    with _through(site.port) as c:
        c.sendall(plain)
        _read(c, len(plain))
    assert len(site.pieces[0]) >= 2 and b"".join(site.pieces[0]) == plain


def test_everything_after_is_passed_on_unchanged_both_ways(site):
    body = bytes(range(256)) * 4096                      # 1 MB
    with _through(site.port) as c:
        c.sendall(HELLO)
        assert _read(c, len(HELLO)) == HELLO
        sender = threading.Thread(target=c.sendall, args=(body,), daemon=True)
        sender.start()
        assert _read(c, len(body)) == body
        sender.join(5)
    assert b"".join(site.pieces[0]) == HELLO + body


def test_the_next_address_is_tried_when_one_does_not_answer(site):
    """IPv6 first, then IPv4 - whichever the site is reachable on."""
    with _through(site.port, addresses=("127.0.0.2", "127.0.0.1")) as c:
        c.sendall(HELLO)
        assert _read(c, len(HELLO)) == HELLO


def test_when_no_address_answers_the_connection_is_closed(site):
    site.close()
    c = _through(site.port)
    c.settimeout(5)
    try:
        assert c.recv(10) == b"", "left hanging with nobody at the other end"
    except ConnectionError:
        pass
    finally:
        c.close()


def test_closing_one_end_closes_the_other(site):
    c = _through(site.port)
    c.sendall(HELLO)
    _read(c, len(HELLO))
    assert site.ended == []
    c.close()
    assert _wait(lambda: len(site.ended) == 1), "the site's side was left open"
    # and the relay goes on serving the next connection
    with _through(site.port) as again:
        again.sendall(HELLO)
        assert _read(again, len(HELLO)) == HELLO


def test_one_relay_serves_a_site_and_each_site_has_its_own(site):
    a = unblock.address_for(NAME, site.port, ["127.0.0.1"])
    assert unblock.address_for(NAME, site.port, ["127.0.0.1"]) == a
    assert unblock.address_for("other.example", site.port, ["127.0.0.1"]) != a
    assert unblock.address_for(NAME, site.port + 1, ["127.0.0.1"]) != a


def test_the_relay_listens_on_this_machine_only(site):
    host, port = unblock.address_for(NAME, site.port, ["127.0.0.1"])
    assert host == "127.0.0.1"
    relay = unblock._relays[(NAME, site.port)]
    assert relay.listener.getsockname()[0] == "127.0.0.1"


def test_new_addresses_replace_the_old_ones(site):
    unblock.address_for(NAME, site.port, ["127.0.0.2"])
    where = unblock.address_for(NAME, site.port, ["127.0.0.1"])
    with socket.create_connection(where, timeout=5) as c:
        c.sendall(HELLO)
        assert _read(c, len(HELLO)) == HELLO


def test_the_cut_is_inside_the_name_or_right_after_the_start():
    at = unblock.split_point(HELLO, NAME)
    start = HELLO.find(NAME.encode())
    assert start < at < start + len(NAME)
    assert unblock.split_point(b"0123456789", NAME) == 5
    assert unblock.split_point(b"ab", NAME) == 1
    assert unblock.split_point(b"a", NAME) == 1, "nothing to split: the whole of it goes first"
