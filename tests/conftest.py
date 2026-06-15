"""Shared pytest fixtures: isolated app-data, deterministic local HTTP servers.

Every test runs against a temp app-data dir (real %APPDATA% state is never
touched) and against in-process http.server handlers (no external network, so
the suite is hermetic and CI-safe).
"""
import os
import sys
import string
import struct
import threading
import http.server

import pytest

# make project modules importable regardless of pytest's rootdir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# headless Qt for any GUI test
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import utils  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_appdata(tmp_path, monkeypatch):
    """Redirect app-data to a temp dir for every test."""
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(d))
    monkeypatch.setattr(utils, "VERIFY_TLS", True, raising=False)
    yield d


# ---------------------------------------------------------------- file server
def _make_payload(n_bytes):
    base = (string.ascii_letters + string.digits).encode()
    out = bytearray()
    while len(out) < n_bytes:
        out.extend(base)
    return bytes(out[:n_bytes])


class _Server:
    """Wraps a ThreadingHTTPServer with a configurable handler."""

    def __init__(self, handler_cls):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def url(self, path):
        return f"{self.base}/{path.lstrip('/')}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def file_server():
    """A range-capable static file server. Holds files registered via .put()."""
    files = {}            # path -> bytes
    behaviors = {"no_range": set(), "no_length": set()}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self, head_only=False):
            path = self.path.split("?")[0].lstrip("/")
            if path not in files:
                self.send_response(404)
                self.end_headers()
                return
            data = files[path]
            rng = self.headers.get("Range")
            no_range = path in behaviors["no_range"]
            if rng and not no_range and rng.startswith("bytes="):
                spec = rng.split("=", 1)[1]
                start_s, _, end_s = spec.partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else len(data) - 1
                end = min(end, len(data) - 1)
                chunk = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                if not head_only:
                    self.wfile.write(chunk)
                return
            # full body
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            if path not in behaviors["no_range"]:
                self.send_header("Accept-Ranges", "bytes")
            if path not in behaviors["no_length"]:
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def do_HEAD(self):
            self._serve(head_only=True)

        def do_GET(self):
            self._serve(head_only=False)

    srv = _Server(Handler)
    srv.files = files
    srv.behaviors = behaviors

    def put(path, n_bytes=None, data=None, no_range=False, no_length=False):
        if data is None:
            data = _make_payload(n_bytes if n_bytes is not None else 1024)
        files[path] = data
        if no_range:
            behaviors["no_range"].add(path)
        if no_length:
            behaviors["no_length"].add(path)
        return data

    srv.put = put
    yield srv
    srv.stop()


@pytest.fixture
def make_payload():
    return _make_payload


@pytest.fixture
def aes_tools():
    """Helpers to build AES-128 encrypted HLS segments for tests."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    def encrypt(data, key, seq):
        iv = struct.pack(">QQ", 0, seq)
        pad = 16 - (len(data) % 16)
        data = data + bytes([pad]) * pad
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return enc.update(data) + enc.finalize()

    return type("AesTools", (), {"encrypt": staticmethod(encrypt)})
