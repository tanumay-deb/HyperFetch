"""No download addresses in the log or in crash reports (2026-11-04, list #4).

The privacy policy: the log holds download addresses only with Debug logging
switched on, and crash reports hold none. Error lines carried them whole - a
failed connection its path and query, sign-in token included, a yt-dlp
failure the page - and a crash report whatever text the error had.
"""
import json
import logging
import os
import sys

import pytest

import crash_reporter
import utils

PAGE = "https://videos.example.com/watch?v=Zx9Secret&list=PLprivate"
POOL = ("HTTPSConnectionPool(host='cdn.example.com', port=443): Max retries exceeded "
        "with url: /files/report.zip?token=s3cr3t (Caused by NewConnectionError('x'))")
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Some.Film.2026&tr=udp://tr.example:1337/announce"
SECRETS = ("Zx9Secret", "PLprivate", "report.zip", "s3cr3t", "Some.Film", "a" * 40, "watch?")


@pytest.mark.parametrize("text,kept", [
    ("yt-dlp failed: %s — ERROR: [generic] Zx9Secret: Sign in to watch" % PAGE,
     "yt-dlp failed: https://videos.example.com/… — ERROR: [generic] Sign in to watch"),
    (POOL, "HTTPSConnectionPool(host='cdn.example.com', port=443): Max retries exceeded "
           "with url: … (Caused by NewConnectionError('x'))"),
    ("could not fetch %s now" % MAGNET, "could not fetch magnet:… now"),
    ("proxy http://user:hunter2@10.0.0.1:3128/x refused", "proxy http://10.0.0.1:3128/… refused"),
    ("[Errno 2] No such file: 'C:/dl/a.bin', at 12:30", "[Errno 2] No such file: 'C:/dl/a.bin', at 12:30"),
])
def test_an_address_is_cut_down_to_its_site(text, kept):
    assert utils.without_addresses(text) == kept


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """The app's log, set up as the app does, in this test's app-data."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    root = logging.getLogger("hyperfetch")
    was = (root.level, root.propagate, list(root.handlers))

    def read():
        for h in root.handlers:
            h.flush()
        with open(tmp_path / "hyperfetch.log", encoding="utf-8") as f:
            return f.read()

    yield read
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(was[0])
    root.propagate = was[1]
    for h in was[2]:
        root.addHandler(h)


def _fail(log):
    log.error("yt-dlp failed: %s — %s", PAGE, "ERROR: [generic] Zx9Secret: Sign in")
    log.warning("could not fetch %s", MAGNET)
    try:
        raise ConnectionError(POOL)
    except ConnectionError:
        log.exception("the download of %s stopped", PAGE)


def test_the_log_names_the_site_and_not_the_address(log_file):
    utils.setup_logging(debug=False)
    _fail(utils.get_logger("downloader"))
    text = log_file()
    assert "videos.example.com" in text and "cdn.example.com" in text
    assert "Traceback" in text and "ConnectionError" in text
    for secret in SECRETS:
        assert secret not in text, secret


def test_with_debug_logging_on_it_has_them_as_the_policy_says(log_file):
    utils.setup_logging(debug=True)
    _fail(utils.get_logger("downloader"))
    text = log_file()
    assert PAGE in text and "token=s3cr3t" in text


def test_a_crash_report_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_reporter.utils, "app_data_dir", lambda: str(tmp_path))
    try:
        raise RuntimeError("could not read %s (%s)" % (PAGE, POOL))
    except RuntimeError:
        crash_reporter._write_report(*sys.exc_info(), "main", "9.9.9")
    (name,) = os.listdir(tmp_path / "crashes")
    with open(tmp_path / "crashes" / name, encoding="utf-8") as f:
        text = f.read()
    report = json.loads(text)
    assert "videos.example.com" in report["exc_msg"] and "RuntimeError" in report["traceback"]
    for secret in SECRETS:
        assert secret not in text, secret
