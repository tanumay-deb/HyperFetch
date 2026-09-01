"""The pairing token must survive a transient read failure.

Minting a new one silently unpairs the browser extension: it presents the token
it stored, the app answers 401, and the user is told to pair by hand. A brief
lock on the file — an antivirus scan right after an installer replaces the app —
used to be enough to cause exactly that.
"""
import builtins
import logging
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


# ---- an unexplained re-mint has to leave a trace ----
def test_a_vanished_token_in_an_existing_dir_is_logged(tmp_path, monkeypatch, caplog):
    """A first run mints in silence, which is right. Minting because the file
    disappeared from a folder that already exists is a different thing: the
    extension is now unpaired and nothing said so."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    caplog.set_level(logging.WARNING, logger="hyperfetch.utils")
    tok = utils.get_or_create_token()
    assert tok
    assert "pair" in caplog.text.lower(), caplog.text


def test_an_empty_token_file_says_why_it_minted(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "pair_token").write_text("   ", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="hyperfetch.utils")
    assert utils.get_or_create_token()
    assert "empty" in caplog.text.lower(), caplog.text


def test_a_token_that_does_not_survive_the_write_is_reported(tmp_path, monkeypatch, caplog):
    """The failure that actually bit: the process holds a token, the file holds
    a different one, and the extension 401s on every request with nothing
    logged anywhere to explain it."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    path = tmp_path / "pair_token"

    real_open = builtins.open

    def fake_open(p, mode="r", *a, **kw):
        f = real_open(p, mode, *a, **kw)
        if str(p) == str(path) and "w" in mode:
            class _Liar:
                def write(self, _): pass          # accepts, stores nothing
                def __enter__(self): return self
                def __exit__(self, *e): f.close(); return False
            return _Liar()
        return f

    monkeypatch.setattr(builtins, "open", fake_open)
    caplog.set_level(logging.ERROR, logger="hyperfetch.utils")
    tok = utils.get_or_create_token()
    monkeypatch.undo()

    assert tok
    assert "dies with the process" in caplog.text, caplog.text


def test_an_existing_token_is_returned_without_any_noise(tmp_path, monkeypatch, caplog):
    """The normal path must stay quiet, or the warnings above mean nothing."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    (tmp_path / "pair_token").write_text("theRealToken", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="hyperfetch.utils")
    assert utils.get_or_create_token() == "theRealToken"
    assert caplog.text == ""
