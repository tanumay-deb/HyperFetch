"""Flask endpoint that receives URLs from the browser extension.

Security model (localhost-only desktop app):
- Bound to 127.0.0.1 — not reachable off-machine.
- CORS allows only ``chrome-extension://`` / ``moz-extension://`` origins, so a
  website's JS cannot drive downloads (its cross-origin JSON POST fails preflight).
- ``/download`` additionally requires the pairing token (``X-HyperFetch-Token`` header
  or ``token`` body field) so other local processes / extensions can't queue
  downloads. The user copies the token from the app into the extension once.

Two modes:
- GUI mode (``pending`` given): requests land in a deque; the GUI pops them and
  shows the file-info dialog before anything is queued.
- Headless mode (``python api_server.py``): tasks are queued immediately.
"""
import os
import sys
import logging

from datetime import timedelta
from flask import Flask, request, jsonify, session, redirect, send_from_directory
from flask_cors import CORS

import task as T
import utils
import torrent as _torrent
import web_auth

PORT = 5000
log = logging.getLogger("hyperfetch.server")

# Extension ids trusted to auto-pair (read the token via /pair). Only the
# published listings belong here — the Chrome Web Store id (and the Edge Add-ons
# id once published). Unpacked/dev loads get a random id and fall back to the
# manual copy-paste in the popup.
TRUSTED_EXT_IDS = {"finojjembpabfbincabngboedegokdlm"}      # Chrome Web Store


LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


# Only these are served from /ui. An allow-list rather than "whatever is in the
# folder", so a stray file dropped in there never becomes a public URL.
ALLOWED_UI_FILES = {"index.html", "style.css", "app.js"}


def web_dir():
    """The web UI folder, in a dev checkout and in the frozen build alike."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "web")


def is_loopback(remote_addr):
    """True when the request came from this machine.

    Read from the socket, never from a header: X-Forwarded-For and friends are
    attacker-controlled and would defeat the whole point.
    """
    return (remote_addr or "") in LOOPBACK_ADDRS


def create_app(queue, save_dir, pending=None, token=None):
    app = Flask(__name__)
    # Only browser-extension origins may call cross-origin. Websites use http(s)
    # origins and are rejected at preflight. /pair is deliberately NOT covered by
    # this global rule — it sets its own Access-Control-Allow-Origin locked to the
    # trusted extension id(s) so only the real extension can read the token.
    _ext = [r"chrome-extension://*", r"moz-extension://*"]
    _hdr = ["Content-Type", "X-HyperFetch-Token"]
    # allow_private_network: Chrome sends a Private Network Access preflight for
    # every extension -> 127.0.0.1 request and BLOCKS the call unless the reply
    # carries `Access-Control-Allow-Private-Network: true`. flask-cors >= 5
    # defaults this to False (it answered an explicit "false"), which silently
    # broke the whole browser bridge: the popup hung on "checking…" and
    # auto-pairing could never fetch a token. This does not widen who may call
    # us — the origin allow-list above still rejects websites, and the token
    # gate is untouched; it only lets the already-allowed extension origins
    # complete their preflight.
    _pna = {"allow_private_network": True}
    CORS(app, resources={
        r"/ping":     {"origins": _ext, **_pna},
        r"/probe":    {"origins": _ext, "allow_headers": _hdr, **_pna},
        r"/download": {"origins": _ext, "allow_headers": _hdr, **_pna},
    })
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    app.config["HYPERFETCH_TOKEN"] = token

    @app.route("/pair", methods=["GET", "OPTIONS"])
    def pair():
        """Hand the pairing token to the official extension so it can auto-pair —
        no copy-paste. CORS is locked to the trusted extension id(s): other
        extensions get a different Origin (403 + no CORS header) and website JS is
        blocked by the browser. A local process could read the token file anyway,
        so serving it here to localhost adds no new exposure."""
        # Loopback only. The bind address used to guarantee this; once the
        # server can listen on the LAN for the web UI, it has to be checked.
        if not is_loopback(request.remote_addr):
            return jsonify({"status": "error",
                            "message": "local requests only"}), 403
        origin = request.headers.get("Origin", "")
        allowed = any(origin == scheme + eid
                      for scheme in ("chrome-extension://", "moz-extension://")
                      for eid in TRUSTED_EXT_IDS)
        if not allowed:
            return ("", 403)
        if request.method == "OPTIONS":
            resp = app.make_default_options_response()
        else:
            resp = jsonify({"token": app.config.get("HYPERFETCH_TOKEN") or ""})
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-HyperFetch-Token"
        # /pair sets its own CORS headers (it is deliberately outside the global
        # rule above), so it needs the Private Network Access opt-in too —
        # without it Chrome blocks the preflight and auto-pairing never runs.
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        return resp

    def _authorized(data):
        expected = app.config.get("HYPERFETCH_TOKEN")
        if not expected:
            return True  # token disabled (e.g. headless/tests)
        presented = request.headers.get("X-HyperFetch-Token") or (data or {}).get("token")
        # constant-time compare
        import hmac
        return bool(presented) and hmac.compare_digest(str(presented), str(expected))

    @app.route("/ping", methods=["GET"])
    def ping():
        # open (no token) so the popup can show connection status; reveals nothing
        # badgeCorner: the extension mirrors this app setting into chrome.storage
        # when its popup pings us (position of the on-page download button —
        # harmless to expose on the open endpoint)
        return jsonify({"status": "ok",
                        "needsToken": bool(app.config.get("HYPERFETCH_TOKEN")),
                        "badgeCorner": utils.BADGE_CORNER})

    @app.route("/probe", methods=["POST"])
    def probe():
        """Parse an HLS master's quality variants for the extension's picker.
        The app has the original capture's cookies/referer/UA and no CORS, so
        it reads referer/auth-gated manifests the extension's own fetch can't."""
        # Loopback only. The bind address used to guarantee this; once the
        # server can listen on the LAN for the web UI, it has to be checked.
        if not is_loopback(request.remote_addr):
            return jsonify({"status": "error",
                            "message": "local requests only"}), 403
        data = request.get_json(silent=True) or {}
        if not _authorized(data):
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        url = (data.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return jsonify({"status": "error", "message": "invalid url"}), 400
        headers = {}
        if data.get("cookies"):
            headers["Cookie"] = data["cookies"]
        if data.get("userAgent"):
            headers["User-Agent"] = data["userAgent"]
        if data.get("referrer"):
            headers["Referer"] = data["referrer"]
        import hls
        try:
            variants = hls.probe_variants(url, headers)
        except Exception:
            variants = []
        return jsonify({"variants": variants})

    @app.route("/download", methods=["POST"])
    def download():
        # Loopback only. The bind address used to guarantee this; once the
        # server can listen on the LAN for the web UI, it has to be checked.
        if not is_loopback(request.remote_addr):
            return jsonify({"status": "error",
                            "message": "local requests only"}), 403
        data = request.get_json(silent=True) or {}
        if not _authorized(data):
            return jsonify({"status": "error", "message": "unauthorized"}), 401

        url = (data.get("url") or "").strip()
        # real web downloads + magnet links; block file://, chrome://, javascript:
        if not url.lower().startswith(("http://", "https://", "magnet:")):
            return jsonify({"status": "error", "message": "invalid url"}), 400

        suggested = data.get("filename") or ""

        # Auto-capture allowlist: the extension's browser-download capture sends
        # auto=true. Reject (so the browser keeps the file) when its extension is
        # not in the Settings allowlist. Manual menu/badge/magnet captures have no
        # auto flag and are never filtered. magnet: links carry no extension and
        # are always allowed.
        if data.get("auto") and url.lower().startswith(("http://", "https://")) \
                and not utils.capture_allowed(suggested or url):
            return jsonify({"status": "ignored", "reason": "extension not in capture list"})

        log.info("server received download: %s", url)

        # browser context for auth-gated hosts (Google Drive etc.)
        headers = {}
        if data.get("cookies"):
            headers["Cookie"] = data["cookies"]
        if data.get("userAgent"):
            headers["User-Agent"] = data["userAgent"]
        if data.get("referrer"):
            headers["Referer"] = data["referrer"]

        if pending is not None:
            # GUI decides via the file-info dialog
            pending.append({"url": url, "filename": suggested,
                            "headers": headers})
            return jsonify({"status": "queued"})

        filename = utils.filename_from_url(url, suggested)
        cat_dir = utils.get_category_dir(save_dir, filename)
        save_path = utils.unique_path(cat_dir, filename)
        task = T.DownloadTask(url, save_path, filename=filename, headers=headers)
        queue.add_task(task)
        return jsonify({"status": "queued", "id": task.id, "filename": filename})

    @app.route("/focus", methods=["POST"])
    def focus():
        """Single-instance handoff: a second `main.py` launch (no CLI target)
        POSTs here so the already-running GUI pops up instead of a duplicate
        window + tray icon. Token-gated + localhost; not in the CORS allow-list,
        so a browser can't reach it. Headless mode answers "no-gui" — the new
        launch then proceeds to open a real window."""
        # Loopback only. The bind address used to guarantee this; once the
        # server can listen on the LAN for the web UI, it has to be checked.
        if not is_loopback(request.remote_addr):
            return jsonify({"status": "error",
                            "message": "local requests only"}), 403
        data = request.get_json(silent=True) or {}
        if not _authorized(data):
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        if pending is None:
            return jsonify({"status": "no-gui"})
        pending.append({"focus": True})
        return jsonify({"status": "focused"})

    @app.route("/open", methods=["POST"])
    def open_target():
        """Single-instance handoff: `main.py`, launched by Windows to open a
        .torrent file or magnet: link, POSTs it here so the already-running app
        adds it (instead of spawning a second window). Token-gated + localhost;
        not in the CORS allow-list, so a browser can't reach it."""
        # Loopback only. The bind address used to guarantee this; once the
        # server can listen on the LAN for the web UI, it has to be checked.
        if not is_loopback(request.remote_addr):
            return jsonify({"status": "error",
                            "message": "local requests only"}), 403
        data = request.get_json(silent=True) or {}
        if not _authorized(data):
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        target = (data.get("target") or "").strip()
        if not (target.lower().startswith("magnet:") or target.lower().endswith(".torrent")):
            return jsonify({"status": "error", "message": "not a torrent/magnet"}), 400
        if pending is not None:
            pending.append({"url": target, "filename": "", "headers": {}})
            return jsonify({"status": "queued"})
        fn = utils.filename_from_url(target) or "torrent"
        task = T.DownloadTask(target, utils.unique_path(save_dir, fn), filename=fn)
        queue.add_task(task)
        return jsonify({"status": "queued", "id": task.id})


    # ---------------------------------------------------------------- web UI
    # Its own password and signed session, never the pairing token: /pair hands
    # that token to any local caller, it would sit in the page source, and it
    # cannot be rotated without re-pairing the extension.
    app.secret_key = web_auth.secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,     # script in the page cannot read it
        SESSION_COOKIE_SAMESITE="Lax",    # another site cannot POST as the user
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    throttle = web_auth.LoginThrottle()

    def web_authed():
        """Signed in AND still on the password used to sign in.

        This stamp check is what makes a password change sign other devices
        out straight away: app.secret_key is read once at construction, so
        rotating it alone leaves a live cookie valid until the next restart.
        """
        if not (session.get("web_ok") and web_auth.is_enabled()
                and web_auth.has_password()):
            return False
        return session.get("pw") == web_auth.password_stamp()

    def require_web_auth():
        """None when the caller may proceed, else a response to return.

        Reachable from the LAN, so this is the only thing standing between the
        network and the download queue.
        """
        if not web_auth.is_enabled():
            return jsonify({"status": "error", "code": "disabled",
                            "message": "The web client is turned off"}), 403
        if not web_auth.has_password():
            return jsonify({"status": "error", "code": "no-password",
                            "message": "Set a web password in Settings first"}), 403
        if not web_authed():
            return jsonify({"status": "error", "code": "auth",
                            "message": "not signed in"}), 401
        return None

    @app.route("/api/session", methods=["GET"])
    def api_session():
        """What the page needs before rendering: is it on, is it set up, am I in?"""
        return jsonify({"enabled": web_auth.is_enabled(),
                        "hasPassword": web_auth.has_password(),
                        "authed": web_authed()})

    @app.route("/api/login", methods=["POST"])
    def api_login():
        data = request.get_json(silent=True) or {}
        addr = request.remote_addr or "?"
        wait = throttle.locked_for(addr)
        if wait > 0:
            return jsonify({"status": "error", "code": "locked",
                            "message": "Too many attempts. Try again in "
                                       f"{int(wait) + 1}s.",
                            "retryAfter": int(wait) + 1}), 429
        if not web_auth.is_enabled():
            return jsonify({"status": "error", "code": "disabled",
                            "message": "The web client is turned off"}), 403
        if not web_auth.has_password():
            return jsonify({"status": "error", "code": "no-password",
                            "message": "Set a web password in Settings first"}), 403
        if not web_auth.verify(data.get("username") or "",
                               data.get("password") or ""):
            throttle.record_failure(addr)
            log.warning("failed web login from %s", addr)
            # One message for both halves on purpose: saying WHICH was wrong
            # tells an attacker when they have found a real username.
            return jsonify({"status": "error", "code": "bad-login",
                            "message": "Wrong username or password"}), 401
        throttle.record_success(addr)
        session.clear()                   # new session id on login
        session["web_ok"] = True
        session["pw"] = web_auth.password_stamp()
        session.permanent = True
        log.info("web login from %s", addr)
        return jsonify({"status": "ok"})

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"status": "ok"})


    # ------------------------------------------------------- web UI: data
    def task_json(t):
        """One download, as the web UI sees it.

        An explicit allow-list, never the task's own dict: tasks carry cookies
        and auth headers in memory, and a "serialise everything" helper would
        put them on the network the first time a field was added.

        No speed or ETA here — the page derives those from successive samples of
        `downloaded`, rather than duplicating the desktop's speed tracker.
        """
        total = int(getattr(t, "total_size", 0) or 0)
        done = int(getattr(t, "downloaded", 0) or 0)
        return {
            "id": t.id,
            "name": t.filename or "",
            "status": t.status,
            "queue": getattr(t, "queue_name", "") or "",
            "totalBytes": total,
            "doneBytes": done,
            "percent": round(done * 100.0 / total, 2) if total else 0.0,
            "added": float(getattr(t, "added", 0) or 0),
            "error": getattr(t, "error", "") or "",
            "isTorrent": _torrent.is_torrent_task(t.url, t.filename),
            "peers": int(getattr(t, "tor_conns", 0) or 0),
            "seeds": int(getattr(t, "tor_seeds", 0) or 0),
            "seeding": bool(getattr(t, "seeding", False)),
            "verifying": bool(getattr(t, "verifying", False)),
            "verifiedPercent": int(getattr(t, "verified_pct", 0) or 0),
            "fetchingMeta": bool(getattr(t, "meta_fetching", False)),
            "metaFailed": bool(getattr(t, "meta_failed", False)),
        }

    @app.route("/api/downloads", methods=["GET"])
    def api_downloads():
        deny = require_web_auth()
        if deny:
            return deny
        return jsonify({"downloads": [task_json(t) for t in list(queue.tasks)]})

    @app.route("/api/stats", methods=["GET"])
    def api_stats():
        deny = require_web_auth()
        if deny:
            return deny
        counts = {}
        down = up = 0
        for t in list(queue.tasks):
            counts[t.status] = counts.get(t.status, 0) + 1
            down += int(getattr(t, "downloaded", 0) or 0)
        try:
            import history
            hist = history.stats()
        except Exception:
            hist = {}
        return jsonify({"byStatus": counts,
                        "activeBytes": down,
                        "history": hist})

    # ---------------------------------------------------- web UI: control
    @app.route("/api/downloads", methods=["POST"])
    def api_add():
        deny = require_web_auth()
        if deny:
            return deny
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        # same allow-list as the extension endpoint: no file://, no chrome://,
        # no javascript:. This one is reachable from the network, so it matters
        # more here, not less.
        if not url.lower().startswith(("http://", "https://", "magnet:")):
            return jsonify({"status": "error", "message": "invalid url"}), 400
        if pending is not None:
            pending.append({"url": url, "filename": "", "headers": {}})
            return jsonify({"status": "queued"})
        fn = utils.filename_from_url(url) or "download"
        path = utils.unique_path(utils.get_category_dir(save_dir, fn), fn)
        task = T.DownloadTask(url, path, filename=fn)
        queue.add_task(task)
        return jsonify({"status": "queued", "id": task.id})

    def _task_or_404(task_id):
        t = queue.get_task(task_id)
        return t, (None if t else
                   (jsonify({"status": "error", "message": "no such download"}), 404))

    @app.route("/api/downloads/<task_id>/pause", methods=["POST"])
    def api_pause(task_id):
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        queue.pause_task(t)
        return jsonify({"status": "ok"})

    @app.route("/api/downloads/<task_id>/resume", methods=["POST"])
    def api_resume(task_id):
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        queue.resume_task(t)
        return jsonify({"status": "ok"})

    @app.route("/api/downloads/<task_id>", methods=["DELETE"])
    def api_remove(task_id):
        """Remove from the list. Deliberately does NOT delete from disk.

        Erasing a user's files from a network-reachable endpoint is a different
        risk class from pausing one, and deserves its own design rather than a
        flag bolted onto this. Engine leftovers (the .aria2 control file and our
        saved metadata) are cleaned up, since they are ours, not the user's.
        """
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        queue.remove_task(t)
        try:
            if _torrent.is_torrent_task(t.url, t.filename):
                _torrent.cleanup_artifacts(t)
        except Exception:
            pass
        return jsonify({"status": "ok"})


    # ------------------------------------------------------- web UI: files
    # Deliberately NOT loopback-gated: this is the page itself, and it is meant
    # to open on a phone. It ships no data — everything it shows comes from the
    # session-gated /api routes above, so an unauthenticated visitor gets a
    # login form and nothing else.
    @app.route("/", methods=["GET"])
    def ui_root():
        return redirect("/ui/")

    @app.route("/ui/", methods=["GET"])
    def ui_index():
        return send_from_directory(web_dir(), "index.html")

    @app.route("/ui/<path:name>", methods=["GET"])
    def ui_asset(name):
        """send_from_directory refuses to escape the folder, so a crafted name
        cannot walk up into the filesystem."""
        if name not in ALLOWED_UI_FILES:
            return ("", 404)
        return send_from_directory(web_dir(), name)

    return app


def run_server(queue, save_dir, port=PORT, pending=None, token=None):
    app = create_app(queue, save_dir, pending, token=token)
    # threaded so multiple browser hits don't block; reloader off (background thread)
    app.run(host="127.0.0.1", port=port, threaded=True,
            use_reloader=False, debug=False)


if __name__ == "__main__":
    from queue_manager import QueueManager
    q = QueueManager()
    run_server(q, utils.default_download_dir(), token=utils.get_or_create_token())
