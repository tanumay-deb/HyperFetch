"""Shared pytest fixtures: isolated app-data, deterministic local HTTP servers.

Every test runs against a temp app-data dir (real %APPDATA% state is never
touched) and against in-process http.server handlers (no external network, so
the suite is hermetic and CI-safe). Cyclic garbage is collected only on the
main thread, between tests.
"""
import gc
import os
import sys
import time
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


# --------------------------------------------------------- garbage collection
# Qt objects must be destroyed on the GUI thread. Python's cyclic collector runs
# on whichever thread is allocating when a threshold trips, and this suite always
# has threads about: the stand-in servers' handlers, downloader workers, UPnP
# lookups. A widget held only by a reference cycle (a test's parentless `host`
# and the dialog that keeps it) was then torn down off the GUI thread: directly
# for a Python subclass, or queued by PySide6 to run at the GUI thread's next
# bytecode, even midway through Qt delivering an event to that widget. Either
# way the interpreter segfaulted now and then. So automatic collection is off,
# and collect_garbage does it on the main thread, between tests.
@pytest.fixture(scope="session", autouse=True)
def no_automatic_gc():
    gc.disable()
    yield
    gc.collect()                # the last of it, before any thread may collect
    gc.enable()


_tests_run = 0


@pytest.fixture(autouse=True)
def collect_garbage():
    """Runs once a test's other fixtures are torn down (it is defined before
    them, so it is torn down after them): the young generation every time, the
    older ones every 10 and 100 tests, about CPython's own schedule."""
    global _tests_run
    yield
    _tests_run += 1
    gc.collect(2 if _tests_run % 100 == 0 else 1 if _tests_run % 10 == 0 else 0)


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


# --------------------------------------------------------------- media server
@pytest.fixture
def media_server():
    """A CDN as the segmented engine meets one: byte ranges, an ETag, and the
    habits that matter here, each off until a test turns it on.

    .put(path, data, etag=None)   serve data at /path (any query string)
    .rate        bytes/s per connection (0 = as fast as it goes)
    .limit       connections served at once; past it a request is refused
    .refuse      how: "403", "429" or "reset" (TCP RST)
    .tokens      when a set, /path?t=<token> must carry one of them (a signed
                 link); anything else is answered 403
    .no_range    paths answered 200 with the whole body whatever the Range
    .forbid_range  paths that answer a request with a Range 403 (hotlink
                 guards do this); a plain GET still gets the whole body
    .html        paths answered with a web page

    .log holds one dict per request: method, path, query, range, headers.
    .stats["refused"] counts refusals, .stats["max_active"] the most served
    at once.
    """
    import socket
    import urllib.parse

    files, etags = {}, {}
    state = {"active": 0, "max_active": 0, "refused": 0}
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self, head):
            u = urllib.parse.urlsplit(self.path)
            path = u.path.lstrip("/")
            q = urllib.parse.parse_qs(u.query)
            srv.log.append({"method": self.command, "path": path,
                            "query": u.query, "range": self.headers.get("Range"),
                            "headers": dict(self.headers.items())})
            if path not in files:
                return self._empty(404)
            if srv.tokens is not None and (q.get("t") or [""])[0] not in srv.tokens:
                return self._empty(403)
            if path in srv.forbid_range and self.headers.get("Range"):
                return self._empty(403)
            if path in srv.html:
                body = b"<!DOCTYPE html><html><body>a page</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                return
            with lock:
                over = bool(srv.limit) and state["active"] >= srv.limit
                if over:
                    state["refused"] += 1
                else:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
            if over:
                return self._refuse()
            try:
                self._body(path, head)
            except OSError:
                pass                                # the client went away
            finally:
                with lock:
                    state["active"] -= 1

        def _empty(self, code, **hdrs):
            self.send_response(code)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _refuse(self):
            if srv.refuse == "reset":
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                           struct.pack("ii", 1, 0))
                self.connection.close()
                return
            return self._empty(429 if srv.refuse == "429" else 403)

        def _body(self, path, head):
            data = files[path]
            start, end = 0, len(data) - 1
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes=") and path not in srv.no_range:
                s, _, e = rng[6:].partition("-")
                start = int(s) if s else 0
                end = min(int(e), len(data) - 1) if e else len(data) - 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            if path not in srv.no_range:
                self.send_header("Accept-Ranges", "bytes")
            if etags.get(path):
                self.send_header("ETag", etags[path])
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            if head:
                return
            pos, t0, sent = start, time.monotonic(), 0
            while pos <= end:
                n = min(65536, end - pos + 1)
                self.wfile.write(data[pos:pos + n])
                pos += n
                sent += n
                if srv.rate:
                    ahead = sent / srv.rate - (time.monotonic() - t0)
                    if ahead > 0:
                        time.sleep(ahead)

        def do_HEAD(self):
            self._serve(True)

        def do_GET(self):
            self._serve(False)

    srv = _Server(Handler)
    srv.log, srv.rate, srv.limit, srv.refuse = [], 0, 0, "403"
    srv.tokens, srv.no_range, srv.html = None, set(), set()
    srv.forbid_range = set()

    def put(path, data, etag=None):
        files[path] = data
        etags[path] = etag
        return data

    srv.put = put
    srv.stats = state
    yield srv
    srv.stop()


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
