"""API server: both modes, category routing, payload validation, headers."""
from collections import deque

import os
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
