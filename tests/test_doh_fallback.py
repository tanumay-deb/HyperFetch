"""Automatic DNS-over-HTTPS fallback: a site the provider's DNS answers with a
block page is looked up again over DoH, whatever the DNS over HTTPS setting.

Seen on Airtel (2026-09-26): eporner, xhamster and pornhub all resolved to
202.56.230.30, and the app - unlike a browser with a VPN extension or secure
DNS - failed every download there."""
import socket

import pytest

import doh

BLOCK = "202.56.230.30"
REAL = "93.184.216.34"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No test may leave the resolver patched or the cache filled."""
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setattr(doh, "_installed", False)
    monkeypatch.setattr(doh, "_enabled", False)
    monkeypatch.setattr(doh, "_auto", False)
    monkeypatch.setattr(doh, "_cache", {})


def system(answers):
    """A stand-in for the operating system's resolver."""
    def gai(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], port))]
    return gai


def test_a_blocked_site_is_looked_up_again_over_doh(monkeypatch):
    monkeypatch.setattr(doh, "_real_getaddrinfo", system({"blocked.test": BLOCK}))
    monkeypatch.setattr(doh, "_resolve", lambda host: [REAL])
    doh.set_auto(True)
    got = socket.getaddrinfo("blocked.test", 443)
    assert [r[4] for r in got] == [(REAL, 443)]


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
