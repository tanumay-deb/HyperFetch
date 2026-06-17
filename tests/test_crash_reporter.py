"""Crash reporter: captures uncaught exceptions to %APPDATA% without raising
or hitting the network."""
import json
import os
import sys
import threading

import pytest

import crash_reporter


@pytest.fixture
def crash_home(tmp_path, monkeypatch):
    """Point app_data_dir at a tmp_path so crash JSONs don't pollute %APPDATA%."""
    monkeypatch.setattr(crash_reporter.utils, "app_data_dir", lambda: str(tmp_path))
    yield tmp_path


def test_install_writes_main_thread_crash(crash_home):
    """sys.excepthook captures an uncaught main-thread exception."""
    crash_reporter.install("9.9.9")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    reports = crash_reporter.unsent_reports()
    assert len(reports) == 1
    with open(reports[0], encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["exc_type"] == "RuntimeError"
    assert payload["version"] == "9.9.9"
    assert payload["source"] == "main"
    assert "boom" in payload["traceback"]


def test_install_writes_worker_thread_crash(crash_home, monkeypatch):
    """threading.excepthook captures a crash inside a worker thread."""
    # neutralize the chained prev hook (pytest installs its own that would
    # turn the deliberate test exception into a test failure)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    crash_reporter.install("9.9.9")

    def crash():
        raise ValueError("thread go boom")

    t = threading.Thread(target=crash, name="bad-worker")
    t.start()
    t.join()
    reports = crash_reporter.unsent_reports()
    assert len(reports) == 1
    with open(reports[0], encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["exc_type"] == "ValueError"
    assert "bad-worker" in payload["source"]


def test_keyboard_interrupt_is_not_reported(crash_home):
    """Ctrl-C / SystemExit aren't crashes — don't pollute the folder."""
    crash_reporter.install("9.9.9")
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        try:
            sys.excepthook(*sys.exc_info())
        except KeyboardInterrupt:
            pass  # the prev hook re-raises; our hook ran before that
    assert crash_reporter.unsent_reports() == []


def test_report_excludes_url_and_path_data(crash_home):
    """Crash JSON must not carry URLs, headers, or anything PII-shaped."""
    crash_reporter.install("9.9.9")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    payload = json.load(open(crash_reporter.unsent_reports()[0], encoding="utf-8"))
    keys = set(payload.keys())
    forbidden = {"url", "headers", "cookies", "save_path", "task"}
    assert keys & forbidden == set(), f"crash dump leaked: {keys & forbidden}"
