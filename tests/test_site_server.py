"""The public users site.

This app answers the internet through a tunnel, so most of these tests are
about what it refuses: other people's downloads, control routes that must not
exist here, and anything that would let one account reach another's files.
"""
import os

import pytest

import site_auth
import site_limits
import task as T
import utils
from site_server import create_site_app


PW = "correct horse battery"
# Behind a tunnel every visitor genuinely arrives as 127.0.0.1, which is why
# this app can never use the caller's address to decide anything.
TUNNEL = {"REMOTE_ADDR": "127.0.0.1"}


class _Queue:
    def __init__(self):
        self.tasks = []
        self.paused, self.resumed, self.removed, self.started = [], [], [], []

    def get_task(self, tid):
        return next((t for t in self.tasks if t.id == tid), None)

    def add_task(self, t, start=True):
        self.tasks.append(t)
        self.started.append((t.id, start))
        return t

    def pause_task(self, t):
        self.paused.append(t.id)
        t.status = T.PAUSED

    def resume_task(self, t):
        self.resumed.append(t.id)
        t.status = T.DOWNLOADING

    def remove_task(self, t):
        self.removed.append(t.id)
        self.tasks.remove(t)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    dl = tmp_path / "downloads"
    dl.mkdir()
    q = _Queue()
    app = create_site_app(q, str(dl))
    app.config["TESTING"] = True
    site_auth.set_enabled(True)
    return app.test_client(), q, str(dl)


def _account(name="tanumay", pw=PW):
    return site_auth.create_user(name, name + "@e.test", pw,
                                 site_auth.invite_code())


def _login(c, name="tanumay", pw=PW):
    _account(name, pw)
    r = c.post("/api/login", json={"username": name, "password": pw},
               environ_overrides=TUNNEL)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r


def _task(q, owner, dl, name="film.mkv", status=T.COMPLETED, body=b"data"):
    folder = utils.user_download_dir(dl, owner) if owner else dl
    p = os.path.join(folder, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(body)
    t = T.DownloadTask("https://e.test/" + name, p, filename=name)
    t.owner = owner
    t.status = status
    q.tasks.append(t)
    return t


def _get(c, p, **kw):
    return c.get(p, environ_overrides=TUNNEL, **kw)


def _post(c, p, **kw):
    return c.post(p, environ_overrides=TUNNEL, **kw)


# ---- the app boundary ------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/download", "/pair", "/probe", "/focus", "/open", "/ping",
    "/api/stats", "/ui/", "/ui/app.js",
])
def test_control_and_extension_routes_do_not_exist_here(env, path):
    """The tunnel makes every visitor look local, so a shared app could not
    tell them apart. These routes are simply not registered on this one."""
    c, _, _ = env
    for method in ("get", "post"):
        r = getattr(c, method)(path, environ_overrides=TUNNEL, json={})
        # The catch-all serves the front end for unknown GETs; what matters is
        # that none of these ever does anything.
        if path.startswith("/api/"):
            # A catch-all answering 200 with HTML turns a typo into a mystery.
            assert r.status_code in (404, 405), path
        else:
            assert r.status_code in (404, 405, 200, 503), path
            if r.status_code == 200:
                assert b"token" not in r.data.lower(), path


def test_the_session_cookie_is_marked_secure(env):
    """This app is only ever reached over HTTPS at the tunnel. The control app
    cannot set this, which is exactly why the two configs differ."""
    from site_server import create_site_app as mk
    app = mk(_Queue(), ".")
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_NAME"] != "session", \
        "sharing a cookie name with the control app invites confusion"


def test_the_site_key_is_not_the_control_key(env):
    import web_auth
    from site_server import create_site_app as mk
    web_auth.set_password(PW, user="admin")
    assert mk(_Queue(), ".").secret_key != web_auth.secret_key()


# ---- maintenance -----------------------------------------------------------
def test_everything_says_unavailable_when_switched_off(env):
    """The tunnel stays up, so this reads as maintenance rather than a broken
    link."""
    c, _, _ = env
    _login(c)
    site_auth.set_enabled(False)
    for path in ("/api/downloads", "/api/session"):
        r = _get(c, path)
        assert r.status_code in (200, 503), path
    assert _get(c, "/api/downloads").status_code == 503
    assert _get(c, "/api/downloads").get_json()["code"] == "unavailable"

    page = _get(c, "/")
    assert page.status_code == 503
    assert b"temporarily unavailable" in page.data


def test_signing_up_is_refused_while_it_is_off(env):
    c, _, _ = env
    site_auth.set_enabled(False)
    r = _post(c, "/api/signup", json={"username": "someone",
                                      "password": PW,
                                      "code": site_auth.invite_code()})
    assert r.status_code == 503


# ---- signing up ------------------------------------------------------------
def test_signup_says_the_same_thing_whether_or_not_the_name_was_free(env):
    """Usernames are the login here, so confirming one exists hands over half a
    credential."""
    c, _, _ = env
    code = site_auth.invite_code()
    first = _post(c, "/api/signup", json={"username": "tanumay",
                                          "email": "a@e.test",
                                          "password": PW, "code": code})
    second = _post(c, "/api/signup", json={"username": "tanumay",
                                           "email": "b@e.test",
                                           "password": PW, "code": code})
    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json(), "the reply revealed the name"
    assert len(site_auth.list_users()) == 1


def test_a_bad_invite_code_says_so(env):
    """That one is about the request itself, not about who else exists."""
    c, _, _ = env
    r = _post(c, "/api/signup", json={"username": "tanumay", "password": PW,
                                      "code": "wrong"})
    assert r.status_code == 400
    assert "invite" in r.get_json()["message"].lower()


def test_a_short_password_says_so(env):
    c, _, _ = env
    r = _post(c, "/api/signup", json={"username": "tanumay", "password": "abc",
                                      "code": site_auth.invite_code()})
    assert r.status_code == 400
    assert "password" in r.get_json()["message"].lower()


# ---- signing in ------------------------------------------------------------
def test_a_good_password_signs_you_in(env):
    c, _, _ = env
    _login(c)
    assert _get(c, "/api/session").get_json()["user"]["username"] == "tanumay"


def test_a_wrong_password_does_not(env):
    c, _, _ = env
    _account()
    r = _post(c, "/api/login", json={"username": "tanumay", "password": "nope12345"})
    assert r.status_code == 401
    assert _get(c, "/api/session").get_json()["user"] is None


def test_disabling_an_account_ends_its_session_immediately(env):
    c, _, _ = env
    _login(c)
    assert _get(c, "/api/downloads").status_code == 200
    u = site_auth.find_user("tanumay")
    site_auth.set_status(u["id"], site_auth.STATUS_DISABLED)
    assert _get(c, "/api/downloads").status_code == 401


def test_resetting_a_password_ends_that_session(env):
    c, _, _ = env
    _login(c)
    u = site_auth.find_user("tanumay")
    site_auth.set_password(u["id"], "a completely different one")
    assert _get(c, "/api/downloads").status_code == 401


def test_repeated_guesses_are_throttled(env):
    c, _, _ = env
    _account()
    for _ in range(site_auth.MAX_ATTEMPTS):
        _post(c, "/api/login", json={"username": "tanumay", "password": "wrong123456"})
    r = _post(c, "/api/login", json={"username": "tanumay", "password": PW})
    assert r.status_code == 429, "the right password got through the lockout"


# ---- seeing only your own --------------------------------------------------
def test_you_see_only_your_own_downloads(env):
    c, q, dl = env
    _login(c)
    _task(q, "tanumay", dl, "mine.mkv")
    _task(q, "someone", dl, "theirs.mkv")
    _task(q, "", dl, "admins.mkv")
    names = [d["name"] for d in _get(c, "/api/downloads").get_json()["downloads"]]
    assert names == ["mine.mkv"]


@pytest.mark.parametrize("suffix,method", [
    ("", "delete"), ("/pause", "post"), ("/resume", "post"),
    ("/files", "get"), ("/file", "get"), ("/file/0", "get"),
])
def test_another_account_s_download_is_a_404_everywhere(env, suffix, method):
    """Ids are uuid4, so guessing is not realistic — but ids leak through
    screenshots, history and logs. This check is the control, not the entropy."""
    c, q, dl = env
    _login(c)
    theirs = _task(q, "someone", dl, "theirs.mkv")
    r = getattr(c, method)("/api/downloads/%s%s" % (theirs.id, suffix),
                           environ_overrides=TUNNEL, json={})
    assert r.status_code == 404, suffix
    assert q.paused == [] and q.removed == [] and q.resumed == []


def test_an_admin_download_is_not_reachable_either(env):
    c, q, dl = env
    _login(c)
    admins = _task(q, "", dl, "admins.mkv")
    assert _get(c, "/api/downloads/%s/file" % admins.id).status_code == 404


# ---- adding ----------------------------------------------------------------
def test_a_magnet_is_accepted_and_lands_in_your_folder(env):
    c, q, dl = env
    _login(c)
    r = _post(c, "/api/downloads", json={"url": "magnet:?xt=urn:btih:" + "a" * 40})
    assert r.status_code == 200
    t = q.tasks[-1]
    assert t.owner == "tanumay"
    assert t.queue_name == site_limits.WEB_QUEUE, "it went into the desktop queue"
    assert os.path.realpath(t.save_path).startswith(
        os.path.realpath(os.path.join(dl, "tanumay")) + os.sep)


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/System32/config/SAM", "chrome://settings",
    "javascript:alert(1)", "ftp://e.test/x", "", "   ",
])
def test_dangerous_schemes_are_refused(env, url):
    c, q, _ = env
    _login(c)
    assert _post(c, "/api/downloads", json={"url": url}).status_code == 400
    assert q.tasks == []


def test_adding_needs_a_session(env):
    c, q, _ = env
    assert _post(c, "/api/downloads",
                 json={"url": "https://e.test/x"}).status_code == 401
    assert q.tasks == []


def test_over_quota_is_refused_with_a_reason(env, monkeypatch):
    c, q, dl = env
    _login(c)
    monkeypatch.setattr(site_limits, "usage_bytes",
                        lambda b, u: site_limits.DEFAULT_QUOTA)
    r = _post(c, "/api/downloads", json={"url": "https://e.test/x"})
    assert r.status_code == 409
    assert r.get_json()["code"] == "limit"
    assert "space" in r.get_json()["message"]
    assert q.tasks == []


def test_a_low_disk_stops_new_downloads(env, monkeypatch):
    c, q, _ = env
    _login(c)
    monkeypatch.setattr(site_limits, "free_bytes", lambda p: 1024)
    r = _post(c, "/api/downloads", json={"url": "https://e.test/x"})
    assert r.status_code == 409
    assert "disk space" in r.get_json()["message"]


def test_past_the_active_limit_it_queues_rather_than_refusing(env):
    """The person asked for it. A queue is the honest answer to "not now"."""
    c, q, dl = env
    _login(c)
    for i in range(site_limits.MAX_ACTIVE_PER_USER):
        _task(q, "tanumay", dl, "busy%d.bin" % i, status=T.DOWNLOADING)
    r = _post(c, "/api/downloads", json={"url": "https://e.test/next"})
    assert r.status_code == 200
    assert r.get_json()["started"] is False
    assert q.started[-1][1] is False


# ---- controlling -----------------------------------------------------------
def test_you_can_pause_and_resume_your_own(env):
    c, q, dl = env
    _login(c)
    t = _task(q, "tanumay", dl, "mine.bin", status=T.DOWNLOADING)
    assert _post(c, "/api/downloads/%s/pause" % t.id).status_code == 200
    assert q.paused == [t.id]
    assert _post(c, "/api/downloads/%s/resume" % t.id).status_code == 200
    assert q.resumed == [t.id]


# ---- taking the file -------------------------------------------------------
def test_a_finished_file_can_be_taken(env):
    c, q, dl = env
    _login(c)
    t = _task(q, "tanumay", dl, "film.mkv", body=b"0123456789")
    r = _get(c, "/api/downloads/%s/file" % t.id)
    assert r.status_code == 200
    assert r.get_data() == b"0123456789"
    assert "attachment" in r.headers["Content-Disposition"]


def test_range_requests_work(env):
    c, q, dl = env
    _login(c)
    t = _task(q, "tanumay", dl, "film.mkv", body=b"0123456789")
    r = _get(c, "/api/downloads/%s/file" % t.id, headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.get_data() == b"2345"


def test_an_unfinished_download_is_not_served(env):
    c, q, dl = env
    _login(c)
    t = _task(q, "tanumay", dl, "part.bin", status=T.DOWNLOADING)
    assert _get(c, "/api/downloads/%s/file" % t.id).status_code == 409


# ---- removing --------------------------------------------------------------
def test_removing_deletes_the_file(env):
    """A site user has no other way to reach it, so leaving it behind creates
    storage nobody can see and nobody can reclaim."""
    c, q, dl = env
    _login(c)
    t = _task(q, "tanumay", dl, "film.mkv")
    path = t.save_path
    assert os.path.isfile(path)
    r = c.delete("/api/downloads/%s" % t.id, environ_overrides=TUNNEL)
    assert r.status_code == 200
    assert r.get_json()["filesRemoved"] == 1
    assert not os.path.exists(path), "the file survived a delete"
    assert q.removed == [t.id]


def test_removing_cannot_reach_outside_the_owner_s_folder(env):
    """The containment check runs on delete as well as on read: a bug here
    would remove somebody else's data rather than merely reveal it."""
    c, q, dl = env
    _login(c)
    outside = os.path.join(dl, "not-yours.bin")
    with open(outside, "wb") as f:
        f.write(b"important")
    t = _task(q, "tanumay", dl, "film.mkv")
    t.save_path = outside          # as if the record had been tampered with
    c.delete("/api/downloads/%s" % t.id, environ_overrides=TUNNEL)
    assert os.path.isfile(outside), "a file outside the user's folder was deleted"


# ---- the built front end ----------------------------------------------------
def test_the_built_bundle_is_served_when_it_exists(env, tmp_path, monkeypatch):
    """A build that never ran the front-end step still works — it serves a
    holding page rather than failing — so the presence of the bundle is what
    switches between them."""
    import site_server
    built = tmp_path / "site"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>real</title>",
                                      encoding="utf-8")
    (built / "assets" / "index-abc.js").write_text("/*bundle*/", encoding="utf-8")
    monkeypatch.setattr(site_server, "site_dir", lambda: str(built))

    c, _, _ = env
    page = _get(c, "/")
    assert page.status_code == 200
    assert b"real" in page.data
    assert b"not been built" not in page.data
    assert _get(c, "/assets/index-abc.js").data == b"/*bundle*/"


def test_the_assets_route_cannot_walk_out_of_the_folder(env, tmp_path, monkeypatch):
    import site_server
    built = tmp_path / "site"
    (built / "assets").mkdir(parents=True)
    (built / "assets" / "ok.js").write_text("x", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    monkeypatch.setattr(site_server, "site_dir", lambda: str(built))

    c, _, _ = env
    for name in ("../secret.txt", "..%2fsecret.txt", "../../secret.txt"):
        r = _get(c, "/assets/" + name)
        assert r.status_code in (404, 400, 308), name
        assert b"nope" not in r.data, name


def test_the_holding_page_is_shown_when_nothing_was_built(env, tmp_path, monkeypatch):
    import site_server
    monkeypatch.setattr(site_server, "site_dir", lambda: str(tmp_path / "absent"))
    c, _, _ = env
    r = _get(c, "/")
    assert r.status_code == 200
    assert b"not been built yet" in r.data


# ---- the audit trail --------------------------------------------------------
def test_the_things_worth_a_record_are_recorded(env):
    """Once credentials are handed out this log is the only account of what an
    account actually did."""
    import site_audit
    c, q, dl = env
    _login(c)
    _post(c, "/api/downloads", json={"url": "magnet:?xt=urn:btih:" + "a" * 40})
    t = _task(q, "tanumay", dl, "film.mkv", body=b"0123456789")
    _get(c, "/api/downloads/%s/file" % t.id)
    c.delete("/api/downloads/%s" % t.id, environ_overrides=TUNNEL)

    actions = [r["action"] for r in site_audit.tail()]
    for expected in ("signin", "add", "download", "remove"):
        assert expected in actions, expected
    assert all(r["user"] == "tanumay" for r in site_audit.tail())


def test_a_failed_sign_in_is_recorded_with_the_name_that_was_tried(env):
    """The record of an attempt, not proof a name exists — and the only place
    a wrong username is written down at all."""
    import site_audit
    c, _, _ = env
    _account("tanumay")
    _post(c, "/api/login", json={"username": "Nobody", "password": "wrong123456"})
    row = site_audit.tail()[0]
    assert row["action"] == "signin-failed"
    assert row["user"] == "nobody"


def test_a_file_is_recorded_before_it_is_sent(env):
    """The response streams, so recording afterwards would mean recording when
    the transfer finished — and a cancelled one would leave no trace."""
    import site_audit
    c, q, dl = env
    _login(c)
    t = _task(q, "tanumay", dl, "film.mkv", body=b"x" * 100)
    _get(c, "/api/downloads/%s/file" % t.id)
    row = next(r for r in site_audit.tail() if r["action"] == "download")
    assert row["detail"]["name"] == "film.mkv"
    assert row["detail"]["size"] == 100


def test_nothing_another_account_did_is_attributed_to_you(env):
    import site_audit
    c, q, dl = env
    _login(c, "tanumay")
    _post(c, "/api/downloads", json={"url": "https://e.test/mine.bin"})
    assert [r["user"] for r in site_audit.tail(user="someone")] == []
