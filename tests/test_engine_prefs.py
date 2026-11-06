"""Seeding, preview priority, and a graceful stop for the legacy engine."""
import subprocess
import sys

import pytest

import aria2d
import torrent
import utils


def _opts(monkeypatch, **settings):
    for k, v in settings.items():
        monkeypatch.setattr(utils, k, v, raising=False)
    return torrent.preference_opts()


def test_seeding_is_off_by_default(monkeypatch):
    """The app has always stopped dead on completion; that stays the default."""
    assert "--seed-time=0" in _opts(monkeypatch, SEED_ENABLED=False)


def test_a_share_ratio_is_passed_when_seeding_is_on(monkeypatch):
    o = _opts(monkeypatch, SEED_ENABLED=True, SEED_RATIO=2.0, SEED_MINUTES=0)
    assert "--seed-ratio=2" in o
    assert "--seed-time=0" not in o          # would defeat the whole thing


def test_a_time_limit_can_be_combined_with_the_ratio(monkeypatch):
    o = _opts(monkeypatch, SEED_ENABLED=True, SEED_RATIO=1.5, SEED_MINUTES=30)
    assert "--seed-ratio=1.5" in o and "--seed-time=30" in o


def test_seeding_with_no_limits_does_not_become_seed_forever(monkeypatch):
    """aria2 reads --seed-ratio=0 as 'seed indefinitely'. Clearing both boxes
    must not silently opt the user into that."""
    o = _opts(monkeypatch, SEED_ENABLED=True, SEED_RATIO=0, SEED_MINUTES=0)
    assert "--seed-ratio=1.0" in o


def test_preview_priority_is_opt_in(monkeypatch):
    assert not any("prioritize-piece" in x
                   for x in _opts(monkeypatch, TORRENT_PREVIEW=False))
    assert "--bt-prioritize-piece=head,tail" in _opts(
        monkeypatch, TORRENT_PREVIEW=True)


def test_settings_reach_the_daemon(monkeypatch):
    """Every torrent setting has to turn into an aria2 flag. One that
    persists but never reaches the engine looks like it works.

    (Was a parity check across two engines; the per-torrent-process engine
    has been removed, so only the daemon is left to assert.)
    """
    monkeypatch.setattr(utils, "SEED_ENABLED", True, raising=False)
    monkeypatch.setattr(utils, "SEED_RATIO", 2.5, raising=False)
    monkeypatch.setattr(utils, "TORRENT_PREVIEW", True, raising=False)

    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    opts = d._options()
    for flag in ("--seed-ratio=2.5", "--bt-prioritize-piece=head,tail"):
        assert flag in opts, f"daemon missing {flag}"

def test_settings_reach_the_engine(tmp_path, monkeypatch):
    """End to end: what the dialog saves must turn into aria2 flags. A setting
    that persists but never reaches the engine looks like it works."""
    import types
    import gui2.app_settings as S

    stub = types.SimpleNamespace(
        _extras={"torrent_preview": True, "seed_enabled": True,
                 "seed_ratio": 2.5, "seed_minutes": 45},
        _settings_path=str(tmp_path / "settings.json"))
    S.SettingsMixin._apply_network_settings(stub)

    assert utils.TORRENT_PREVIEW is True
    assert utils.SEED_ENABLED is True
    o = torrent.preference_opts()
    assert "--seed-ratio=2.5" in o
    assert "--seed-time=45" in o
    assert "--bt-prioritize-piece=head,tail" in o


def test_defaults_keep_the_old_behaviour(tmp_path, monkeypatch):
    """An existing install with no new keys must not suddenly start seeding."""
    import types
    import gui2.app_settings as S

    stub = types.SimpleNamespace(_extras={}, _settings_path=str(tmp_path / "s.json"))
    S.SettingsMixin._apply_network_settings(stub)
    assert utils.SEED_ENABLED is False
    assert "--seed-time=0" in torrent.preference_opts()


def test_peer_recruitment_threshold_is_raised(monkeypatch):
    """aria2 only hunts for MORE peers while total speed is under
    bt-request-peer-speed-limit. Its 50 KiB/s default means it stops looking
    almost immediately on a modern line and plateaus on the peers it has."""
    o = torrent.preference_opts()
    assert f"--bt-request-peer-speed-limit={torrent.PEER_SPEED_TARGET}" in o
    assert torrent.PEER_SPEED_TARGET != "50K"


def test_it_is_a_threshold_not_a_download_cap(monkeypatch):
    """Nothing here may ever cap download speed — that is the one thing this
    option must not be confused with."""
    o = torrent.preference_opts()
    assert not any("max-overall-download-limit" in x for x in o)
    assert not any("--max-download-limit" in x for x in o)


def test_preallocation_uses_falloc_not_prealloc(monkeypatch):
    """prealloc WRITES ZERO BYTES across the whole file, and aria2 is single
    threaded — measured on a live daemon, RPC that normally answers in 1-60ms
    took up to 9.5s and the download speed collapsed for the duration. That is
    the "10 Mb/s then 0 then it climbs back" sawtooth. falloc asks the
    filesystem to reserve the space, which on NTFS is near-instant."""
    monkeypatch.setattr(utils, "PREALLOCATE", True, raising=False)
    assert torrent.allocation_opt() == "--file-allocation=falloc"


def test_preallocation_off_allocates_nothing(monkeypatch):
    monkeypatch.setattr(utils, "PREALLOCATE", False, raising=False)
    assert torrent.allocation_opt() == "--file-allocation=none"


def test_the_daemon_uses_the_allocation_setting(monkeypatch):
    monkeypatch.setattr(utils, "PREALLOCATE", True, raising=False)
    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    assert torrent.allocation_opt() in d._options()



# ---- told to the engine that is running (2026-11-03) ---------------------------
# They went in on aria2's command line only, so a cap or a switch changed in
# Settings did nothing until HyperFetch restarted.
def _live(monkeypatch, **settings):
    settings = {"PREALLOCATE": False, "PROXIES": None, **settings}
    for k, v in settings.items():
        monkeypatch.setattr(utils, k, v, raising=False)
    return torrent.live_preferences()


def test_each_setting_is_said_outright_so_one_switched_off_goes_off(monkeypatch):
    on = _live(monkeypatch, MAX_UPLOAD_BPS=100 * 1024, TORRENT_PREVIEW=True,
               SEED_ENABLED=True, SEED_RATIO=2.0, SEED_MINUTES=30)
    assert on == {"max-overall-upload-limit": str(100 * 1024),
                  "bt-prioritize-piece": "head,tail", "seed-ratio": "2", "seed-time": "30",
                  "file-allocation": "none", "all-proxy": ""}
    off = _live(monkeypatch, MAX_UPLOAD_BPS=0, TORRENT_PREVIEW=False, SEED_ENABLED=False)
    assert off == {"max-overall-upload-limit": "0", "bt-prioritize-piece": "",
                   "seed-time": "0", "file-allocation": "none", "all-proxy": ""}


def test_pre_allocation_and_the_proxy_are_said_too(monkeypatch):
    """They went in on aria2's command line only, as Listen Port and Disk
    cache still do (2026-11-04, list #7)."""
    proxy = {"http": "http://127.0.0.1:3128", "https": "http://127.0.0.1:3128"}
    o = _live(monkeypatch, PREALLOCATE=True, PROXIES=proxy, PROXY_TORRENTS=True)
    assert (o["file-allocation"], o["all-proxy"]) == ("falloc", "http://127.0.0.1:3128")
    o = _live(monkeypatch, PREALLOCATE=True, PROXIES=proxy, PROXY_TORRENTS=False)
    assert o["all-proxy"] == "", "kept off the proxy: it is cleared, not left as it was"


def test_no_time_limit_is_said_as_one_too_long_to_reach(monkeypatch):
    """A running daemon cannot forget a seed-time it has (0, if seeding was off
    as it started); as at a start, the ratio then ends the seeding."""
    o = _live(monkeypatch, SEED_ENABLED=True, SEED_RATIO=1.5, SEED_MINUTES=0)
    assert o["seed-time"] == torrent.SEED_TIME_NONE and o["seed-ratio"] == "1.5"
    o = _live(monkeypatch, SEED_ENABLED=True, SEED_RATIO=0.0, SEED_MINUTES=0)
    assert o["seed-ratio"] == "1.0", "as preference_opts: two cleared boxes are not forever"


def test_a_running_daemon_is_sent_them(monkeypatch):
    sent = []
    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    monkeypatch.setattr(d, "_post", lambda port, secret, method, params, timeout=0:
                        sent.append((method, params)))
    monkeypatch.setattr(utils, "MAX_UPLOAD_BPS", 250 * 1024)
    d.apply_preferences()
    live = torrent.live_preferences()
    proxy = {"all-proxy": live.pop("all-proxy")}
    # the proxy on its own: one aria2 refuses fails the call it is in
    assert sent == [("aria2.changeGlobalOption", [live]),
                    ("aria2.changeGlobalOption", [proxy])]
    assert sent[0][1][0]["max-overall-upload-limit"] == str(250 * 1024)
    sent.clear()
    aria2d.Aria2Daemon().apply_preferences()          # none running: nothing to tell
    assert sent == []


def test_saving_settings_tells_the_running_engine(monkeypatch):
    import threading
    import types
    from gui2 import palette
    from gui2 import app_settings
    told = threading.Event()
    monkeypatch.setattr(aria2d, "DAEMON", types.SimpleNamespace(apply_preferences=told.set))
    monkeypatch.setattr(app_settings, "apply_theme", lambda name: None)
    monkeypatch.setattr(palette, "set_theme", lambda name: name)
    monkeypatch.setattr(palette, "set_accent", lambda key: key)
    accent = next(k for k, v in palette.ACCENTS.items() if v == palette.COLORS["accent"])

    class Win(app_settings.SettingsMixin):
        theme, save_dir, _extras = "dark", "", {}
        queue = types.SimpleNamespace(set_max_concurrent=lambda q, n: None, segments=8)

        def _nothing(self, *a, **k):          # the rest of the window: not this test's
            pass

        _sync_concurrency = _apply_web_settings = _apply_network_settings = _nothing
        _apply_throttle = _apply_appearance = _save_settings = refresh = _nothing

    Win()._apply_settings({"save_dir": "", "max_concurrent": 3, "segments": 8,
                           "verify_tls": True, "speed_limit": "Unlimited", "theme": "dark",
                           "accent": accent, "sched_en": False, "sched_start": "02:00",
                           "sched_stop": "08:00"})
    assert told.wait(5), "the running engine was not told"


def test_upnp_is_given_the_port_the_running_engine_listens_on(monkeypatch):
    """aria2 keeps the port it started with; Settings' new one waits for its
    next start. UPnP opened the new one at once and closed the old (list #7)."""
    d = aria2d.Aria2Daemon()
    d.port, d.secret = 6800, "s"
    monkeypatch.setattr(d, "_post", lambda port, secret, method, params, timeout=0:
                        {"listen-port": "51990"} if method == "aria2.getGlobalOption"
                        else pytest.fail(method))
    monkeypatch.setattr(aria2d, "DAEMON", d)
    monkeypatch.setattr(utils, "LISTEN_PORT", 6000)
    assert torrent.listen_port() == 51990
    assert torrent.wanted_listen_port() == 6000
    d.port = 0                                       # none running: the setting's
    assert torrent.listen_port() == 6000


def test_a_proxy_aria2_cannot_use_is_not_handed_to_it(monkeypatch):
    """aria2 takes http proxies only: started with a socks5:// one it exits
    at once (28), and every torrent failed (2026-11-06)."""
    monkeypatch.setattr(utils, "PROXY_TORRENTS", True)
    for given, handed in (("socks5://127.0.0.1:1080", ""), ("http://127.0.0.1:3128",
                          "http://127.0.0.1:3128"), ("127.0.0.1:3128", "127.0.0.1:3128")):
        monkeypatch.setattr(utils, "PROXIES", {"http": given, "https": given})
        assert torrent.proxy_url() == handed, given
