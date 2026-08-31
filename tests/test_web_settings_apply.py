"""Applying the Settings -> Web Client page.

The dialog hands `_apply_settings` a plain dict, and everything left in that
dict is written to settings.json. So the one thing worth pinning down here is
that the password is taken OUT of it on the way past.
"""
import json
import os

import pytest

import utils
import web_auth
from gui2.app_settings import SettingsMixin


PW = "correct horse battery"


class _App(SettingsMixin):
    """Just enough of the window for _apply_web_settings to run."""

    def __init__(self):
        self.toasts = []

    def _web_toast(self, kind, title, msg):
        self.toasts.append((kind, title, msg))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    return _App()


def test_the_password_never_reaches_the_settings_dict(app, tmp_path):
    """settings.json is rewritten constantly and has no business holding a
    secret. The hash goes to web_auth.json instead."""
    v = {"web_enabled": True, "web_username": "admin", "web_password": PW}
    app._apply_web_settings(v)

    assert "web_password" not in v
    assert PW not in json.dumps(v)
    assert web_auth.verify("admin", PW) is True

    stored = open(os.path.join(str(tmp_path), "web_auth.json"),
                  encoding="utf-8").read()
    assert PW not in stored, "the password was stored in the clear"


def test_a_blank_password_keeps_the_existing_one(app):
    """The field cannot be pre-filled — only a hash is kept — so blank has to
    mean "leave it alone" rather than "clear it"."""
    web_auth.set_password(PW, user="admin")
    app._apply_web_settings({"web_enabled": True, "web_username": "admin",
                             "web_password": ""})
    assert web_auth.verify("admin", PW) is True
    assert web_auth.is_enabled() is True


def test_the_username_can_change_on_its_own(app):
    web_auth.set_password(PW, user="admin")
    app._apply_web_settings({"web_enabled": True, "web_username": "tanumay",
                             "web_password": ""})
    assert web_auth.username() == "tanumay"
    assert web_auth.verify("tanumay", PW) is True
    assert web_auth.verify("admin", PW) is False


def test_it_will_not_switch_on_without_a_password(app):
    """Otherwise "enabled" would promise access that cannot be granted, and
    the page would show a login form nothing can satisfy."""
    v = {"web_enabled": True, "web_username": "admin", "web_password": ""}
    app._apply_web_settings(v)
    assert web_auth.is_enabled() is False
    assert v["web_enabled"] is False, "the saved value must match reality"
    assert app.toasts, "the user was not told why"


def test_a_rejected_password_leaves_the_old_one_working(app):
    """A typo that is too short must not quietly drop the credentials."""
    web_auth.set_password(PW, user="admin")
    app._apply_web_settings({"web_enabled": True, "web_username": "admin",
                             "web_password": "ab"})
    assert web_auth.verify("admin", PW) is True
    assert app.toasts and app.toasts[0][0] == "error"


def test_a_short_password_is_accepted_here(app):
    """Loopback-only today, so the low floor applies — this is what lets a
    user pick something short for their own machine."""
    app._apply_web_settings({"web_enabled": True, "web_username": "admin",
                             "web_password": "admin"})
    assert web_auth.verify("admin", "admin") is True
    assert web_auth.is_enabled() is True
    assert web_auth.is_weak() is True, "it must still be flagged for LAN"
