"""Accounts for the public users site.

This store faces the internet through a tunnel, so the tests care most about
what it refuses: bad invite codes, usernames that would escape a folder, and
anything that would let one account's session speak for another.
"""
import json
import os

import pytest

import site_auth
import utils


PW = "correct horse battery"


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    return tmp_path


def _register(name="tanumay", email="t@example.test", pw=PW):
    return site_auth.create_user(name, email, pw, site_auth.invite_code())


# ---- the switch ------------------------------------------------------------
def test_the_site_is_off_until_switched_on():
    assert site_auth.is_enabled() is False
    site_auth.set_enabled(True)
    assert site_auth.is_enabled() is True


# ---- invite codes ----------------------------------------------------------
def test_an_invite_code_is_created_on_first_read_and_then_stays():
    a = site_auth.invite_code()
    assert len(a) >= 8
    assert site_auth.invite_code() == a, "the code changed under the user"


def test_registration_without_the_code_is_refused():
    """The signup form is reachable by anyone who finds the URL, and the URL is
    public HTTPS that gets crawled."""
    with pytest.raises(ValueError, match="invite code"):
        site_auth.create_user("tanumay", "t@example.test", PW, "not-the-code")
    assert site_auth.list_users() == []


def test_rotating_the_code_stops_the_old_one():
    old = site_auth.invite_code()
    new = site_auth.rotate_invite_code()
    assert new != old
    assert site_auth.check_invite(old) is False
    assert site_auth.check_invite(new) is True


def test_a_trivial_invite_code_is_refused():
    with pytest.raises(ValueError):
        site_auth.set_invite_code("abc")


# ---- usernames -------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "", "  ", "ab",                       # too short
    "a" * 33,                             # too long
    "../etc", "a/b", "a\\b",              # would escape the download folder
    ".hidden", "trailing.",               # leading/trailing dot
    "has space", "emoji\U0001F600",
    "con", "COM1", "nul",                 # Windows reserved device names
    "admin", "HyperFetch", "root",        # ours
])
def test_usernames_that_would_break_a_folder_are_refused(bad):
    """Usernames become directory names, so they are checked against what a
    filesystem accepts, not what looks tidy."""
    assert site_auth.username_error(bad) != "", bad


@pytest.mark.parametrize("ok", ["tanumay", "tan.u_may", "a-b-c", "user123", "x9y"])
def test_reasonable_usernames_are_accepted(ok):
    assert site_auth.username_error(ok) == "", ok


def test_a_bad_username_is_rejected_not_rewritten():
    """Silently turning "my name" into "my_name" hands someone a login they
    will not remember."""
    with pytest.raises(ValueError):
        site_auth.create_user("my name", "a@b.test", PW, site_auth.invite_code())


# ---- registration ----------------------------------------------------------
def test_an_account_can_be_created_and_used():
    u = _register()
    assert u["username"] == "tanumay"
    assert u["status"] == site_auth.STATUS_ACTIVE
    assert u["plan"] == "unlimited"
    assert u["provider"] == "local"
    assert site_auth.verify("tanumay", PW)["id"] == u["id"]


def test_the_public_view_never_carries_the_secret():
    _register()
    blob = json.dumps(site_auth.list_users())
    stored = json.load(open(os.path.join(utils.app_data_dir(), "site_auth.json"),
                            encoding="utf-8"))
    secret = stored["users"][0]
    assert secret["hash"] not in blob
    assert secret["salt"] not in blob
    assert PW not in blob


def test_the_password_is_never_stored_in_the_clear(appdata):
    _register()
    raw = open(os.path.join(str(appdata), "site_auth.json"), encoding="utf-8").read()
    assert PW not in raw


def test_a_short_password_is_refused():
    with pytest.raises(ValueError):
        site_auth.create_user("tanumay", "t@e.test", "short", site_auth.invite_code())


def test_usernames_are_unique_regardless_of_case():
    _register("tanumay")
    with pytest.raises(ValueError, match="taken"):
        site_auth.create_user("TanuMay", "other@e.test", PW, site_auth.invite_code())


def test_an_email_is_not_reused():
    _register("tanumay", "t@e.test")
    with pytest.raises(ValueError, match="email"):
        site_auth.create_user("someone", "T@E.test", PW, site_auth.invite_code())


# ---- signing in ------------------------------------------------------------
def test_the_wrong_password_does_not_get_in():
    _register()
    assert site_auth.verify("tanumay", "wrong one here") is None


def test_an_unknown_user_does_not_get_in():
    assert site_auth.verify("nobody", PW) is None


def test_email_works_as_a_login():
    _register("tanumay", "t@example.test")
    assert site_auth.verify("T@Example.test", PW) is not None


def test_the_username_is_not_case_sensitive():
    _register("tanumay")
    assert site_auth.verify("TANUMAY", PW) is not None


def test_a_disabled_account_cannot_sign_in():
    """Decided in one place, so no later route has to remember to check."""
    u = _register()
    site_auth.set_status(u["id"], site_auth.STATUS_DISABLED)
    assert site_auth.verify("tanumay", PW) is None


def test_a_pending_account_cannot_sign_in():
    u = _register()
    site_auth.set_status(u["id"], site_auth.STATUS_PENDING)
    assert site_auth.verify("tanumay", PW) is None


# ---- sessions --------------------------------------------------------------
def test_each_account_has_its_own_session_stamp():
    a = _register("aaa", "a@e.test")
    b = _register("bbb", "b@e.test")
    assert site_auth.stamp(a["id"]) != site_auth.stamp(b["id"])


def test_resetting_one_password_signs_out_only_that_account():
    a = _register("aaa", "a@e.test")
    b = _register("bbb", "b@e.test")
    a_before, b_before = site_auth.stamp(a["id"]), site_auth.stamp(b["id"])

    site_auth.set_password(a["id"], "a whole new password")

    assert site_auth.stamp(a["id"]) != a_before, "the reset did not end the session"
    assert site_auth.stamp(b["id"]) == b_before, "an unrelated account was signed out"
    assert site_auth.verify("aaa", "a whole new password") is not None


def test_disabling_an_account_invalidates_its_live_session():
    """Otherwise a signed-in tab keeps working until the app restarts."""
    u = _register()
    before = site_auth.stamp(u["id"])
    site_auth.set_status(u["id"], site_auth.STATUS_DISABLED)
    assert site_auth.stamp(u["id"]) != before


def test_a_deleted_account_has_no_stamp():
    u = _register()
    site_auth.delete_user(u["id"])
    assert site_auth.stamp(u["id"]) == ""
    assert site_auth.verify("tanumay", PW) is None


def test_the_site_key_is_not_the_control_key():
    """Two apps, two ports, two cookies — a session minted on the public site
    must never authenticate against the LAN control page."""
    import web_auth
    web_auth.set_password("correct horse battery", user="admin")
    assert site_auth.secret_key() != web_auth.secret_key()


def test_the_site_key_survives_a_restart():
    """A restart must not sign every phone out."""
    assert site_auth.secret_key() == site_auth.secret_key()


# ---- housekeeping ----------------------------------------------------------
def test_deleting_an_account_leaves_the_others_alone():
    a = _register("aaa", "a@e.test")
    _register("bbb", "b@e.test")
    assert site_auth.delete_user(a["id"]) is True
    assert [u["username"] for u in site_auth.list_users()] == ["bbb"]
    assert site_auth.delete_user(a["id"]) is False


def test_a_corrupt_store_does_not_take_the_app_down(appdata):
    open(os.path.join(str(appdata), "site_auth.json"), "w").write("{not json")
    assert site_auth.list_users() == []
    assert site_auth.is_enabled() is False
    assert site_auth.verify("anyone", PW) is None


def test_the_account_count_is_capped(monkeypatch):
    monkeypatch.setattr(site_auth, "MAX_USERS", 2)
    _register("aaa", "a@e.test")
    _register("bbb", "b@e.test")
    with pytest.raises(ValueError, match="not accepting"):
        _register("ccc", "c@e.test")


# ---- throttle --------------------------------------------------------------
def test_repeated_failures_lock_a_key_out():
    t = site_auth.Throttle(max_attempts=3, lockout=60)
    for _ in range(3):
        t.record_failure("1.2.3.4")
    assert t.locked_for("1.2.3.4") > 0
    assert t.locked_for("5.6.7.8") == 0, "one device locked out another"


def test_a_success_clears_the_counter():
    t = site_auth.Throttle(max_attempts=3, lockout=60)
    t.record_failure("1.2.3.4")
    t.record_success("1.2.3.4")
    t.record_failure("1.2.3.4")
    assert t.locked_for("1.2.3.4") == 0


def test_the_lockout_does_not_extend_itself():
    """Otherwise a bot hammering the form keeps the real user locked out."""
    t = site_auth.Throttle(max_attempts=2, lockout=60)
    t.record_failure("x")
    t.record_failure("x")
    first = t.locked_for("x")
    t.record_failure("x")
    assert t.locked_for("x") <= first
