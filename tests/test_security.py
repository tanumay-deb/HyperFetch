"""P0 security guarantees: pairing token, URL scheme allow-list, path-traversal
confinement, sensitive-header stripping, TLS-verify default."""
from collections import deque

import pytest

import utils
import task as T
from api_server import create_app


@pytest.fixture
def client_factory(tmp_path):
    def make(token=None, headless=False):
        save = tmp_path / "dl"
        save.mkdir(exist_ok=True)
        app = create_app(None if headless else object(), str(save),
                         pending=None if headless else deque(), token=token)
        return app.test_client(), str(save)
    return make


def test_token_required_when_set(client_factory):
    c, _ = client_factory(token="secret123")
    assert c.post("/download", json={"url": "https://x/a.zip"}).status_code == 401
    ok = c.post("/download", json={"url": "https://x/a.zip"},
                headers={"X-HyperFetch-Token": "secret123"})
    assert ok.status_code == 200


def test_token_accepted_in_body(client_factory):
    c, _ = client_factory(token="secret123")
    r = c.post("/download", json={"url": "https://x/a.zip", "token": "secret123"})
    assert r.status_code == 200


def test_wrong_token_rejected(client_factory):
    c, _ = client_factory(token="secret123")
    r = c.post("/download", json={"url": "https://x/a.zip"},
               headers={"X-HyperFetch-Token": "nope"})
    assert r.status_code == 401


def test_ping_open_and_advertises_token(client_factory):
    c, _ = client_factory(token="secret123")
    r = c.get("/ping")
    assert r.status_code == 200 and r.get_json()["needsToken"] is True


def test_token_none_is_open(client_factory):
    c, _ = client_factory(token=None)
    assert c.post("/download", json={"url": "https://x/a.zip"}).status_code == 200


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd", "file://C:/Windows/win.ini",
    "javascript:alert(1)", "chrome://settings", "ftp://x/y", "", "   ",
])
def test_non_http_scheme_blocked(client_factory, bad):
    c, _ = client_factory(token=None)
    assert c.post("/download", json={"url": bad}).status_code == 400


@pytest.mark.parametrize("evil", [
    "../../../../Windows/evil.exe",
    "..\\..\\..\\system32\\bad.dll",
    "/etc/cron.d/x",
    "....//....//x.sh",
    "foo/../../bar.bin",
])
def test_unique_path_confined(tmp_path, evil):
    import os
    base = str(tmp_path)
    p = utils.unique_path(base, evil)
    assert os.path.dirname(os.path.abspath(p)) == os.path.abspath(base)


def test_safe_filename_no_separators():
    import os
    for n in ["../x", "a/b/c", "a\\b", "..", ".", "", "CON"]:
        out = utils.safe_filename(n)
        assert os.sep not in out and "/" not in out and out not in ("", ".", "..")


def test_cookies_not_persisted():
    t = T.DownloadTask("https://x/f.zip", "C:/t/f.zip",
                       headers={"Cookie": "sess=abc", "Referer": "https://x",
                                "Authorization": "Bearer y"})
    d = t.to_dict()
    assert "Cookie" not in d["headers"]
    assert "Authorization" not in d["headers"]
    assert d["headers"].get("Referer") == "https://x"


def test_strip_sensitive_case_insensitive():
    out = utils.strip_sensitive({"COOKIE": "x", "AuThOrIzAtIoN": "y", "X-Ok": "z"})
    assert out == {"X-Ok": "z"}


def test_verify_tls_default_true():
    assert utils.VERIFY_TLS is True


def test_pairing_token_stable_and_persisted(isolate_appdata):
    t1 = utils.get_or_create_token()
    t2 = utils.get_or_create_token()
    assert t1 and t1 == t2 and len(t1) >= 24
