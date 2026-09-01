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
from flask import (Flask, request, jsonify, session, redirect,
                   send_from_directory, send_file)
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


# A torrent can hold thousands of files. The listing is for a person choosing
# one on a phone, so it stops rather than building a list nobody will scroll.
MAX_LISTED_FILES = 500


def _resolve_root(t):
    """Where this download's files actually are.

    Normally ``save_path``. But a magnet has no name until its metadata
    arrives, so a torrent task is created with a placeholder like
    ``magnet_.bin`` and only corrected once it finishes — and
    ``torrent._resolve_save_path`` leaves it alone when it cannot work out the
    top-level entry. The record then points at a file that never existed while
    the download sits on disk under its real name, and the page says the file
    is gone.

    So when save_path is missing, look for the task's own ``filename`` beside
    it. Both halves come from the task, never from a request, and the result
    still has to sit inside the folder save_path named — this recovers a broken
    record without becoming a search for whatever looks close.
    """
    root = getattr(t, "save_path", "") or ""
    if not root or os.path.exists(root):
        return root

    parent = os.path.dirname(root)
    name = (getattr(t, "filename", "") or "").strip()
    if not parent or not name or not os.path.isdir(parent):
        return root                      # nothing better to offer

    candidate = os.path.join(parent, name)
    if not os.path.exists(candidate):
        return root
    try:
        real_parent = os.path.realpath(parent)
        if not os.path.realpath(candidate).startswith(real_parent + os.sep):
            return root                  # a filename that climbed out
    except OSError:
        return root
    log.info("save_path for %r does not exist; using %s", name, candidate)
    return candidate


def servable_files(t):
    """The files a finished download is allowed to hand out, in order.

    The root comes from the TASK (``save_path``), never from the request, so
    there is no caller-supplied path to traverse with. Callers pick a file by
    index into this list; a directory is walked once and every result is
    re-checked to be inside the root, so a symlink planted in a torrent cannot
    point the server at something else on disk.
    """
    root = _resolve_root(t)
    if not root:
        return []
    try:
        real_root = os.path.realpath(root)
    except OSError:
        return []

    def inside(p):
        try:
            rp = os.path.realpath(p)
        except OSError:
            return False
        return rp == real_root or rp.startswith(real_root + os.sep)

    if os.path.isfile(real_root):
        return [{"path": real_root, "name": os.path.basename(real_root),
                 "rel": os.path.basename(real_root),
                 "size": os.path.getsize(real_root)}]

    if not os.path.isdir(real_root):
        return []

    out = []
    for base, dirs, names in os.walk(real_root):
        dirs.sort()
        for n in sorted(names):
            if n.endswith((".hfdownload", ".aria2")):
                continue                     # still being written
            full = os.path.join(base, n)
            if not inside(full):
                continue                     # symlink out of the torrent
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            out.append({"path": full, "name": n,
                        "rel": os.path.relpath(full, real_root).replace("\\", "/"),
                        "size": size})
            if len(out) >= MAX_LISTED_FILES:
                return out
    return out


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
            "url": t.url or "",
            "speedLimit": int(getattr(t, "speed_limit", 0) or 0),
            "totalBytes": total,
            "doneBytes": done,
            "percent": round(done * 100.0 / total, 2) if total else 0.0,
            "added": float(getattr(t, "added", 0) or 0),
            "error": getattr(t, "error", "") or "",
            "isTorrent": _torrent.is_torrent_task(t.url, t.filename),
            # Same buckets the desktop sidebar groups by, decided in one place
            # so the two never disagree about what counts as a Video.
            "category": utils.category_for(t.filename or ""),
            "peers": int(getattr(t, "tor_conns", 0) or 0),
            "seeds": int(getattr(t, "tor_seeds", 0) or 0),
            "upSpeed": int(getattr(t, "tor_upload", 0) or 0),
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
        cats = {}
        down = up_speed = up_total = 0
        for t in list(queue.tasks):
            counts[t.status] = counts.get(t.status, 0) + 1
            down += int(getattr(t, "downloaded", 0) or 0)
            # Upload is torrent-only, and aria2 is the only thing that knows it.
            up_speed += int(getattr(t, "tor_upload", 0) or 0)
            up_total += int(getattr(t, "tor_uploaded", 0) or 0)
            try:
                cats[utils.category_for(t.filename)] = \
                    cats.get(utils.category_for(t.filename), 0) + 1
            except Exception:
                pass
        try:
            import history
            hist = history.stats()
        except Exception:
            hist = {}
        return jsonify({
            "byStatus": counts,
            "byCategory": cats,
            "activeBytes": down,
            "history": hist,
            "upSpeed": up_speed,
            # Sent by the torrents CURRENTLY in the list. Not a lifetime figure:
            # nothing persists an upload total once a torrent is removed, so
            # calling this "total uploaded" would overstate what is known.
            "uploadedNow": up_total,
            # Lifetime bytes of COMPLETED downloads, from history.json.
            "downloadedTotal": int((hist or {}).get("total_bytes") or 0),
            "version": utils.APP_VERSION,
        })

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

    @app.route("/api/downloads/<task_id>/force", methods=["POST"])
    def api_force(task_id):
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        queue.force_start(t)
        return jsonify({"status": "ok"})

    @app.route("/api/downloads/<task_id>/limit", methods=["POST"])
    def api_limit(task_id):
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        try:
            bps = max(0, int((request.get_json(silent=True) or {}).get("bps", 0)))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "bad limit"}), 400
        t.speed_limit = bps
        try:
            t._limiter.set_limit(bps)
        except Exception:
            pass                     # a task not yet running has no limiter
        return jsonify({"status": "ok", "bps": bps})

    @app.route("/api/downloads/<task_id>/move", methods=["POST"])
    def api_move(task_id):
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        where = (request.get_json(silent=True) or {}).get("where", "")
        if where not in ("top", "up", "down", "bottom"):
            return jsonify({"status": "error", "message": "bad direction"}), 400
        queue.move(t, where)
        return jsonify({"status": "ok"})

    @app.route("/api/downloads/<task_id>/rename", methods=["POST"])
    def api_rename(task_id):
        """Rename the display name, and the file too once it is finished.

        Same rule as the desktop: an in-flight task only retargets save_path,
        because its bytes live in an id-keyed .hfdownload temp and finalize
        simply lands on the new name.
        """
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        raw = ((request.get_json(silent=True) or {}).get("name") or "").strip()
        # Ask before sanitising, not after: safe_filename never returns empty,
        # it falls back to "download". Checking its output would let a name of
        # spaces through and quietly rename the file to that.
        if not raw:
            return jsonify({"status": "error",
                            "message": "That is not a usable file name."}), 400
        new = utils.safe_filename(raw)
        if new == t.filename:
            return jsonify({"status": "ok", "name": new})

        d = os.path.dirname(t.save_path) or "."
        if t.status == T.COMPLETED and os.path.exists(t.save_path):
            dest = utils.unique_path(d, new)
            try:
                os.rename(t.save_path, dest)
            except OSError as e:
                return jsonify({"status": "error",
                                "message": "Could not rename: %s" % e}), 409
            t.save_path = dest
        else:
            t.save_path = utils.unique_path(d, new)
        t.filename = os.path.basename(t.save_path)
        t.log_event("Renamed")
        return jsonify({"status": "ok", "name": t.filename})

    # ------------------------------------------- web UI: take the file away
    # The point of the whole web client: an iPhone has no torrent client, so
    # the PC fetches it and the phone collects the finished file from here.
    @app.route("/api/downloads/<task_id>/files", methods=["GET"])
    def api_files(task_id):
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        files = servable_files(t)
        return jsonify({
            "ready": t.status == T.COMPLETED,
            "truncated": len(files) >= MAX_LISTED_FILES,
            # Where it looked, when it found nothing. This is the owner's own
            # machine and their own control page, so the path is theirs to see —
            # and "the file is no longer here" without it is undiagnosable. A
            # magnet's save_path is a placeholder until the download finishes
            # and _resolve_save_path corrects it, so the usual answer is that
            # the record points somewhere the file never was.
            "lookedIn": (getattr(t, "save_path", "") or "") if not files else "",
            # No absolute paths: the phone only ever needs a name and an index,
            # and the layout of this PC's disk is not the browser's business.
            "files": [{"index": i, "name": f["name"], "path": f["rel"],
                       "size": f["size"]}
                      for i, f in enumerate(files)],
        })

    @app.route("/api/downloads/<task_id>/file", methods=["GET"])
    @app.route("/api/downloads/<task_id>/file/<int:index>", methods=["GET"])
    def api_file(task_id, index=0):
        deny = require_web_auth()
        if deny:
            return deny
        t, missing = _task_or_404(task_id)
        if missing:
            return missing
        if t.status != T.COMPLETED:
            # Half a file looks like a whole one once it is on the phone.
            return jsonify({"status": "error", "code": "not-ready",
                            "message": "This download has not finished yet"}), 409

        files = servable_files(t)
        if not files:
            where = getattr(t, "save_path", "") or "(nowhere recorded)"
            return jsonify({"status": "error", "code": "gone",
                            "message": "Nothing found at %s" % where,
                            "lookedIn": where}), 404
        if index < 0 or index >= len(files):
            return jsonify({"status": "error", "message": "no such file"}), 404

        f = files[index]
        # conditional=True is what makes this usable on a phone: Werkzeug then
        # answers Range requests, so Safari can stream a video without pulling
        # the whole thing, and a dropped 4 GB download resumes instead of
        # starting over.
        inline = request.args.get("inline") == "1"
        return send_file(f["path"], as_attachment=not inline,
                         download_name=f["name"], conditional=True)

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

    @app.route("/ui/logo.png", methods=["GET"])
    def ui_logo():
        """The same bundled icon the desktop window and taskbar use.

        Served from assets/ rather than copied into web/, so the page and the
        app can never drift onto two different logos. One explicit route, not
        a second static folder — assets/ holds more than the page should be
        able to ask for.
        """
        base = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(__file__)))
        return send_from_directory(os.path.join(base, "assets"), "icon.png")

    return app


def bind_host():
    """127.0.0.1 unless the user has explicitly opened this to their network.

    Evaluated once per start, so turning LAN access on or off takes effect on
    the next launch rather than silently changing what a running process is
    already listening on.
    """
    try:
        if web_auth.lan_allowed():
            return "0.0.0.0"
    except Exception:
        log.exception("could not read the LAN setting — staying on loopback")
    return "127.0.0.1"


def run_server(queue, save_dir, port=PORT, pending=None, token=None):
    app = create_app(queue, save_dir, pending, token=token)
    host = bind_host()
    if host != "127.0.0.1":
        # Worth a line in the log: it is the one moment this app stops being
        # local-only, and the extension routes stay loopback-only regardless.
        log.warning("web client is reachable from your network on port %s", port)
    # threaded so multiple browser hits don't block; reloader off (background thread)
    app.run(host=host, port=port, threaded=True,
            use_reloader=False, debug=False)


if __name__ == "__main__":
    from queue_manager import QueueManager
    q = QueueManager()
    run_server(q, utils.default_download_dir(), token=utils.get_or_create_token())
