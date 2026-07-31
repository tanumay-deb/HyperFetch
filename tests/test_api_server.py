"""API server: both modes, category routing, payload validation, headers."""
from collections import deque

import os
import sys

import pytest

import task as T
import queue_manager
from api_server import create_app


class _FakeQueue:
    def __init__(self):
        self.tasks = []

    def add_task(self, t, start=True):
        self.tasks.append(t)
        return t


def test_ping_both_modes(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=deque()).test_client()
    assert c.get("/ping").get_json()["status"] == "ok"


def test_gui_mode_fills_pending(tmp_path):
    pend = deque()
    q = _FakeQueue()
    c = create_app(q, str(tmp_path), pending=pend).test_client()
    r = c.post("/download", json={"url": "https://x/file.zip", "filename": "file.zip"})
    assert r.status_code == 200
    assert len(pend) == 1 and pend[0]["url"] == "https://x/file.zip"
    assert q.tasks == []                 # GUI decides later, nothing queued yet


def test_headless_mode_queues_with_category(tmp_path):
    q = _FakeQueue()
    c = create_app(q, str(tmp_path), pending=None).test_client()
    r = c.post("/download", json={"url": "https://x/a.zip", "filename": "a.zip"})
    assert r.status_code == 200
    assert len(q.tasks) == 1
    assert os.path.basename(os.path.dirname(q.tasks[0].save_path)) == "Compressed"


def test_headless_pdf_documents_category(tmp_path):
    q = _FakeQueue()
    c = create_app(q, str(tmp_path), pending=None).test_client()
    c.post("/download", json={"url": "https://x/doc.pdf"})
    assert os.path.basename(os.path.dirname(q.tasks[0].save_path)) == "Documents"


def test_headless_duplicate_unique_paths(tmp_path):
    q = _FakeQueue()
    c = create_app(q, str(tmp_path), pending=None).test_client()
    c.post("/download", json={"url": "https://x/a.zip", "filename": "a.zip"})
    open(q.tasks[0].save_path, "w").close()  # first now exists on disk
    c.post("/download", json={"url": "https://x/a.zip", "filename": "a.zip"})
    assert q.tasks[0].save_path != q.tasks[1].save_path


def test_missing_url_400(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None).test_client()
    assert c.post("/download", json={}).status_code == 400


def test_malformed_body_no_crash(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None).test_client()
    r = c.post("/download", data="not json", content_type="text/plain")
    assert r.status_code == 400
    assert c.get("/ping").status_code == 200   # server still alive


def test_cookies_forwarded_to_task(tmp_path):
    q = _FakeQueue()
    c = create_app(q, str(tmp_path), pending=None).test_client()
    c.post("/download", json={"url": "https://x/a.zip", "cookies": "s=1",
                              "userAgent": "UA", "referrer": "https://ref"})
    h = q.tasks[0].headers
    assert h["Cookie"] == "s=1" and h["Referer"] == "https://ref" and h["User-Agent"] == "UA"


def test_auto_capture_allowlist(tmp_path, monkeypatch):
    """auto=true downloads are filtered by utils.CAPTURE_EXTS; manual ones and
    magnets are never filtered."""
    import utils
    monkeypatch.setattr(utils, "CAPTURE_EXTS", ["zip", "mp4"])
    pend = deque()
    c = create_app(_FakeQueue(), str(tmp_path), pending=pend).test_client()

    # auto + extension NOT in list -> ignored, nothing queued
    r = c.post("/download", json={"url": "https://x/p.html", "filename": "p.html", "auto": True})
    assert r.get_json()["status"] == "ignored" and len(pend) == 0
    # auto + allowed extension -> queued
    r = c.post("/download", json={"url": "https://x/c.mp4", "filename": "c.mp4", "auto": True})
    assert r.get_json()["status"] == "queued" and len(pend) == 1
    # manual (no auto flag) -> queued regardless of extension
    c.post("/download", json={"url": "https://x/p.html", "filename": "p.html"})
    assert len(pend) == 2
    # magnet + auto -> always allowed (no extension)
    c.post("/download", json={"url": "magnet:?xt=urn:btih:abc", "auto": True})
    assert len(pend) == 3


def test_auto_capture_empty_list_allows_all(tmp_path, monkeypatch):
    import utils
    monkeypatch.setattr(utils, "CAPTURE_EXTS", [])
    pend = deque()
    c = create_app(_FakeQueue(), str(tmp_path), pending=pend).test_client()
    c.post("/download", json={"url": "https://x/anything.xyz", "filename": "anything.xyz", "auto": True})
    assert len(pend) == 1


def test_probe_returns_variants(tmp_path, monkeypatch):
    import hls
    monkeypatch.setattr(hls, "probe_variants",
                        lambda url, headers=None: [{"label": "1080p", "height": 1080,
                                                    "bandwidth": 5_000_000,
                                                    "url": url + "#1080", "size": 9}])
    c = create_app(_FakeQueue(), str(tmp_path), pending=None).test_client()
    r = c.post("/probe", json={"url": "https://x/master.m3u8"})
    assert r.status_code == 200
    v = r.get_json()["variants"]
    assert len(v) == 1 and v[0]["label"] == "1080p"


def test_probe_rejects_bad_scheme(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None).test_client()
    assert c.post("/probe", json={"url": "file:///etc/passwd"}).status_code == 400


def test_probe_requires_token(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None, token="SECRET").test_client()
    assert c.post("/probe", json={"url": "https://x/m.m3u8"}).status_code == 401


# ---- auto-pair: /pair serves the token only to the trusted extension id ----
import api_server

_OFFICIAL = "chrome-extension://" + next(iter(api_server.TRUSTED_EXT_IDS))


def test_pair_serves_token_to_official_extension(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None, token="SECRET").test_client()
    r = c.get("/pair", headers={"Origin": _OFFICIAL})
    assert r.status_code == 200
    assert r.get_json()["token"] == "SECRET"
    assert r.headers.get("Access-Control-Allow-Origin") == _OFFICIAL


def test_pair_denies_other_extension(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None, token="SECRET").test_client()
    r = c.get("/pair", headers={"Origin": "chrome-extension://someotherextensionidaaaaaaaaaaaa"})
    assert r.status_code == 403
    assert "SECRET" not in r.get_data(as_text=True)


def test_pair_denies_website_and_missing_origin(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None, token="SECRET").test_client()
    assert c.get("/pair", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.get("/pair").status_code == 403


def test_pair_preflight_ok_for_official(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=None, token="SECRET").test_client()
    r = c.open("/pair", method="OPTIONS", headers={"Origin": _OFFICIAL})
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin") == _OFFICIAL


# ---- /open: single-instance handoff for .torrent / magnet: ----
def test_open_accepts_magnet_and_torrent(tmp_path):
    pend = deque()
    c = create_app(_FakeQueue(), str(tmp_path), pending=pend, token="S").test_client()
    h = {"X-HyperFetch-Token": "S"}
    assert c.post("/open", json={"target": "magnet:?xt=urn:btih:abc"}, headers=h).status_code == 200
    assert c.post("/open", json={"target": "C:/x/file.torrent"}, headers=h).status_code == 200
    assert len(pend) == 2


def test_open_rejects_non_torrent_and_unauthorized(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=deque(), token="S").test_client()
    assert c.post("/open", json={"target": "https://x/y.zip"},
                  headers={"X-HyperFetch-Token": "S"}).status_code == 400
    assert c.post("/open", json={"target": "magnet:?x"}).status_code == 401


def test_open_headless_queues(tmp_path):
    q = _FakeQueue()
    c = create_app(q, str(tmp_path), pending=None, token="S").test_client()
    r = c.post("/open", json={"target": "magnet:?xt=urn:btih:z"}, headers={"X-HyperFetch-Token": "S"})
    assert r.status_code == 200 and len(q.tasks) == 1


# ---- /focus: single-instance handoff for plain (no-target) launches ----
def test_focus_gui_mode_appends_focus_item(tmp_path):
    pend = deque()
    c = create_app(_FakeQueue(), str(tmp_path), pending=pend, token="S").test_client()
    r = c.post("/focus", json={}, headers={"X-HyperFetch-Token": "S"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "focused"
    assert len(pend) == 1 and pend[0].get("focus") is True


def test_focus_headless_says_no_gui(tmp_path):
    """A headless server has no window to raise — the second launch must NOT
    exit thinking it focused something."""
    c = create_app(_FakeQueue(), str(tmp_path), pending=None, token="S").test_client()
    r = c.post("/focus", json={}, headers={"X-HyperFetch-Token": "S"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "no-gui"


def test_focus_requires_token(tmp_path):
    pend = deque()
    c = create_app(_FakeQueue(), str(tmp_path), pending=pend, token="S").test_client()
    assert c.post("/focus", json={}).status_code == 401
    assert len(pend) == 0


# ---- Private Network Access: Chrome preflights every extension -> 127.0.0.1
# request and blocks the call unless the reply opts in. flask-cors >= 5 defaults
# this OFF, which silently broke the whole browser bridge.
_PNA_REQ = {"Origin": _OFFICIAL,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true"}


def test_ping_preflight_allows_private_network(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=deque()).test_client()
    r = c.open("/ping", method="OPTIONS", headers=_PNA_REQ)
    assert r.status_code in (200, 204)
    assert r.headers.get("Access-Control-Allow-Private-Network") == "true"


def test_download_preflight_allows_private_network(tmp_path):
    c = create_app(_FakeQueue(), str(tmp_path), pending=deque(), token="S").test_client()
    r = c.open("/download", method="OPTIONS",
               headers={**_PNA_REQ, "Access-Control-Request-Method": "POST"})
    assert r.headers.get("Access-Control-Allow-Private-Network") == "true"


def test_pair_preflight_allows_private_network(tmp_path):
    """/pair sets its own CORS headers (outside the global rule), so it needs
    the opt-in too — without it auto-pairing can never fetch a token."""
    c = create_app(_FakeQueue(), str(tmp_path), pending=None, token="SECRET").test_client()
    r = c.open("/pair", method="OPTIONS", headers=_PNA_REQ)
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Private-Network") == "true"


def test_private_network_optin_does_not_widen_origins(tmp_path):
    """The PNA opt-in must not let a website through: origin rules still rule."""
    c = create_app(_FakeQueue(), str(tmp_path), pending=deque(), token="S").test_client()
    r = c.open("/ping", method="OPTIONS",
               headers={**_PNA_REQ, "Origin": "https://evil.example"})
    assert r.headers.get("Access-Control-Allow-Origin") != "https://evil.example"
    # and /pair still refuses a non-official extension outright
    assert c.get("/pair", headers={"Origin": "chrome-extension://nottherealoneaaaaaaaaaaaaaaaaaa"}
                 ).status_code == 403


# ---- single-instance guard (main.py): a duplicate window shares downloads.json
# with the original, so whichever saves last wipes the other's list ----
def test_guard_runs_even_when_a_target_was_given(monkeypatch):
    """The hole that produced two live windows: opening a .torrent set `target`,
    and a FAILED handoff then fell through to a full second instance because the
    focus check was gated on `not target`."""
    import main
    calls = {"handoff": 0, "focus": 0}

    def fake_post(path, payload):
        if path == "/open":
            calls["handoff"] += 1
            return None                     # handoff fails
        if path == "/focus":
            calls["focus"] += 1
            return {"status": "focused"}
        return None

    monkeypatch.setattr(main, "_post_running", fake_post)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "C:/x/a.torrent"])
    monkeypatch.setattr(main.crash_reporter, "install", lambda *a, **k: None)
    started = {"n": 0}
    monkeypatch.setitem(__import__("sys").modules, "gui2.app",
                        type("M", (), {"run_v2": staticmethod(lambda **k: started.__setitem__("n", 1))})())
    assert main.main() == 0                 # exits instead of opening a window
    assert calls["focus"] == 1
    assert started["n"] == 0                # no second instance
    assert calls["handoff"] == 2            # and the torrent was retried


def test_guard_lets_the_first_instance_start(monkeypatch):
    """With nothing running, the app must actually open."""
    import main
    monkeypatch.setattr(main, "_claim_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(main, "_post_running", lambda *a, **k: None)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    monkeypatch.setattr(main.crash_reporter, "install", lambda *a, **k: None)
    started = {"n": 0}
    monkeypatch.setitem(__import__("sys").modules, "gui2.app",
                        type("M", (), {"run_v2": staticmethod(
                            lambda **k: (started.__setitem__("n", 1), 0)[1])})())
    assert main.main() == 0
    assert started["n"] == 1


def test_restart_waits_for_predecessor_then_focuses_if_it_lingers(monkeypatch):
    """--restarted used to skip the guard entirely, so a restart whose old
    instance failed to quit produced two windows."""
    import main
    monkeypatch.setattr(main, "_wait_for_exit", lambda timeout=15.0: False)
    monkeypatch.setattr(main, "_claim_single_instance", lambda *a, **k: True)
    monkeypatch.setattr(main, "_post_running",
                        lambda p, d: {"status": "focused"} if p == "/focus" else None)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--restarted"])
    monkeypatch.setattr(main.crash_reporter, "install", lambda *a, **k: None)
    started = {"n": 0}
    monkeypatch.setitem(__import__("sys").modules, "gui2.app",
                        type("M", (), {"run_v2": staticmethod(lambda **k: started.__setitem__("n", 1))})())
    assert main.main() == 0
    assert started["n"] == 0                # focused the survivor, no duplicate


# ---- single-instance mutex: the guard must not depend on the HTTP server ----
@pytest.mark.skipif(sys.platform != "win32",
                    reason="named mutexes are a Windows facility; elsewhere "
                           "_claim_single_instance deliberately fails open")
def test_mutex_blocks_a_second_claim_and_releases_on_death():
    """A kernel mutex, not an HTTP round-trip: the old guard asked the running
    instance over its localhost server, but that server is exactly what fails
    when the port is taken — so a crippled instance looked like 'nothing
    running' and a duplicate window opened."""
    import subprocess
    import sys
    import textwrap
    import time

    hold = textwrap.dedent("""
        import sys, time; sys.path.insert(0, %r)
        import main
        print(main._claim_single_instance("Local\HyperFetchTest.Mutex"), flush=True)
        time.sleep(10)
    """) % str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    probe = textwrap.dedent("""
        import sys; sys.path.insert(0, %r)
        import main
        print(main._claim_single_instance("Local\HyperFetchTest.Mutex"))
    """) % str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    a = subprocess.Popen([sys.executable, "-c", hold], stdout=subprocess.PIPE, text=True)
    try:
        assert a.stdout.readline().strip() == "True"        # owner takes it
        time.sleep(0.3)
        b = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        assert b.stdout.strip() == "False"                  # duplicate refused
    finally:
        a.kill(); a.wait()
    time.sleep(1)
    c = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert c.stdout.strip() == "True"                       # no stale lock


def test_second_launch_exits_without_opening_a_window(monkeypatch):
    """Even if /focus cannot be reached (crippled instance), the launch must
    still exit rather than open a rival window."""
    import main
    monkeypatch.setattr(main, "_claim_single_instance", lambda: False)
    monkeypatch.setattr(main, "_post_running", lambda *a, **k: None)   # server down
    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    monkeypatch.setattr(main.crash_reporter, "install", lambda *a, **k: None)
    started = {"n": 0}
    monkeypatch.setitem(__import__("sys").modules, "gui2.app",
                        type("M", (), {"run_v2": staticmethod(lambda **k: started.__setitem__("n", 1))})())
    assert main.main() == 0
    assert started["n"] == 0


def test_restart_is_not_blocked_by_its_predecessors_mutex(monkeypatch):
    """--restarted is the predecessor's intended replacement; it must not be
    refused by the lock the outgoing instance still holds."""
    import main
    monkeypatch.setattr(main, "_claim_single_instance", lambda: False)
    monkeypatch.setattr(main, "_wait_for_exit", lambda timeout=15.0: True)
    monkeypatch.setattr(main, "_post_running", lambda *a, **k: None)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--restarted"])
    monkeypatch.setattr(main.crash_reporter, "install", lambda *a, **k: None)
    started = {"n": 0}
    monkeypatch.setitem(__import__("sys").modules, "gui2.app",
                        type("M", (), {"run_v2": staticmethod(
                            lambda **k: (started.__setitem__("n", 1), 0)[1])})())
    assert main.main() == 0
    assert started["n"] == 1                                # replacement DID start
