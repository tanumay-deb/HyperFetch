"""Password + session secret for the web UI.

This guards a server that will be reachable from every device on the network,
so the properties below are the ones that actually matter: the password is never
stored recoverably, an unset password locks everyone out rather than letting
everyone in, and guessing is throttled.
"""
import base64
import json
import os
import time

import pytest

import utils
import web_auth


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    return tmp_path


# ---- storage ---------------------------------------------------------------
def test_no_password_by_default():
    assert web_auth.has_password() is False


def test_setting_a_password_makes_it_verifiable():
    assert web_auth.set_password("correct horse") is True
    assert web_auth.has_password() is True
    assert web_auth.verify_password("correct horse") is True


def test_the_password_is_not_stored_anywhere_readable(appdata):
    web_auth.set_password("correct horse battery")
    raw = (appdata / "web_auth.json").read_text(encoding="utf-8")
    assert "correct horse battery" not in raw
    d = json.loads(raw)
    assert d["algo"] == "scrypt"
    # and the stored digest is not just an encoding of the password
    assert base64.b64decode(d["hash"]) != b"correct horse battery"


def test_a_wrong_password_is_rejected():
    web_auth.set_password("correct horse")
    for bad in ("Correct horse", "correct hors", "", "correct horse "):
        assert web_auth.verify_password(bad) is False, bad


def test_an_unset_password_lets_nobody_in():
    """The dangerous failure mode: no password meaning no check."""
    assert web_auth.verify_password("") is False
    assert web_auth.verify_password("anything") is False


def test_a_password_below_the_floor_is_refused():
    with pytest.raises(ValueError):
        web_auth.set_password("abc")
    assert web_auth.has_password() is False


def test_a_short_password_is_allowed_but_marked_weak():
    """Loopback-only, a short password is the user's own business — the person
    typing it is already sitting at this PC."""
    assert web_auth.set_password("short") is True
    assert web_auth.has_password() is True
    assert web_auth.is_weak() is True


def test_lan_holds_a_higher_floor():
    """The caller about to expose this to the network asks for the strict
    threshold, and a password stored below it stays flagged."""
    with pytest.raises(ValueError):
        web_auth.set_password("short", for_lan=True)
    assert web_auth.set_password("longenough", for_lan=True) is True
    assert web_auth.is_weak() is False


def _stored():
    with open(os.path.join(utils.app_data_dir(), "web_auth.json"),
              encoding="utf-8") as f:
        return json.load(f)


def test_the_same_password_hashes_differently_each_time():
    """A fresh salt per set, so two installs with the same password do not
    share a hash."""
    web_auth.set_password("correct horse")
    d1 = _stored()
    web_auth.set_password("correct horse")
    d2 = _stored()
    assert d1["salt"] != d2["salt"], "same salt reused, so identical hashes leak"
    assert d1["hash"] != d2["hash"]


def test_clearing_removes_access():
    web_auth.set_password("correct horse")
    web_auth.clear_password()
    assert web_auth.has_password() is False
    assert web_auth.verify_password("correct horse") is False


# ---- session secret --------------------------------------------------------
def test_the_secret_key_persists():
    """A restart must not log the user's phone out."""
    web_auth.set_password("correct horse")
    assert web_auth.secret_key() == web_auth.secret_key()


def test_changing_the_password_ends_existing_sessions():
    web_auth.set_password("correct horse")
    before = web_auth.secret_key()
    web_auth.set_password("a different one")
    assert web_auth.secret_key() != before, (
        "the signing key survived a password change, so old cookies still work")


def test_clearing_the_password_also_ends_sessions():
    web_auth.set_password("correct horse")
    before = web_auth.secret_key()
    web_auth.clear_password()
    assert web_auth.secret_key() != before


def test_a_secret_exists_even_before_any_password():
    assert len(web_auth.secret_key()) == 32


# ---- throttle --------------------------------------------------------------
def test_guessing_is_locked_out():
    t = web_auth.LoginThrottle(max_attempts=3, lockout=60)
    addr = "192.168.1.9"
    assert t.locked_for(addr) == 0
    for _ in range(3):
        t.record_failure(addr)
    assert t.locked_for(addr) > 0, "unlimited guessing against a LAN-facing login"


def test_one_device_cannot_lock_out_another():
    t = web_auth.LoginThrottle(max_attempts=2, lockout=60)
    for _ in range(5):
        t.record_failure("192.168.1.9")
    assert t.locked_for("192.168.1.10") == 0


def test_success_clears_the_counter():
    t = web_auth.LoginThrottle(max_attempts=3, lockout=60)
    addr = "192.168.1.9"
    t.record_failure(addr)
    t.record_failure(addr)
    t.record_success(addr)
    t.record_failure(addr)
    assert t.locked_for(addr) == 0, "a correct login did not reset the count"


def test_the_lockout_expires():
    t = web_auth.LoginThrottle(max_attempts=2, lockout=0.2)
    addr = "192.168.1.9"
    t.record_failure(addr)
    t.record_failure(addr)
    assert t.locked_for(addr) > 0
    time.sleep(0.25)
    assert t.locked_for(addr) == 0, "locked out permanently"


def test_more_failures_while_locked_do_not_extend_it():
    """Otherwise a device hammering the login could lock itself out forever."""
    t = web_auth.LoginThrottle(max_attempts=2, lockout=0.3)
    addr = "192.168.1.9"
    t.record_failure(addr); t.record_failure(addr)
    first = t.locked_for(addr)
    time.sleep(0.1)
    t.record_failure(addr)
    assert t.locked_for(addr) <= first
