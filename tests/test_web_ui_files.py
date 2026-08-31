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


def test_the_page_carries_all_three_dead_ends(client):
    """Off, on-but-unconfigured, and signed-out need different advice, so the
    page has to ship a pane for each — the API only reports which one."""
    body = client.get("/ui/", environ_overrides=LAN).get_data(as_text=True)
    for pane in ('id="disabled"', 'id="noPass"', 'id="login"'):
        assert pane in body, pane
    assert "Settings → Web Client" in body or "Settings &rarr; Web Client" in body         or "Web Client" in body


def test_the_login_form_asks_for_a_username(client):
    body = client.get("/ui/", environ_overrides=LAN).get_data(as_text=True)
    assert 'id="user"' in body
    assert 'autocomplete="username"' in body


def test_the_logo_is_the_app_s_own_icon(client):
    """Served from assets/ rather than copied into web/, so the page and the
    desktop window can never end up on two different logos."""
    r = client.get("/ui/logo.png", environ_overrides=LAN)
    assert r.status_code == 200
    # the 8-byte PNG signature, spelled in hex so no escape survives a round trip
    assert r.data[:8] == bytes.fromhex("89504e470d0a1a0a"), "not a PNG"

    import os
    from api_server import web_dir
    on_disk = os.path.join(os.path.dirname(web_dir()), "assets", "icon.png")
    assert r.data == open(on_disk, "rb").read()


def test_the_logo_route_is_not_a_way_into_assets(client):
    """One explicit file, not a second static folder — assets/ holds more than
    the page should be able to ask for."""
    for name in ("icon.ico", "icons/video.svg", "../api_server.py"):
        r = client.get("/ui/" + name, environ_overrides=LAN)
        assert r.status_code in (404, 400, 308), f"{name} was served"


def test_the_page_ships_its_icons_inline(client):
    """A phone on a LAN with no internet still has to draw them, and they must
    match the desktop's — so they are inlined from assets/icons/*.svg."""
    body = client.get("/ui/", environ_overrides=LAN).get_data(as_text=True)
    for glyph in ("i-video", "i-music", "i-archive", "i-magnet", "i-pause"):
        assert 'id="%s"' % glyph in body, glyph
