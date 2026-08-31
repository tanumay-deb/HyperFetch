"""Login, session and lockout on the web endpoints.

These are LAN-reachable by design, unlike the extension routes, so the session
cookie is the only thing between the network and the download queue.
"""
import pytest

import utils
import web_auth
from api_server import create_app

PW = "correct horse battery"
LAN = "192.168.1.50"


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


def _post(c, path, addr=LAN, **kw):
    return c.post(path, environ_overrides={"REMOTE_ADDR": addr}, **kw)


def _get(c, path, addr=LAN, **kw):
    return c.get(path, environ_overrides={"REMOTE_ADDR": addr}, **kw)


# ---- before a password exists ---------------------------------------------
def test_session_reports_no_password(client):
    r = _get(client, "/api/session")
    assert r.get_json() == {"hasPassword": False, "authed": False}


def test_login_is_refused_until_a_password_is_set(client):
    r = _post(client, "/api/login", json={"password": "anything"})
    assert r.status_code == 403
    assert r.get_json()["code"] == "no-password"


# ---- with a password -------------------------------------------------------
def test_the_right_password_signs_you_in(client):
    web_auth.set_password(PW)
    r = _post(client, "/api/login", json={"password": PW})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _get(client, "/api/session").get_json()["authed"] is True


def test_the_wrong_password_does_not(client):
    web_auth.set_password(PW)
    r = _post(client, "/api/login", json={"password": "wrong one here"})
    assert r.status_code == 401
    assert r.get_json()["code"] == "bad-password"
    assert _get(client, "/api/session").get_json()["authed"] is False


def test_logout_ends_the_session(client):
    web_auth.set_password(PW)
    _post(client, "/api/login", json={"password": PW})
    _post(client, "/api/logout")
    assert _get(client, "/api/session").get_json()["authed"] is False


def test_the_session_survives_further_requests(client):
    """A phone should not have to log in on every poll."""
    web_auth.set_password(PW)
    _post(client, "/api/login", json={"password": PW})
    for _ in range(3):
        assert _get(client, "/api/session").get_json()["authed"] is True


# ---- lockout ---------------------------------------------------------------
def test_repeated_guesses_get_locked_out(client):
    web_auth.set_password(PW)
    for _ in range(web_auth.MAX_ATTEMPTS):
        _post(client, "/api/login", json={"password": "nope nope nope"})
    r = _post(client, "/api/login", json={"password": "nope nope nope"})
    assert r.status_code == 429, "unlimited guessing against a LAN-facing login"
    assert r.get_json()["code"] == "locked"


def test_the_lockout_is_per_device(client):
    web_auth.set_password(PW)
    for _ in range(web_auth.MAX_ATTEMPTS + 1):
        _post(client, "/api/login", json={"password": "nope nope nope"}, addr="192.168.1.9")
    r = _post(client, "/api/login", json={"password": PW}, addr="192.168.1.77")
    assert r.status_code == 200, "one bad device locked out the whole house"


# ---- cookie hardening ------------------------------------------------------
def test_the_session_cookie_is_httponly_and_samesite(client):
    web_auth.set_password(PW)
    r = _post(client, "/api/login", json={"password": PW})
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie, "page script can read the session cookie"
    assert "SameSite" in cookie, "another site could POST as the signed-in user"


def test_changing_the_password_signs_other_devices_out(client):
    """The only way to revoke access when sessions live in cookies.

    Not covered by rotating the signing key alone: Flask reads secret_key
    once at construction, so a live cookie would stay valid on the running
    app until the next restart.
    """
    web_auth.set_password(PW)
    _post(client, "/api/login", json={"password": PW})
    assert _get(client, "/api/session").get_json()["authed"] is True

    web_auth.set_password("a completely different one")

    assert _get(client, "/api/session").get_json()["authed"] is False, (
        "the old session survived a password change")


def test_clearing_the_password_signs_everyone_out(client):
    web_auth.set_password(PW)
    _post(client, "/api/login", json={"password": PW})
    web_auth.clear_password()
    assert _get(client, "/api/session").get_json()["authed"] is False
