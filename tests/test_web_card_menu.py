"""The routes behind the browser page's right-click menu.

These run against a real :class:`QueueManager` rather than a stand-in. The
reason is a bug this file exists to prevent: the routes call ``queue.move``
and ``queue.force_start``, and a hand-written fake that simply lacks them
turns every one of those calls into a 500 that no test notices. A fake that
*has* them but reorders differently is worse still — it agrees with whatever
the route does.

``QueueManager`` starts its scheduler thread in ``__init__``, so the fixture
stops it again before adding anything. Otherwise the scheduler picks each task
straight back off the heap and really tries to download it — which empties the
pending order these tests are about, and reaches the network besides.
"""
import os

import pytest

import task as T
import utils
import web_auth
from api_server import create_app
from queue_manager import QueueManager

PW = "correct horse battery"
LAN = "192.168.1.50"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    q = QueueManager()
    q.shutdown()               # before any task exists, so none is ever popped

    def add(name, status=T.QUEUED):
        t = T.DownloadTask("https://example.test/" + name,
                           str(tmp_path / name), filename=name)
        t.id = "id-" + name
        t.total_size, t.downloaded = 1000, 0
        q.add_task(t)          # start=True is what puts it in the pending heap
        t.status = status
        return t

    app = create_app(q, str(tmp_path), pending=None, token="tok")
    app.config["TESTING"] = True
    c = app.test_client()
    web_auth.set_password(PW, user="admin")
    web_auth.set_enabled(True)
    assert c.post("/api/login", json={"username": "admin", "password": PW},
                  environ_overrides={"REMOTE_ADDR": LAN}).status_code == 200
    return c, q, add


def _post(c, path, **kw):
    return c.post(path, environ_overrides={"REMOTE_ADDR": LAN}, **kw)


def _queued_order(q):
    return [t.filename for t in sorted(q._heap)]


# ------------------------------------------------------------------- move
def test_move_to_top_reorders_the_pending_queue(env):
    c, q, add = env
    a, b, d = add("a.bin"), add("b.bin"), add("c.bin")
    assert _queued_order(q) == ["a.bin", "b.bin", "c.bin"]

    assert _post(c, "/api/downloads/id-c.bin/move",
                 json={"where": "top"}).status_code == 200
    assert _queued_order(q) == ["c.bin", "a.bin", "b.bin"]


@pytest.mark.parametrize("where,expected", [
    ("top", ["b.bin", "a.bin", "c.bin"]),
    ("up", ["b.bin", "a.bin", "c.bin"]),
    ("down", ["a.bin", "c.bin", "b.bin"]),
    ("bottom", ["a.bin", "c.bin", "b.bin"]),
])
def test_each_direction_moves_the_middle_one_where_it_says(env, where, expected):
    c, q, add = env
    add("a.bin"), add("b.bin"), add("c.bin")
    assert _post(c, "/api/downloads/id-b.bin/move",
                 json={"where": where}).status_code == 200
    assert _queued_order(q) == expected


def test_move_rejects_a_direction_it_does_not_know(env):
    """Guarding this matters because the value is passed straight through from
    the browser, and an unrecognised one would otherwise silently do nothing."""
    c, q, add = env
    add("a.bin"), add("b.bin")
    r = _post(c, "/api/downloads/id-b.bin/move", json={"where": "sideways"})
    assert r.status_code == 400
    assert _queued_order(q) == ["a.bin", "b.bin"]


def test_moving_a_task_that_is_not_waiting_does_nothing(env):
    """A running or finished task has no place in the pending order. The menu
    hides the option, but the route must not rely on the menu."""
    c, q, add = env
    add("a.bin")
    done = add("done.bin", status=T.COMPLETED)
    q._heap.remove(done)
    assert _post(c, "/api/downloads/id-done.bin/move",
                 json={"where": "top"}).status_code == 200
    assert _queued_order(q) == ["a.bin"]


# ------------------------------------------------------------------ force
def test_force_starts_a_paused_task(env):
    c, q, add = env
    t = add("a.bin", status=T.PAUSED)
    assert _post(c, "/api/downloads/id-a.bin/force").status_code == 200
    assert t.status != T.PAUSED
    assert not t.pause_requested


def test_force_leaves_a_finished_task_alone(env):
    c, q, add = env
    t = add("a.bin", status=T.COMPLETED)
    assert _post(c, "/api/downloads/id-a.bin/force").status_code == 200
    assert t.status == T.COMPLETED


# ------------------------------------------------------------------ limit
def test_limit_sets_the_per_task_rate(env):
    c, q, add = env
    t = add("a.bin")
    r = _post(c, "/api/downloads/id-a.bin/limit", json={"bps": 62500})
    assert r.status_code == 200 and r.get_json()["bps"] == 62500
    assert t.speed_limit == 62500


def test_limit_zero_means_unlimited(env):
    c, q, add = env
    t = add("a.bin")
    t.speed_limit = 62500
    assert _post(c, "/api/downloads/id-a.bin/limit",
                 json={"bps": 0}).status_code == 200
    assert t.speed_limit == 0


def test_a_negative_limit_is_clamped_rather_than_throttling_to_nothing(env):
    c, q, add = env
    t = add("a.bin")
    assert _post(c, "/api/downloads/id-a.bin/limit",
                 json={"bps": -5}).status_code == 200
    assert t.speed_limit == 0


def test_a_limit_that_is_not_a_number_is_refused(env):
    c, q, add = env
    t = add("a.bin")
    r = _post(c, "/api/downloads/id-a.bin/limit", json={"bps": "fast"})
    assert r.status_code == 400
    assert t.speed_limit in (0, None)


# ----------------------------------------------------------------- rename
def test_rename_moves_a_finished_file_on_disk(env, tmp_path):
    c, q, add = env
    t = add("a.bin", status=T.COMPLETED)
    open(t.save_path, "wb").write(b"payload")

    r = _post(c, "/api/downloads/id-a.bin/rename", json={"name": "better.bin"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert t.filename == "better.bin"
    assert os.path.basename(t.save_path) == "better.bin"
    assert open(t.save_path, "rb").read() == b"payload"
    assert not os.path.exists(str(tmp_path / "a.bin"))


def test_rename_cannot_climb_out_of_the_download_folder(env, tmp_path):
    """The name arrives from the browser, so `../` in it must not be able to
    write outside the folder the user chose."""
    c, q, add = env
    t = add("a.bin", status=T.COMPLETED)
    open(t.save_path, "wb").write(b"payload")
    parent = os.path.dirname(str(tmp_path))

    _post(c, "/api/downloads/id-a.bin/rename",
          json={"name": "../../escaped.bin"})

    assert os.path.dirname(os.path.abspath(t.save_path)) == str(tmp_path)
    assert ".." not in t.filename
    assert not os.path.exists(os.path.join(parent, "escaped.bin"))


def test_rename_refuses_a_name_with_nothing_usable_in_it(env):
    c, q, add = env
    t = add("a.bin", status=T.COMPLETED)
    r = _post(c, "/api/downloads/id-a.bin/rename", json={"name": "   "})
    assert r.status_code == 400
    assert t.filename == "a.bin"


def test_renaming_to_the_same_name_is_not_an_error(env):
    c, q, add = env
    t = add("a.bin", status=T.COMPLETED)
    open(t.save_path, "wb").write(b"payload")
    assert _post(c, "/api/downloads/id-a.bin/rename",
                 json={"name": "a.bin"}).status_code == 200
    assert t.filename == "a.bin"


def test_renaming_an_unfinished_task_retargets_it_without_touching_bytes(env):
    """Its bytes live in an id-keyed .hfdownload temp, so only the destination
    changes; finalize lands on the new name."""
    c, q, add = env
    t = add("a.bin", status=T.PAUSED)
    assert _post(c, "/api/downloads/id-a.bin/rename",
                 json={"name": "renamed.bin"}).status_code == 200
    assert t.filename == "renamed.bin"
    assert os.path.basename(t.save_path) == "renamed.bin"


# ------------------------------------------------------------------- misc
@pytest.mark.parametrize("path", ["force", "limit", "move", "rename"])
def test_an_unknown_task_is_a_404_not_a_crash(env, path):
    c, _, _ = env
    r = _post(c, "/api/downloads/no-such-id/" + path,
              json={"where": "top", "bps": 0, "name": "x.bin"})
    assert r.status_code == 404


def test_the_list_carries_what_the_menu_needs_to_draw_itself(env):
    """The menu ticks the active speed limit and offers "Copy link", so both
    have to survive the trip to the browser."""
    c, q, add = env
    t = add("a.bin")
    t.speed_limit = 62500
    d = c.get("/api/downloads",
              environ_overrides={"REMOTE_ADDR": LAN}).get_json()["downloads"][0]
    assert d["speedLimit"] == 62500
    assert d["url"] == "https://example.test/a.bin"
