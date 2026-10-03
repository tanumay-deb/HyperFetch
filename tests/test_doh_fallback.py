"""Automatic DNS-over-HTTPS fallback: a site the provider's DNS answers with a
block page is looked up again over DoH, whatever the DNS over HTTPS setting.

Seen on Airtel (2026-09-26): eporner, xhamster and pornhub all resolved to
202.56.230.30, and the app - unlike a browser with a VPN extension or secure
DNS - failed every download there."""
import socket

import pytest

import doh
import unblock

BLOCK = "202.56.230.30"
REAL = "93.184.216.34"
REAL6 = "2001:db8::10"
RELAY = ("127.0.0.1", 50123)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No test may leave the resolver patched or the cache filled."""
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setattr(doh, "_installed", False)
    monkeypatch.setattr(doh, "_enabled", False)
    monkeypatch.setattr(doh, "_auto", False)
    monkeypatch.setattr(doh, "_cache", {})
    monkeypatch.setattr(doh, "_resolve6", lambda host: None)


@pytest.fixture
def relay(monkeypatch):
    """Stands in for unblock.address_for and records what it was asked."""
    asked = []

    def address_for(host, port, addresses):
        asked.append((host, port, list(addresses)))
        return RELAY
    monkeypatch.setattr(unblock, "address_for", address_for)
    return asked


def system(answers):
    """A stand-in for the operating system's resolver."""
    def gai(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], port))]
    return gai


def test_a_blocked_site_is_looked_up_again_over_doh(monkeypatch, relay):
    """...and connected to through the relay: the same provider also resets
    the handshake of a connection made straight to the real address
    (unblock.py), so the real address alone was not enough."""
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: [REAL])
    doh.set_auto(True)
    got = socket.getaddrinfo("blocked.test", 443, 0, socket.SOCK_STREAM)
    assert [r[4] for r in got] == [RELAY]
    assert got[0][0] == socket.AF_INET and got[0][1] == socket.SOCK_STREAM
    assert relay == [("blocked.test", 443, [REAL])]


def test_ipv6_is_asked_for_too_and_tried_first(monkeypatch, relay):
    """A browser prefers IPv6, and a site's download links can be tied to the
    address the browser came from - eporner's carry it. Asking secure DNS for
    IPv4 only put the app on a different address than the link was made for."""
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: [REAL])
    monkeypatch.setattr(doh, "_resolve6", lambda host: [REAL6])
    doh.set_auto(True)
    socket.getaddrinfo("blocked.test", 443, 0, socket.SOCK_STREAM)
    assert relay == [("blocked.test", 443, [REAL6, REAL])]


def test_a_site_with_only_an_ipv6_answer_still_goes_through(monkeypatch, relay):
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: None)
    monkeypatch.setattr(doh, "_resolve6", lambda host: [REAL6])
    doh.set_auto(True)
    assert socket.getaddrinfo("blocked.test", 443, 0, socket.SOCK_STREAM)[0][4] == RELAY
    assert relay == [("blocked.test", 443, [REAL6])]


def test_the_providers_other_block_page_is_known(monkeypatch, relay):
    """Airtel, 2026-10-03: static.eporner.com and the HLS hosts came back as
    13.127.247.216, which the app did not know - so it connected to the block
    page itself and timed out."""
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": "13.127.247.216"}))
    monkeypatch.setattr(doh, "_resolve", lambda host: [REAL])
    doh.set_auto(True)
    assert socket.getaddrinfo("blocked.test", 443, 0, socket.SOCK_STREAM)[0][4] == RELAY


def test_a_lookup_that_is_not_for_a_tcp_connection_gets_the_real_address(monkeypatch, relay):
    """The relay carries TCP. A UDP lookup (a tracker scrape) or one without a
    port has nothing to send through it."""
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: [REAL])
    doh.set_auto(True)
    assert socket.getaddrinfo("blocked.test", 6969, 0, socket.SOCK_DGRAM)[0][4] == (REAL, 6969)
    assert socket.getaddrinfo("blocked.test", None)[0][4] == (REAL, None)
    assert relay == []


def test_a_connection_to_a_blocked_site_really_goes_through_the_relay(monkeypatch):
    """End to end with the real relay: connect by name, arrive at the site."""
    site = socket.socket()
    site.bind(("127.0.0.1", 0))
    site.listen(1)
    port = site.getsockname()[1]
    monkeypatch.setattr(unblock, "_relays", {})
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: ["127.0.0.1"])
    doh.set_auto(True)
    try:
        with socket.create_connection(("blocked.test", port), timeout=5) as c:
            conn, _ = site.accept()
            c.sendall(b"hello, site")
            got = b""
            conn.settimeout(5)
            while len(got) < 11:
                got += conn.recv(64)
            assert got == b"hello, site"
            conn.sendall(b"hello, app")
            assert c.recv(64) == b"hello, app"
            conn.close()
    finally:
        site.close()


def test_an_ordinary_answer_never_touches_doh(monkeypatch):
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"fine.test": REAL}))

    def must_not(host):
        raise AssertionError("asked DoH about an ordinary answer")
    monkeypatch.setattr(doh, "_resolve", must_not)
    doh.set_auto(True)
    assert socket.getaddrinfo("fine.test", 443)[0][4] == (REAL, 443)


def test_with_the_fallback_off_the_block_page_comes_back(monkeypatch):
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: [REAL])
    doh.set_auto(False)
    doh.enable(True)
    doh.enable(False)                   # installed, but neither mode on
    assert socket.getaddrinfo("blocked.test", 443)[0][4] == (BLOCK, 443)


def test_when_doh_fails_too_the_system_answer_stands(monkeypatch):
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: None)
    doh.set_auto(True)
    assert socket.getaddrinfo("blocked.test", 443)[0][4] == (BLOCK, 443)


def test_a_hosts_file_block_is_respected(monkeypatch):
    """0.0.0.0 and 127.0.0.1 are how people block things themselves, in their
    hosts file; that is not the provider's doing and is not second-guessed."""
    monkeypatch.setattr(doh, "_real_getaddrinfo",
                        system({"ads.test": "0.0.0.0", "local.test": "127.0.0.1"}))
    monkeypatch.setattr(doh, "_resolve", lambda host: [REAL])
    doh.set_auto(True)
    assert socket.getaddrinfo("ads.test", 443)[0][4] == ("0.0.0.0", 443)
    assert socket.getaddrinfo("local.test", 443)[0][4] == ("127.0.0.1", 443)
