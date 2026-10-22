"""After a restart, a recorded daemon's number may belong to another program.

2026-10-15: the app started after the machine had been restarted, and its
record named the daemon of the night before - pid 12576, on a port that now
refused every connection. Windows had given pid 12576 to the Windows Search
host. The app took that process for its daemon, merely busy, and would not
start another for five minutes: every torrent that started in that time
paused again within two seconds, "Torrent engine unavailable (aria2 daemon
busy ...)". After the five minutes it would have hard-killed the search host
to make room.

A recorded number is the daemon only while the process holding it is an
aria2c that was already running when the record was written. Anything else -
another program, or an aria2c started since - is not ours: a new daemon is
started at once, and it is not killed.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error

import pytest

import aria2d
import utils

DAY = 24 * 3600


def _recorded(tmp_path, monkeypatch, pid=12576, written=None):
    """A record of a daemon, written `written` (default: a day ago)."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    p = tmp_path / "aria2d.json"
    p.write_text(json.dumps({"pid": pid, "port": 54913, "secret": "s"}), encoding="utf-8")
    at = time.time() - DAY if written is None else written
    os.utime(p, (at, at))
    return at


def _refused(*a, **k):
    raise urllib.error.URLError(ConnectionRefusedError(10061, "actively refused"))


def _daemon(monkeypatch, holder):
    """A daemon object whose recorded port refuses, whose pid is `holder`
    ((image, started) or None), and whose spawn and kill are counted."""
    d = aria2d.Aria2Daemon()
    monkeypatch.setattr(d, "_post", _refused)
    monkeypatch.setattr(aria2d, "_process", lambda pid: holder)
    spawned, killed = [], []

    def spawn():
        spawned.append(1)
        d.pid, d.port, d.secret = 4242, 60000, "new"
        d._last_ok = time.monotonic()
        return True

    monkeypatch.setattr(d, "_spawn", spawn)
    monkeypatch.setattr(d, "_purge", lambda: None)
    monkeypatch.setattr(d, "_reap_others", lambda: None)
    monkeypatch.setattr(aria2d, "_kill", killed.append)
    return d, spawned, killed


def test_a_number_windows_gave_another_program_is_not_the_daemon(tmp_path, monkeypatch):
    _recorded(tmp_path, monkeypatch)
    d, spawned, killed = _daemon(monkeypatch, ("SearchHost.exe", time.time() - 60))

    assert d.ensure() is True                       # no "busy", no five-minute wait
    assert spawned == [1], "no new daemon was started"
    assert killed == [], "killed a program that was never ours"


def test_an_aria2c_started_after_the_record_is_another_daemon(tmp_path, monkeypatch):
    """The server beside the desktop app runs one too, and can be given the
    number."""
    _recorded(tmp_path, monkeypatch)
    d, spawned, killed = _daemon(monkeypatch, ("aria2c.exe", time.time() - 60))

    assert d.ensure() is True
    assert spawned == [1] and killed == []


def test_the_recorded_daemon_itself_is_still_given_time(tmp_path, monkeypatch):
    """Busy is not dead: a hash check holds aria2's RPC thread for minutes."""
    written = _recorded(tmp_path, monkeypatch)
    d, spawned, killed = _daemon(monkeypatch, ("aria2c.exe", written - 5))

    with pytest.raises(aria2d.Aria2Error):
        d.ensure()
    assert spawned == [] and killed == []


def test_a_number_nobody_has_is_replaced_at_once(tmp_path, monkeypatch):
    _recorded(tmp_path, monkeypatch)
    d, spawned, killed = _daemon(monkeypatch, None)
    assert d.ensure() is True
    assert spawned == [1] and killed == []


def _kills(monkeypatch):
    """What _kill asks for: taskkill on Windows, a signal elsewhere."""
    calls = []
    monkeypatch.setattr(aria2d.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(aria2d.os, "kill", lambda pid, sig: calls.append(["kill", str(pid)]))
    return calls


def test_kill_never_touches_another_program(monkeypatch):
    calls = _kills(monkeypatch)
    monkeypatch.setattr(aria2d, "_process", lambda pid: ("SearchHost.exe", time.time()))
    aria2d._kill(12576)
    assert calls == []


def test_kill_still_stops_an_aria2c(monkeypatch):
    calls = _kills(monkeypatch)
    monkeypatch.setattr(aria2d, "_process", lambda pid: ("aria2c.exe", time.time()))
    aria2d._kill(12576)
    assert len(calls) == 1 and "12576" in calls[0]


# ---- the real processes -------------------------------------------------------------
windows = pytest.mark.skipif(sys.platform != "win32", reason="asks Windows about processes")


@windows
def test_a_process_is_named_and_dated_by_windows_itself():
    name, started = aria2d._process(os.getpid())
    assert name.lower().startswith("python")
    assert time.time() - DAY < started <= time.time()
    assert aria2d._pid_alive(os.getpid()) is False, "this process is no aria2c"
    assert aria2d._process(0x7FFFFFF0) is None


@windows
def test_with_a_real_aria2c_the_record_decides(tmp_path):
    import torrent
    exe = torrent.aria2c_path()
    if not exe:
        pytest.skip("no aria2c on this machine")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = subprocess.Popen([exe, "--enable-rpc", "--rpc-listen-all=false",
                          "--rpc-listen-port=%d" % port, "--rpc-secret=x",
                          "--dir=%s" % tmp_path, "--quiet"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        time.sleep(0.5)
        assert aria2d._pid_alive(p.pid) is True
        assert aria2d._recorded_daemon_alive(p.pid, time.time() + 5) is True
        assert aria2d._recorded_daemon_alive(p.pid, time.time() - 3600) is False, (
            "an aria2c that started after the record was taken for the recorded one")
    finally:
        p.kill()
        p.wait(5)
    assert aria2d._pid_alive(p.pid) is False
