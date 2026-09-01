"""HyperFetch Server — the download engine and the users site, no window.

For a machine that sits and serves: an old laptop, a NAS, a Pi, a VPS. It runs
the same engine as the desktop app and none of its interface, so it needs no
Qt, no display, and about a third less disk.

    python server.py                 run it
    python server.py --check         start, prove everything came up, exit 0

    python server.py users           list the accounts
    python server.py users add NAME  create one (asks for the password)
    python server.py users passwd NAME
    python server.py users disable NAME  /  enable NAME  /  remove NAME
    python server.py invite          show the code
    python server.py invite --new [--days N]
    python server.py site on | off

There is no settings window here, so account management is the command line.
The desktop app deliberately does not offer it: a download manager is not a
hosting service, and mixing the two is how somebody publishes a machine they
did not mean to.

What it does NOT do, deliberately:

- It never starts a tunnel. Publishing a machine to the internet should be a
  command somebody types, not something a service decides.
- It does not serve the extension routes off-machine. Those stay loopback-only
  exactly as they do on the desktop.
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time

import utils

log = logging.getLogger("hyperfetch.server")


def _load_settings():
    """Read the same settings.json the desktop app writes.

    Shared on purpose: a machine that has run both should not need its
    download folder configured twice.
    """
    s = utils.load_json(os.path.join(utils.app_data_dir(), "settings.json"), {})
    save_dir = s.get("save_dir") or utils.default_download_dir()
    if not os.path.isdir(save_dir):
        save_dir = utils.default_download_dir()
    return s, save_dir


def build(save_dir=None, settings=None):
    """The queue, restored from disk, with housekeeping running.

    Returned rather than run, so `--check` can assert it came up without
    starting the servers.
    """
    from queue_manager import QueueManager

    s = settings if settings is not None else _load_settings()[0]
    save_dir = save_dir or (_load_settings()[1])

    queues = s.get("queues") or [{"name": "Main",
                                  "max_concurrent": int(s.get("max_concurrent", 3))}]
    q = QueueManager(queues=queues, segments=int(s.get("segments", 8)))

    utils.VERIFY_TLS = bool(s.get("verify_tls", True))
    utils.global_limiter.set_limit(int(s.get("global_speed_limit", 0)))

    state = os.path.join(utils.app_data_dir(), "downloads.json")
    restored, skipped = q.restore(utils.load_json(state, []))
    log.info("restored %d download(s), %d skipped", restored, skipped)

    q.start_housekeeping(save_dir)
    return q, save_dir


def _save_state(queue):
    """Persist the download list, the same file the desktop app reads."""
    try:
        utils.save_json(os.path.join(utils.app_data_dir(), "downloads.json"),
                        [t.to_dict() for t in list(queue.tasks)])
    except Exception:
        log.exception("could not save the download list")


def _state_saver(queue, every=30.0):
    """The desktop app saves on quit and on change; a service can be killed
    without warning, so this writes on a timer instead."""
    def loop():
        while True:
            time.sleep(every)
            _save_state(queue)
    threading.Thread(target=loop, name="hyperfetch-state", daemon=True).start()


def _admin(argv):
    """The `users`, `invite` and `site` subcommands. Returns an exit code.

    Kept out of main()'s startup path: these run against the store and exit,
    without building a queue or binding anything.
    """
    import getpass
    import site_auth
    import site_limits

    cmd = argv[0]
    rest = argv[1:]

    if cmd == "site":
        if rest and rest[0] in ("on", "off"):
            site_auth.set_enabled(rest[0] == "on")
        print("users site: %s" % ("on" if site_auth.is_enabled() else "off"))
        if site_auth.is_enabled() and not site_auth.list_users():
            print("  no accounts yet — `server.py users add NAME`")
        return 0

    if cmd == "invite":
        if "--new" in rest:
            site_auth.rotate_invite_code()
        if "--days" in rest:
            days = float(rest[rest.index("--days") + 1])
            site_auth.set_invite_expiry(time.time() + days * 86400 if days else 0)
        print("invite code: %s" % site_auth.invite_code())
        exp = site_auth.invite_expiry()
        if exp:
            left = (exp - time.time()) / 86400.0
            print("  %s" % ("expired" if left <= 0 else "expires in %.1f days" % left))
        else:
            print("  never expires")
        return 0

    if cmd != "users":
        print("unknown command: %s" % cmd, file=sys.stderr)
        return 2

    action = rest[0] if rest else "list"
    name = rest[1] if len(rest) > 1 else ""

    if action == "list":
        users = site_auth.list_users()
        if not users:
            print("no accounts")
            return 0
        _s, save_dir = _load_settings()
        for u in users:
            used = site_limits.usage_bytes(save_dir, u["username"])
            print("%-20s %-9s %8s of %-8s %s"
                  % (u["username"], u["status"],
                     site_limits.human(used), site_limits.human(u["quota"]),
                     u["email"] or ""))
        return 0

    if action not in ("list", "add", "passwd", "disable", "enable", "remove"):
        print("unknown: users %s" % action, file=sys.stderr)
        return 2
    if not name:
        print("which account?", file=sys.stderr)
        return 2

    if action == "add":
        # Prompted rather than passed as an argument: a password on the command
        # line ends up in shell history and in the process list.
        pw = getpass.getpass("password for %s: " % name)
        if pw != getpass.getpass("again: "):
            print("they did not match", file=sys.stderr)
            return 1
        try:
            u = site_auth.create_user_as_admin(name, "", pw)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print("created %s" % u["username"])
        return 0

    u = site_auth.find_user(name)
    if not u:
        print("no account called %s" % name, file=sys.stderr)
        return 1

    if action == "passwd":
        pw = getpass.getpass("new password for %s: " % u["username"])
        try:
            site_auth.set_password(u["id"], pw)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print("changed — %s is signed out everywhere" % u["username"])
        return 0
    if action in ("disable", "enable"):
        site_auth.set_status(u["id"], site_auth.STATUS_DISABLED
                             if action == "disable" else site_auth.STATUS_ACTIVE)
        print("%s is now %s" % (u["username"], action + "d"))
        return 0
    if action == "remove":
        site_auth.delete_user(u["id"])
        # Said out loud because it is the opposite of what a delete usually
        # means, and the files are the expensive part.
        print("removed %s — their downloads are still on disk" % u["username"])
        return 0
    return 2                        # unreachable: the verb was checked above


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("users", "invite", "site"):
        utils.setup_logging()
        return _admin(argv)

    ap = argparse.ArgumentParser(description="HyperFetch Server")
    ap.add_argument("--check", action="store_true",
                    help="start everything, report, and exit 0")
    ap.add_argument("--port", type=int, default=None,
                    help="port for the users site (default 5001)")
    args = ap.parse_args(argv)

    utils.setup_logging()
    settings, save_dir = _load_settings()
    queue, save_dir = build(save_dir, settings)

    import api_server
    import site_auth
    import site_server

    site_port = args.port or site_server.PORT

    if args.check:
        # Enough to prove the pieces exist and agree, without binding a port or
        # touching the network.
        app = site_server.create_site_app(queue, save_dir)
        routes = {str(r) for r in app.url_map.iter_rules()}
        for gone in ("/download", "/pair", "/ping"):
            assert gone not in routes, "%s is registered on the users site" % gone
        print("save dir       : %s" % save_dir)
        print("downloads      : %d restored" % len(queue.tasks))
        print("users site     : %d routes, port %d" % (len(routes), site_port))
        print("site enabled   : %s" % site_auth.is_enabled())
        print("control port   : %d" % api_server.PORT)
        print("server check OK v%s" % utils.APP_VERSION)
        return 0

    _state_saver(queue)

    # The control server keeps the browser extension working on this machine.
    # It stays loopback-or-LAN exactly as it does on the desktop; nothing here
    # widens it.
    threading.Thread(
        target=lambda: api_server.run_server(
            queue, save_dir, api_server.PORT,
            pending=None, token=utils.get_or_create_token()),
        name="hyperfetch-control", daemon=True).start()

    stop = threading.Event()

    def _bye(_sig, _frm):
        log.info("stopping")
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _bye)
        except (ValueError, OSError):
            pass                        # not the main thread, or not supported

    log.info("HyperFetch Server %s — users site on 127.0.0.1:%d, downloads in %s",
             utils.APP_VERSION, site_port, save_dir)
    if not site_auth.is_enabled():
        log.warning("the users site is switched off; every request will get a "
                    "maintenance page until it is enabled")

    site_thread = threading.Thread(
        target=lambda: site_server.run_site_server(queue, save_dir, site_port),
        name="hyperfetch-site", daemon=True)
    site_thread.start()

    try:
        while not stop.is_set():
            stop.wait(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        # A service is usually killed rather than closed, so the last thing it
        # does is write the list it would otherwise lose.
        _save_state(queue)
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
