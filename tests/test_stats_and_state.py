"""Sidebar stats (peers vs seeders) and the aria2 daemon's state record."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import aria2d
import utils
from gui2.sidebar import Sidebar


def _app():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------- stats card
def test_seeders_are_shown_beside_connections():
    """49 connected peers next to 0 Mb/s is normal in a swarm where nobody has
    a complete copy — seeders are what predict throughput."""
    _app()
    s = Sidebar()
    s.set_stats(2_000_000, 49, 1)
    assert s.lbl_conns.text() == "49 / 1"
    assert "seeder" in s.stats.toolTip()


def test_an_http_only_list_shows_no_seeder_figure():
    """A permanent 0 seeders on a list with no torrents explains nothing."""
    _app()
    s = Sidebar()
    s.set_stats(2_000_000, 8, None)
    assert s.lbl_conns.text() == "8"
    assert "seeder" not in s.stats.toolTip()


def test_zero_seeders_is_still_reported():
    """The case worth surfacing: peers connected, nothing to get from them."""
    _app()
    s = Sidebar()
    s.set_stats(0, 27, 0)
    assert s.lbl_conns.text() == "27 / 0"


# ------------------------------------------------------- daemon state record
def test_the_state_record_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    d = aria2d.Aria2Daemon()
    d.pid, d.port, d.secret = 111, 6800, "s"
    assert d._save_state() is True
    saved = json.loads((tmp_path / "aria2d.json").read_text(encoding="utf-8"))
    assert saved == {"pid": 111, "port": 6800, "secret": "s"}


def test_a_failed_write_is_retried_then_reported(tmp_path, monkeypatch, caplog):
    """aria2d.json is the ONLY way a later run finds this daemon. Swallowing
    the error leaves a record pointing at a dead daemon — which is how an
    orphaned aria2c ended up running unattached on a real machine."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(aria2d.time, "sleep", lambda *_: None)
    d = aria2d.Aria2Daemon()
    d.pid, d.port, d.secret = 222, 6801, "s"

    attempts = {"n": 0}
    real_open = open

    def locked(path, *a, **k):
        if str(path).endswith("aria2d.json") and "w" in (a[0] if a else k.get("mode", "")):
            attempts["n"] += 1
            raise PermissionError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", locked)
    with caplog.at_level("ERROR"):
        assert d._save_state() is False
    assert attempts["n"] > 1, "gave up without retrying"
    assert any("could not record" in r.message for r in caplog.records)


def test_spawn_probes_the_new_port_not_a_cached_liveness(tmp_path, monkeypatch):
    """alive() trusts a probe from up to LIVENESS_TTL ago, and that probe
    belonged to the PREVIOUS daemon. Using it here would declare a fresh spawn
    up without anything ever contacting it, and record it before it listened."""
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    d = aria2d.Aria2Daemon()
    d._last_ok = aria2d.time.monotonic()          # a very recent "success"

    class _P:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(aria2d, "_aria2c_path", lambda: "aria2c.exe")
    monkeypatch.setattr(aria2d.subprocess, "Popen", lambda *a, **k: _P())
    monkeypatch.setattr(aria2d.Aria2Daemon, "_options", lambda self: [])
    probed = []
    monkeypatch.setattr(d, "_post",
                        lambda port, secret, m, p, **k: probed.append(port) or {})

    assert d._spawn() is True
    assert probed, "recorded the daemon without ever contacting it"
    assert probed[0] == d.port
