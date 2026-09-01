"""Finding out whether the users site is reachable from outside, via Tailscale.

This module only ever **reads**. It never turns a funnel on, because doing that
publishes this machine to the internet and that should be a command somebody
types on purpose, not a side effect of opening a settings page.

Everything is parsed defensively and every call has a short timeout: the CLI's
exact output is not a contract, and the settings dialog must not hang because a
subprocess is thinking.
"""
import json
import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("hyperfetch.tunnel")

TIMEOUT = 4.0

_WINDOWS_GUESSES = [
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
]


def tailscale_path():
    """The tailscale CLI, or "" when it is not installed.

    PATH first, then the usual install location — the Windows installer does
    not always put it on PATH for an already-open session.
    """
    found = shutil.which("tailscale")
    if found:
        return found
    if sys.platform == "win32":
        for p in _WINDOWS_GUESSES:
            if os.path.isfile(p):
                return p
    return ""


def _run(args):
    """Run the CLI and return stdout, or "" on any failure at all."""
    exe = tailscale_path()
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe] + args, capture_output=True, text=True, timeout=TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("tailscale %s failed: %s", " ".join(args), e)
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout or ""


def _dns_name(status_json):
    """This machine's tailnet name, e.g. hyperfetch.tail1a2b3c.ts.net.

    Read from Self.DNSName, which is where it has lived for a long time, but
    the whole thing is wrapped: a CLI output shape is not a contract, and a
    settings page should degrade to "unknown" rather than raise.
    """
    try:
        d = json.loads(status_json)
    except (ValueError, TypeError):
        return ""
    try:
        name = (d.get("Self") or {}).get("DNSName") or ""
    except AttributeError:
        return ""
    return name.strip().rstrip(".")


def state(port):
    """What we can tell about Tailscale and the funnel, as plain data.

    Every field degrades on its own, so a half-working install still produces a
    usable answer rather than nothing.
    """
    exe = tailscale_path()
    info = {
        "installed": bool(exe),
        "path": exe,
        "signed_in": False,
        "machine": "",
        "url": "",
        "funnel_on": False,
        "command": "tailscale funnel %d" % port,
    }
    if not exe:
        return info

    status = _run(["status", "--json"])
    name = _dns_name(status)
    if name:
        info["signed_in"] = True
        info["machine"] = name
        info["url"] = "https://%s/" % name

    # `funnel status` prints human text whose exact shape is not promised, so
    # this looks for the port rather than trying to parse a table. A wrong
    # answer here only means the hint says "not on yet" — nothing depends on it.
    funnel = _run(["funnel", "status"]) or _run(["serve", "status"])
    if funnel and str(port) in funnel and "http" in funnel.lower():
        info["funnel_on"] = True
    return info


def summary(port):
    """One sentence for the settings page, and the URL when there is one."""
    s = state(port)
    if not s["installed"]:
        return s, ("Tailscale is not installed. It is the simplest way to reach "
                   "this from outside your network without opening a port on "
                   "your router.")
    if not s["signed_in"]:
        return s, ("Tailscale is installed but not signed in. Run  tailscale up  "
                   "once, then come back.")
    if not s["funnel_on"]:
        return s, ("Ready. Run  %s  to publish it, then this address works from "
                   "anywhere." % s["command"])
    return s, "Reachable from anywhere at the address below."
