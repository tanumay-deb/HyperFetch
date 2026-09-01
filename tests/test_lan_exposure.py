"""The extension/single-instance routes must never answer a non-local caller.

Their docstrings promise "Token-gated + localhost", but the localhost half was
only true because the server bound 127.0.0.1. The web UI needs the server
reachable on the LAN, so that half has to be enforced in code.

The specific hole this closes: /pair's only gate is an Origin header, which is
trustworthy from a browser and freely settable by anything else. The trusted
extension id is public — it is the Chrome Web Store URL, printed in Settings
and the Welcome dialog — so on a LAN,

    curl -H "Origin: chrome-extension://<public id>" http://<pc>:21456/pair

would have handed over the pairing token, and that token unlocks /download,
/focus and /open.
"""
import json

import pytest

from api_server import create_app, TRUSTED_EXT_IDS, is_loopback


LAN = "192.168.1.50"
GOOD_ORIGIN = "chrome-extension://" + next(iter(TRUSTED_EXT_IDS))


@pytest.fixture
def client(tmp_path):
    class _Q:
        tasks = []

        def add_task(self, *a, **k):
            pass

    app = create_app(_Q(), str(tmp_path), pending=None, token="secret-token")
    app.config["TESTING"] = True
    return app.test_client()


def _as(client, method, path, addr, **kw):
    return getattr(client, method)(path, environ_overrides={"REMOTE_ADDR": addr}, **kw)


# ---- the reported hole -----------------------------------------------------
def test_pair_does_not_hand_the_token_to_the_lan(client):
    r = _as(client, "get", "/pair", LAN, headers={"Origin": GOOD_ORIGIN})
    assert r.status_code == 403, "the pairing token leaked to a LAN caller"
    assert b"secret-token" not in r.data


def test_pair_still_works_from_this_machine(client):
    r = _as(client, "get", "/pair", "127.0.0.1", headers={"Origin": GOOD_ORIGIN})
    assert r.status_code == 200
    assert r.get_json()["token"] == "secret-token"


# ---- everything the token would have unlocked ------------------------------
@pytest.mark.parametrize("method,path,payload", [
    ("post", "/download", {"url": "https://example.test/f.zip"}),
    ("post", "/open", {"target": "magnet:?xt=urn:btih:abc"}),
    ("post", "/focus", {}),
    ("post", "/probe", {"url": "https://example.test/v.m3u8"}),
])
def test_control_routes_refuse_the_lan_even_with_the_token(client, method, path, payload):
    body = dict(payload, token="secret-token")
    r = _as(client, method, path, LAN, json=body)
    assert r.status_code == 403, (
        f"{path} answered a LAN caller holding the token — the token is not a "
        "substitute for being local")


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/download", {"url": "https://example.test/f.zip"}),
    ("post", "/open", {"target": "magnet:?xt=urn:btih:abc"}),
    ("post", "/focus", {}),
])
def test_the_same_calls_still_work_locally(client, method, path, payload):
    body = dict(payload, token="secret-token")
    r = _as(client, method, path, "127.0.0.1", json=body)
    assert r.status_code != 403, f"{path} broke for a local caller"


# ---- the helper itself -----------------------------------------------------
@pytest.mark.parametrize("addr,expect", [
    ("127.0.0.1", True),
    ("::1", True),
    ("::ffff:127.0.0.1", True),
    ("192.168.1.50", False),
    ("10.0.0.7", False),
    ("", False),
    (None, False),
])
def test_is_loopback(addr, expect):
    assert is_loopback(addr) is expect


def test_a_forwarded_header_cannot_fake_being_local(client):
    """remote_addr comes from the socket; headers are attacker-controlled."""
    r = _as(client, "get", "/pair", LAN,
            headers={"Origin": GOOD_ORIGIN,
                     "X-Forwarded-For": "127.0.0.1",
                     "X-Real-IP": "127.0.0.1"})
    assert r.status_code == 403, "a spoofed header talked its way past the gate"


def test_ping_stays_open(client):
    """/ping deliberately reveals nothing and is how the popup shows status."""
    r = _as(client, "get", "/ping", "127.0.0.1")
    assert r.status_code == 200
