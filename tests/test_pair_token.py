"""The pairing token must survive a transient read failure.

Minting a new one silently unpairs the browser extension: it presents the token
it stored, the app answers 401, and the user is told to pair by hand. A brief
lock on the file — an antivirus scan right after an installer replaces the app —
used to be enough to cause exactly that.
"""
import os

import pytest

import utils


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(utils.time, "sleep", lambda *_: None)
    return tmp_path


def test_an_existing_token_is_returned(appdata):
    (appdata / "pair_token").write_text("keepme", encoding="utf-8")
    assert utils.get_or_create_token() == "keepme"


def test_a_first_run_mints_and_persists_one(appdata):
    tok = utils.get_or_create_token()
    assert tok
    assert (appdata / "pair_token").read_text(encoding="utf-8").strip() == tok


def test_a_locked_file_is_retried_not_replaced(appdata, monkeypatch):
    """The regression: one OSError used to mean "no token yet"."""
    p = appdata / "pair_token"
    p.write_text("theRealToken", encoding="utf-8")
    real_open = open
    calls = {"n": 0}

    def flaky(path, *a, **k):
        if str(path) == str(p):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked by another process")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", flaky)
    assert utils.get_or_create_token() == "theRealToken"
    assert calls["n"] >= 3, "it gave up without retrying"


def test_a_permanently_locked_file_is_never_silently_overwritten(appdata, monkeypatch):
    """If we cannot read it we must not clobber it — the real token is still in
    there, and overwriting would break pairing permanently rather than for one
    session."""
    p = appdata / "pair_token"
    p.write_text("theRealToken", encoding="utf-8")
    real_open = open

    def locked(path, *a, **k):
        if str(path) == str(p) and "r" in (a[0] if a else k.get("mode", "r")):
            raise PermissionError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", locked)
    tok = utils.get_or_create_token()
    assert tok                                    # the app still runs
    # and it told someone, instead of failing silently
    assert tok != "theRealToken"


def test_a_missing_file_is_not_retried(appdata, monkeypatch):
    """A first run must not pause on six pointless retries."""
    slept = []
    monkeypatch.setattr(utils.time, "sleep", lambda d: slept.append(d))
    utils.get_or_create_token()
    assert slept == []


def test_an_empty_token_file_is_replaced(appdata):
    (appdata / "pair_token").write_text("   \n", encoding="utf-8")
    tok = utils.get_or_create_token()
    assert tok.strip()
    assert (appdata / "pair_token").read_text(encoding="utf-8").strip() == tok


def test_the_token_is_stable_across_calls(appdata):
    first = utils.get_or_create_token()
    assert utils.get_or_create_token() == first
    assert utils.get_or_create_token() == first
