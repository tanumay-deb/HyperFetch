"""Serving the web UI's own files.

The page is public by design — it is a login form and nothing else until a
session exists. What matters is that it cannot be used to read anything the
server was not meant to hand out.
"""
import pytest

import utils
from api_server import create_app, ALLOWED_UI_FILES


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))

    class _Q:
        tasks = []

        def add_task(self, *a, **k):
            pass

    app = create_app(_Q(), str(tmp_path), pending=None, token="tok")
    app.config["TESTING"] = True
    return app.test_client()


LAN = {"REMOTE_ADDR": "192.168.1.50"}


def test_root_redirects_to_the_ui(client):
    r = client.get("/", environ_overrides=LAN)
    assert r.status_code in (301, 302, 308)
    assert "/ui" in r.headers["Location"]


def test_the_page_loads(client):
    r = client.get("/ui/", environ_overrides=LAN)
    assert r.status_code == 200
    assert b"HyperFetch" in r.data
    assert b'id="loginForm"' in r.data


@pytest.mark.parametrize("name", sorted(ALLOWED_UI_FILES))
def test_each_allowed_asset_is_served(client, name):
    r = client.get("/ui/" + name, environ_overrides=LAN)
    assert r.status_code == 200, name
    assert r.data, name


@pytest.mark.parametrize("name", [
    "../api_server.py",
    "../../CLAUDE.md",
    "..%2fapi_server.py",
    "web_auth.py",
    "settings.json",
    "../web/app.js",
])
def test_it_will_not_serve_anything_else(client, name):
    """An allow-list, not "whatever is in the folder" — so neither a traversal
    nor a stray file dropped into web/ becomes a public URL."""
    r = client.get("/ui/" + name, environ_overrides=LAN)
    assert r.status_code in (404, 400, 308), f"{name} was served ({r.status_code})"
    assert b"HYPERFETCH_TOKEN" not in r.data
    assert b"scrypt" not in r.data


def test_the_page_carries_no_secret_values(client, tmp_path):
    """Served before login, so it must not embed anything worth having.

    Checks for the actual secrets rather than the WORD "password" — a login
    form is full of that word by necessity (label, placeholder, autocomplete)
    and asserting its absence tests nothing.
    """
    import web_auth
    web_auth.set_password("correct horse battery")
    body = client.get("/ui/", environ_overrides=LAN).get_data(as_text=True)

    stored = open(str(tmp_path / "web_auth.json"), encoding="utf-8").read()
    import json
    d = json.loads(stored)
    for secret in (d["hash"], d["salt"], d["secret_key"], "tok",
                   "correct horse battery"):
        assert secret not in body, "a secret was embedded in the page"

def test_the_ui_does_not_require_a_session(client):
    """A login form behind a login is not much use."""
    assert client.get("/ui/", environ_overrides=LAN).status_code == 200
