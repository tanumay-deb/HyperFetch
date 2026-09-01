"""Reading Tailscale's state for the settings page.

Written against fixed CLI output rather than a live tailnet, so what these
really pin down is the *degradation*: a missing binary, a signed-out install, a
timeout and a changed output shape must all produce a usable answer instead of
an exception in a settings dialog.
"""
import subprocess

import pytest

import site_tunnel


PORT = 5001

# Trimmed from `tailscale status --json`. Only Self.DNSName is read.
STATUS_JSON = """{
  "Version": "1.76.1",
  "BackendState": "Running",
  "Self": {
    "ID": "n123",
    "HostName": "hyperfetch",
    "DNSName": "hyperfetch.tail1a2b3c.ts.net.",
    "Online": true
  }
}"""

FUNNEL_ON = """# Funnel on:
#     - https://hyperfetch.tail1a2b3c.ts.net

https://hyperfetch.tail1a2b3c.ts.net (Funnel on)
|-- / proxy http://127.0.0.1:5001
"""


def _fake_cli(monkeypatch, outputs, path="/usr/bin/tailscale"):
    """Answer each `tailscale <verb>` with a canned string."""
    monkeypatch.setattr(site_tunnel, "tailscale_path", lambda: path)

    def run(args, **kw):
        verb = " ".join(args[1:3])
        for key, text in outputs.items():
            if verb.startswith(key):
                return subprocess.CompletedProcess(args, 0, text, "")
        return subprocess.CompletedProcess(args, 1, "", "no")
    monkeypatch.setattr(site_tunnel.subprocess, "run", run)


# ---- not installed ---------------------------------------------------------
def test_a_machine_without_tailscale_still_gets_an_answer(monkeypatch):
    monkeypatch.setattr(site_tunnel, "tailscale_path", lambda: "")
    s, note = site_tunnel.summary(PORT)
    assert s["installed"] is False
    assert s["url"] == ""
    assert "not installed" in note


def test_nothing_is_run_when_it_is_not_installed(monkeypatch):
    monkeypatch.setattr(site_tunnel, "tailscale_path", lambda: "")

    def boom(*a, **k):
        raise AssertionError("a subprocess was started anyway")
    monkeypatch.setattr(site_tunnel.subprocess, "run", boom)
    site_tunnel.state(PORT)


# ---- installed, various states ---------------------------------------------
def test_signed_out_says_so_rather_than_guessing(monkeypatch):
    _fake_cli(monkeypatch, {})            # every call fails
    s, note = site_tunnel.summary(PORT)
    assert s["installed"] is True
    assert s["signed_in"] is False
    assert s["url"] == ""
    assert "tailscale up" in note


def test_signed_in_gives_the_address(monkeypatch):
    _fake_cli(monkeypatch, {"status": STATUS_JSON})
    s, note = site_tunnel.summary(PORT)
    assert s["signed_in"] is True
    assert s["machine"] == "hyperfetch.tail1a2b3c.ts.net"
    assert s["url"] == "https://hyperfetch.tail1a2b3c.ts.net/"
    assert s["funnel_on"] is False
    assert "tailscale funnel %d" % PORT in note


def test_the_trailing_dot_is_dropped(monkeypatch):
    """DNSName is a fully qualified name and ends with a dot, which would give
    a URL that looks broken to anyone reading it."""
    _fake_cli(monkeypatch, {"status": STATUS_JSON})
    assert not site_tunnel.state(PORT)["machine"].endswith(".")


def test_a_running_funnel_is_noticed(monkeypatch):
    _fake_cli(monkeypatch, {"status": STATUS_JSON, "funnel status": FUNNEL_ON})
    s, note = site_tunnel.summary(PORT)
    assert s["funnel_on"] is True
    assert "anywhere" in note


def test_a_funnel_on_a_different_port_is_not_ours(monkeypatch):
    other = FUNNEL_ON.replace("5001", "9999")
    _fake_cli(monkeypatch, {"status": STATUS_JSON, "funnel status": other})
    assert site_tunnel.state(PORT)["funnel_on"] is False


# ---- degradation -----------------------------------------------------------
def test_a_changed_output_shape_does_not_raise(monkeypatch):
    """The CLI's output is not a contract. A settings page must degrade to
    "unknown" rather than throw."""
    for junk in ("", "not json", "[]", "null", '{"Self": null}',
                 '{"Self": {"DNSName": null}}', '{"Self": "a string"}'):
        _fake_cli(monkeypatch, {"status": junk})
        s = site_tunnel.state(PORT)
        assert s["installed"] is True
        assert s["url"] == ""


def test_a_hanging_cli_does_not_hang_the_dialog(monkeypatch):
    monkeypatch.setattr(site_tunnel, "tailscale_path", lambda: "/usr/bin/tailscale")

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, site_tunnel.TIMEOUT)
    monkeypatch.setattr(site_tunnel.subprocess, "run", slow)
    s, note = site_tunnel.summary(PORT)
    assert s["signed_in"] is False
    assert note


def test_an_os_error_is_survivable(monkeypatch):
    monkeypatch.setattr(site_tunnel, "tailscale_path", lambda: "/usr/bin/tailscale")

    def broken(args, **kw):
        raise OSError("exec format error")
    monkeypatch.setattr(site_tunnel.subprocess, "run", broken)
    assert site_tunnel.state(PORT)["url"] == ""


def test_the_timeout_is_short_enough_for_a_dialog():
    assert site_tunnel.TIMEOUT <= 5


# ---- what it must never do -------------------------------------------------
def test_this_module_never_turns_a_funnel_on():
    """Publishing this machine to the internet should be a command somebody
    types on purpose, never a side effect of opening a settings page."""
    import inspect
    src = inspect.getsource(site_tunnel)
    for verb in ('"funnel", "%d"', "'funnel', '%d'", '"up"', "serve --bg",
                 "funnel --bg"):
        assert verb not in src, verb
    # The only funnel/serve calls are status reads.
    for line in src.splitlines():
        if "_run([" in line:
            assert "status" in line, "a tailscale call that is not a status read: " + line
