"""The web UI's read + control API.

Every one of these is reachable from the network once LAN mode is on, so the
tests that matter most are the ones proving they refuse an unauthenticated
caller and never hand out anything they shouldn't.
"""
import pytest

import task as T
import utils
import web_auth
from api_server import create_app

PW = "correct horse battery"
LAN = "192.168.1.50"


class _Queue:
    def __init__(self):
        self.tasks = []
        self.paused = []
        self.resumed = []
        self.removed = []

    def add_task(self, t, start=True):
        self.tasks.append(t)
        return t

    def get_task(self, tid):
        return next((t for t in self.tasks if t.id == tid), None)

    def pause_task(self, t):
        self.paused.append(t.id)

    def resume_task(self, t):
        self.resumed.append(t.id)

    def remove_task(self, t):
        self.removed.append(t.id)
        self.tasks = [x for x in self.tasks if x.id is not t.id]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    q = _Queue()
    t = T.DownloadTask("https://example.test/Big.File.zip", str(tmp_path / "Big.File.zip"),
                       filename="Big.File.zip",
                       headers={"Cookie": "SESSION=supersecret",
                                "Authorization": "Bearer hunter2",
                                "Referer": "https://example.test/"})
    t.total_size, t.downloaded = 1000, 250
    t.id = "task-1"
    q.tasks.append(t)
    app = create_app(q, str(tmp_path), pending=None, token="tok")
    app.config["TESTING"] = True
    return app.test_client(), q, t


def _get(c, p, **kw):
    return c.get(p, environ_overrides={"REMOTE_ADDR": LAN}, **kw)


def _post(c, p, **kw):
    return c.post(p, environ_overrides={"REMOTE_ADDR": LAN}, **kw)


def _delete(c, p, **kw):
    return c.delete(p, environ_overrides={"REMOTE_ADDR": LAN}, **kw)


def _enable(user="admin"):
    """Switch the client on and give it credentials — both are now required
    before any route answers."""
    web_auth.set_password(PW, user=user)
    web_auth.set_enabled(True)


def _login(c, user="admin"):
    _enable(user)
    assert _post(c, "/api/login",
                 json={"username": user, "password": PW}).status_code == 200


# ---- nothing works without signing in --------------------------------------
@pytest.mark.parametrize("method,path", [
    ("get", "/api/downloads"),
    ("get", "/api/stats"),
    ("post", "/api/downloads"),
    ("post", "/api/downloads/task-1/pause"),
    ("post", "/api/downloads/task-1/resume"),
    ("delete", "/api/downloads/task-1"),
])
def test_every_route_refuses_an_anonymous_caller(env, method, path):
    c, q, _ = env
    _enable()                                 # set up and switched on; just not signed in
    r = getattr(c, method)(path, environ_overrides={"REMOTE_ADDR": LAN}, json={})
    assert r.status_code == 401, f"{path} answered without a session"
    assert q.paused == [] and q.removed == []


def test_routes_refuse_when_no_password_is_set(env):
    c, _, _ = env
    web_auth.set_enabled(True)
    r = _get(c, "/api/downloads")
    assert r.status_code == 403
    assert r.get_json()["code"] == "no-password"


def test_routes_refuse_while_the_client_is_switched_off(env):
    """Off is a separate state from unconfigured, and outranks it: a password
    that exists must not let anyone in while the user has it turned off."""
    c, q, _ = env
    web_auth.set_password(PW, user="admin")
    web_auth.set_enabled(False)
    r = _get(c, "/api/downloads")
    assert r.status_code == 403
    assert r.get_json()["code"] == "disabled"
    assert q.paused == [] and q.removed == []


def test_switching_it_off_ends_a_live_session(env):
    """Someone already signed in on a phone must lose access the moment the
    switch goes off — not at the next restart."""
    c, _, _ = env
    _login(c)
    assert _get(c, "/api/downloads").status_code == 200
    web_auth.set_enabled(False)
    r = _get(c, "/api/downloads")
    assert r.status_code == 403
    assert r.get_json()["code"] == "disabled"


def test_the_wrong_username_is_refused(env):
    c, _, _ = env
    _enable("tanumay")
    r = _post(c, "/api/login", json={"username": "admin", "password": PW})
    assert r.status_code == 401


def test_the_username_is_not_case_sensitive(env):
    """Typed on a phone keyboard that loves to capitalise the first letter."""
    c, _, _ = env
    _enable("admin")
    assert _post(c, "/api/login",
                 json={"username": "Admin", "password": PW}).status_code == 200


def test_a_failed_login_does_not_say_which_half_was_wrong(env):
    """Distinct messages would confirm when a username guess had landed."""
    c, _, _ = env
    _enable("admin")
    bad_user = _post(c, "/api/login", json={"username": "nope", "password": PW})
    bad_pass = _post(c, "/api/login", json={"username": "admin", "password": "x" * 12})
    assert bad_user.status_code == bad_pass.status_code == 401
    assert bad_user.get_json() == bad_pass.get_json()


# ---- reading ---------------------------------------------------------------
def test_downloads_are_listed(env):
    c, _, _ = env
    _login(c)
    rows = _get(c, "/api/downloads").get_json()["downloads"]
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Big.File.zip"
    assert row["totalBytes"] == 1000 and row["doneBytes"] == 250
    assert row["percent"] == 25.0


def test_cookies_and_auth_headers_never_reach_the_network(env):
    """The task holds them in memory; an allow-list serialiser is what keeps
    them off the wire even as fields get added later."""
    c, _, _ = env
    _login(c)
    body = _get(c, "/api/downloads").get_data(as_text=True)
    for secret in ("supersecret", "hunter2", "Authorization", "Cookie"):
        assert secret not in body, f"{secret} leaked to the web API"


def test_stats_reports_counts(env):
    c, _, t = env
    _login(c)
    d = _get(c, "/api/stats").get_json()
    assert d["byStatus"].get(t.status) == 1
    assert "history" in d


# ---- control ---------------------------------------------------------------
def test_pause_and_resume_reach_the_queue(env):
    c, q, _ = env
    _login(c)
    assert _post(c, "/api/downloads/task-1/pause").status_code == 200
    assert q.paused == ["task-1"]
    assert _post(c, "/api/downloads/task-1/resume").status_code == 200
    assert q.resumed == ["task-1"]


def test_delete_removes_from_the_list(env):
    c, q, _ = env
    _login(c)
    assert _delete(c, "/api/downloads/task-1").status_code == 200
    assert q.removed == ["task-1"]


def test_an_unknown_id_is_a_404_not_a_crash(env):
    c, _, _ = env
    _login(c)
    assert _post(c, "/api/downloads/nope/pause").status_code == 404


def test_adding_a_url_queues_it(env):
    c, q, _ = env
    _login(c)
    r = _post(c, "/api/downloads", json={"url": "https://example.test/new.zip"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert any(t.url.endswith("new.zip") for t in q.tasks)


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/System32/config/SAM",
    "chrome://settings",
    "javascript:alert(1)",
    "ftp://example.test/x",
    "",
    "   ",
])
def test_dangerous_schemes_are_refused(env, url):
    """This endpoint is network-reachable, so the scheme allow-list matters
    more here than on the local extension route, not less."""
    c, q, _ = env
    _login(c)
    before = len(q.tasks)
    r = _post(c, "/api/downloads", json={"url": url})
    assert r.status_code == 400, f"{url!r} was accepted"
    assert len(q.tasks) == before


def test_a_magnet_is_accepted(env):
    c, q, _ = env
    _login(c)
    r = _post(c, "/api/downloads", json={"url": "magnet:?xt=urn:btih:" + "a" * 40})
    assert r.status_code == 200


# ---- the extension routes stay local ---------------------------------------
def test_the_web_session_does_not_unlock_the_extension_routes(env):
    """Signing in to the web UI must not turn /pair into a LAN endpoint."""
    c, _, _ = env
    _login(c)
    r = _get(c, "/pair", headers={"Origin": "chrome-extension://x"})
    assert r.status_code == 403
