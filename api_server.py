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
import logging

from datetime import timedelta
from flask import Flask, request, jsonify, session
from flask_cors import CORS

import task as T
import utils
import web_auth

PORT = 5000
log = logging.getLogger("hyperfetch.server")

# Extension ids trusted to auto-pair (read the token via /pair). Only the
# published listings belong here — the Chrome Web Store id (and the Edge Add-ons
# id once published). Unpacked/dev loads get a random id and fall back to the
# manual copy-paste in the popup.
TRUSTED_EXT_IDS = {"finojjembpabfbincabngboedegokdlm"}      # Chrome Web Store


LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


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
        if not (session.get("web_ok") and web_auth.has_password()):
            return False
        return session.get("pw") == web_auth.password_stamp()

    def require_web_auth():
        """None when the caller may proceed, else a response to return.

        Reachable from the LAN, so this is the only thing standing between the
        network and the download queue.
        """
        if not web_auth.has_password():
            return jsonify({"status": "error", "code": "no-password",
                            "message": "Set a web password in Settings first"}), 403
        if not web_authed():
            return jsonify({"status": "error", "code": "auth",
                            "message": "not signed in"}), 401
        return None

    @app.route("/api/session", methods=["GET"])
    def api_session():
        """What the page needs before rendering: is a password set, am I in?"""
        return jsonify({"hasPassword": web_auth.has_password(),
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
        if not web_auth.has_password():
            return jsonify({"status": "error", "code": "no-password",
                            "message": "Set a web password in Settings first"}), 403
        if not web_auth.verify_password(data.get("password") or ""):
            throttle.record_failure(addr)
            log.warning("failed web login from %s", addr)
            return jsonify({"status": "error", "code": "bad-password",
                            "message": "Wrong password"}), 401
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
