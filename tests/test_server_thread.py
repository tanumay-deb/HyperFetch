"""What the desktop window's server thread has to survive.

This exists because of a failure that was invisible for a long time. The
thread in ``gui2.app`` wraps ``run_server`` in a 20-second retry loop so a
restart can ride out the previous instance still holding the port. It caught
``OSError`` — but werkzeug catches the bind error itself and calls
``sys.exit(1)``, and ``SystemExit`` derives from ``BaseException``, not
``Exception``. So the one failure the loop existed to handle was the one it
could not catch: the thread died on the first attempt, logged nothing, and the
window came up looking perfectly healthy with no server behind it. The only
trace was a crash file, and the first anyone knew was that the browser
extension and the phone could not reach the app.

These tests are about the seam between werkzeug and that loop, so they use a
real socket on a real busy port rather than a mock that would just agree with
whatever we already believe.
"""
import ast
import io
import os
import socket

import pytest

import utils
from api_server import run_server
from queue_manager import QueueManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What gui2.app's serve() promises to catch. Kept here so the assertion below
# reads as a claim about behaviour rather than about source text.
CAUGHT = (OSError, SystemExit)


@pytest.fixture
def busy_port(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    s = socket.socket()
    # No SO_REUSEADDR here, and SO_EXCLUSIVEADDRUSE where it exists: on Windows
    # SO_REUSEADDR lets a second socket bind the same address, so the port
    # would not be busy at all and run_server would happily start and block.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def test_a_busy_port_raises_something_the_window_actually_catches(busy_port, tmp_path):
    """The whole bug in one assertion: if werkzeug raises a type outside this
    tuple, the retry loop dies silently again."""
    q = QueueManager()
    q.shutdown()
    try:
        run_server(q, str(tmp_path), port=busy_port, token="tok")
    except CAUGHT:
        pass                                  # what serve() expects
    except BaseException as e:                 # noqa: BLE001 - that is the point
        pytest.fail(
            "a busy port raised %s, which gui2.app's serve() does not catch — "
            "the server thread would die without retrying or logging"
            % type(e).__name__)
    else:
        pytest.fail("run_server returned instead of failing on a taken port")


def test_system_exit_would_not_be_caught_by_oserror_alone():
    """Pins the reason OSError on its own was not enough, so nobody narrows
    the tuple back down later."""
    assert not issubclass(SystemExit, OSError)
    assert not issubclass(SystemExit, Exception)
    assert issubclass(SystemExit, BaseException)


def _serve_body():
    """The source of the serve() closure inside gui2.app."""
    src = io.open(os.path.join(ROOT, "gui2", "app.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "serve":
            return node
    raise AssertionError("gui2.app no longer defines serve()")


def test_the_retry_loop_still_catches_both():
    """Read from the AST rather than grepped, so a comment mentioning
    SystemExit cannot make this pass."""
    caught = set()
    for node in ast.walk(_serve_body()):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            t = node.type
            for part in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if isinstance(part, ast.Name):
                    caught.add(part.id)
    assert {"OSError", "SystemExit"} <= caught, (
        "serve() catches %s; dropping SystemExit brings back a server thread "
        "that dies silently on a busy port" % (sorted(caught) or "nothing"))


def test_giving_up_leaves_something_for_the_window_to_show():
    """A log line alone was not enough — nobody reads the log when the window
    looks fine. The failure has to reach the user."""
    body = ast.unparse(_serve_body())
    assert "_server_error" in body, (
        "serve() no longer records why it gave up, so the window cannot tell "
        "the user the server is not running")
