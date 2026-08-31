"""Opening the web client to the local network.

The one switch that stops this being a local-only app, so every test here is
about it failing closed: a weak password, a missing password, or the client
being off must all leave the server on 127.0.0.1.
"""
import pytest

import api_server
import utils
import web_auth
from gui2.app_settings import SettingsMixin


STRONG = "correct horse battery"
WEAK = "admin"


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    return tmp_path


class _App(SettingsMixin):
    def __init__(self):
        self.toasts = []

    def _web_toast(self, kind, title, msg):
        self.toasts.append((kind, title, msg))


# ---- the gate itself -------------------------------------------------------
def test_loopback_is_the_default():
    assert web_auth.lan_allowed() is False
    assert api_server.bind_host() == "127.0.0.1"


def test_a_strong_password_opens_it():
    web_auth.set_password(STRONG, user="admin")
    web_auth.set_enabled(True)
    web_auth.set_lan(True)
    assert web_auth.lan_allowed() is True
    assert api_server.bind_host() == "0.0.0.0"


def test_a_weak_password_keeps_it_shut():
    """A password accepted under the loopback floor must not become the only
    thing between the network and the download queue."""
    web_auth.set_password(WEAK, user="admin")
    web_auth.set_enabled(True)
    web_auth.set_lan(True)                      # even with the flag set
    assert web_auth.is_weak() is True
    assert web_auth.lan_allowed() is False
    assert api_server.bind_host() == "127.0.0.1"


def test_no_password_keeps_it_shut():
    web_auth.set_enabled(True)
    web_auth.set_lan(True)
    assert web_auth.lan_allowed() is False
    assert api_server.bind_host() == "127.0.0.1"


def test_switching_the_client_off_closes_the_network_too():
    web_auth.set_password(STRONG, user="admin")
    web_auth.set_lan(True)
    web_auth.set_enabled(False)
    assert web_auth.lan_allowed() is False
    assert api_server.bind_host() == "127.0.0.1"


def test_an_unreadable_setting_falls_back_to_loopback(monkeypatch):
    """If the check itself fails, the safe answer is the closed one."""
    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr(web_auth, "lan_allowed", boom)
    assert api_server.bind_host() == "127.0.0.1"


def test_upgrading_the_password_clears_the_weak_flag():
    web_auth.set_password(WEAK, user="admin")
    web_auth.set_enabled(True)
    web_auth.set_lan(True)
    assert api_server.bind_host() == "127.0.0.1"
    web_auth.set_password(STRONG, user="admin")
    assert api_server.bind_host() == "0.0.0.0"


# ---- what the user is told -------------------------------------------------
def test_the_refusal_says_why():
    """"The toggle did nothing" is the worst possible answer here."""
    assert "password" in web_auth.lan_refusal().lower()
    web_auth.set_password(WEAK, user="admin")
    assert str(web_auth.MIN_LAN_PASSWORD) in web_auth.lan_refusal()
    web_auth.set_password(STRONG, user="admin")
    assert web_auth.lan_refusal() == ""


def test_settings_refuses_and_reports_a_weak_password():
    app = _App()
    v = {"web_enabled": True, "web_username": "admin",
         "web_password": WEAK, "web_lan": True}
    app._apply_web_settings(v)
    assert web_auth.lan_allowed() is False
    assert v["web_lan"] is False, "the saved value must match reality"
    assert any(t[0] == "error" for t in app.toasts), "the user was not told"


def test_settings_turns_it_on_with_a_strong_password():
    app = _App()
    v = {"web_enabled": True, "web_username": "admin",
         "web_password": STRONG, "web_lan": True}
    app._apply_web_settings(v)
    assert web_auth.lan_allowed() is True
    assert v["web_lan"] is True
    assert any("Restart" in t[1] for t in app.toasts), "no restart notice"


def test_turning_it_off_is_reported_too():
    web_auth.set_password(STRONG, user="admin")
    web_auth.set_enabled(True)
    web_auth.set_lan(True)
    app = _App()
    app._apply_web_settings({"web_enabled": True, "web_username": "admin",
                             "web_password": "", "web_lan": False})
    assert web_auth.lan_allowed() is False
    assert any("Restart" in t[1] for t in app.toasts)


def test_the_extension_routes_stay_loopback_only_on_the_lan():
    """Opening the page to the network must not open /download with it — that
    is gated by the pairing token AND by being local, and only the second one
    is meaningful once the port answers the LAN."""
    web_auth.set_password(STRONG, user="admin")
    web_auth.set_enabled(True)
    web_auth.set_lan(True)
    assert api_server.bind_host() == "0.0.0.0"

    class _Q:
        tasks = []

        def add_task(self, *a, **k):
            pass

    app = api_server.create_app(_Q(), ".", pending=None, token="tok")
    app.config["TESTING"] = True
    c = app.test_client()
    # Each route's real method, so a 405 cannot stand in for the guard.
    for method, path in (("post", "/download"), ("post", "/probe"),
                         ("get", "/pair"), ("post", "/focus"), ("post", "/open")):
        r = getattr(c, method)(
            path, json={"url": "https://example.test/x", "token": "tok"},
            headers={"X-HyperFetch-Token": "tok"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.77"})
        assert r.status_code == 403, f"{path} answered a LAN caller ({r.status_code})"
        assert "token" not in r.get_data(as_text=True).lower(),             f"{path} leaked something to a LAN caller"
